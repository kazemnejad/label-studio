import os
import datetime
import logging.config
import traceback as tb
import io
from flask import request, jsonify, make_response
import json  # it MUST be included after flask!
import db

from pythonjsonlogger import jsonlogger
from lxml import etree
from xml.etree import ElementTree


# this must be before logger setup
class CustomJsonFormatter(jsonlogger.JsonFormatter):
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        if not log_record.get('timestamp'):
            # this doesn't use record.created, so it is slightly off
            now = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ')
            log_record['timestamp'] = now
        if log_record.get('level'):
            log_record['level'] = log_record['level'].upper()
        else:
            log_record['level'] = record.levelname


# read logger config
log_config = json.load(open('logger.json'))
logfile = log_config['handlers']['file']['filename']
# create log file
os.mkdir(os.path.dirname(logfile)) if not os.path.exists(os.path.dirname(logfile)) else ()
open(logfile, 'w') if not os.path.exists(logfile) else ()
# set logger config
logging.config.dictConfig(log_config)
log = logging.getLogger('service')


# make an answer to client
def answer(status=0, msg='', result=None):
    if status == 0 and not msg and result is None:
        status = -1000
        msg = "nothing happened"

    if status == 200 and not msg:
        msg = 'ok'

    a = {"status": status, "msg": msg}
    a.update({'request': request.args})

    if result is not None:
        a.update({"result": result})

    return make_response(jsonify(a), status)


# make an answer as exception
class AnswerException(Exception):
    def __init__(self, status, msg='', result=None):
        self.status, self.msg, self.result = status, msg, result
        self.answer = answer(status, msg, result)
        Exception.__init__(self, self.answer)


# standard exception treatment for any api function
def exception_treatment(f):
    def exception_f(*args, **kwargs):
        try:
            return f(*args, **kwargs)

        except AnswerException as e:
            traceback = tb.format_exc()
            log.critical('\n\n--------------\n' + traceback + '--------------\n')

            if 'traceback' not in e.result:
                e.result['traceback'] = traceback
            if hasattr(exception_f, 'request_id') and not e.result['request_id']:
                e.result['request_id'] = exception_f.request_id
            return answer(e.status, e.msg, e.result)

        except Exception as e:
            traceback = tb.format_exc()
            log.critical('\n\n--------------\n' + traceback + '--------------\n')

            body = {'traceback': traceback}
            if hasattr(exception_f, 'request_id'):
                body['request_id'] = exception_f.request_id
            return answer(501, str(e), body)

    exception_f.__name__ = f.__name__
    return exception_f


def config_line_stripped(xml_config):
    """ Remove comments, \n and \r from xml, flat xml to string

    :param xml_config: xml config string
    :return: xml config string
    """
    tree = etree.fromstring(xml_config)
    comments = tree.xpath('//comment()')

    for xml_config in comments:
        p = xml_config.getparent()
        p.remove(xml_config)
        xml_config = etree.tostring(tree, method='html').decode("utf-8")

    return xml_config.replace('\n', '').replace('\r', '')


def load_config():
    """ Combine args with json config

    :param config_path: json file path
    :return: config dict
    """
    def generator():
        import argparse

        parser = argparse.ArgumentParser(description='Label studio')
        parser.add_argument('-c', '--config', dest='config_path', default='config.json',
                            help='backend config')
        parser.add_argument('-l', '--label-config', dest='label_config', default='',
                            help='label config path')
        parser.add_argument('-i', '--input-path', dest='input_path', default='',
                            help='input path to task file or directory with tasks')
        parser.add_argument('-o', '--output-dir', dest='output_dir', default='',
                            help='output directory for completions')
        parser.add_argument('-p', '--port', dest='port', default=8200, type=int,
                            help='backend port')
        args,_ = parser.parse_known_args()

        config_path = args.config_path
        prev_config = None

        while True:
            c = json.load(open(config_path))
            c['port'] = args.port if args.port else c['port']
            c['label_config'] = args.label_config if args.label_config else c['label_config']
            c['input_path'] = args.input_path if args.input_path else c['input_path']
            c['output_dir'] = args.output_dir if args.output_dir else c['output_dir']

            # re-init db
            if prev_config != c:
                print('Config changes detected, reloading DB')
                db.re_init(c)

            yield c

    for new_config in generator():
        return new_config


class LabelConfigParser(object):

    def __init__(self, filepath):
        with io.open(filepath) as f:
            self._config = f.read()

    def get_value_for_name(self, name):
        tag_iter = ElementTree.fromstring(self._config).iter()
        return next((
            tag.attrib.get('value') for tag in tag_iter
            if tag.attrib.get('name') == name), None
        )

    def get_input_data_tags(self):
        tag_iter = ElementTree.fromstring(self._config).iter()
        return [
            tag for tag in tag_iter
            if tag.attrib.get('name') and tag.attrib.get('value', '').startswith('$')
        ]

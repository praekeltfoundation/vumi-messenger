import json
from datetime import datetime
from urllib import urlencode

import treq

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue
from twisted.internet.error import TimeoutError
from twisted.web import http
from twisted.web.client import HTTPConnectionPool

from vumi.config import ConfigText, ConfigDict, ConfigBool
from vumi.transports.httprpc import HttpRpcTransport


class MessengerTransportConfig(HttpRpcTransport.CONFIG_CLASS):

    access_token = ConfigText(
        "The access_token for the Messenger API",
        required=True)
    app_id = ConfigText(
        "The app id for the Messenger API",
        required=False)
    welcome_message = ConfigDict(
        ("The payload for setting up a welcome message. "
         "Requires an app_id to be set"),
        required=False)
    retrieve_profile = ConfigBool(
        "Set to true to include the user profile details in "
        "the helper_metadata", required=False, default=False)


class Page(object):
    """A thing that parses "Page" objects as received from Messenger"""

    def __init__(self, to_addr, from_addr,
                 mid, content, timestamp, in_reply_to=None):
        self.to_addr = to_addr
        self.from_addr = from_addr
        self.in_reply_to = in_reply_to
        self.mid = mid
        self.content = content
        self.timestamp = timestamp

    def __str__(self):
        ("<Page to_addr: %s, from_addr: %s, in_reply_to: %s, content: %s, "
         "mid: %s, timestamp: %s>") % (self.to_addr,
                                       self.from_addr,
                                       self.in_reply_to,
                                       self.content,
                                       self.mid,
                                       self.timestamp)

    @classmethod
    def from_fp(cls, fp):
        try:
            data = json.load(fp)
            [entry] = data['entry']
            [msg] = entry['messaging']
        except (ValueError, KeyError), e:
            raise UnsupportedMessage('Unable to parse message: %s' % (e,))

        if ('message' in msg) and ('text' in msg['message']):
            return cls(
                to_addr=msg['recipient']['id'],
                from_addr=msg['sender']['id'],
                mid=msg['message']['mid'],
                content=msg['message']['text'],
                timestamp=datetime.fromtimestamp(msg['timestamp'] / 1000))
        elif ('message' in msg) and ('attachments' in msg['message']):
            raise UnsupportedMessage(
                'Not supporting attachments yet: %s.' % (data,))
        elif 'optin' in msg:
            raise UnsupportedMessage(
                'Not supporting optin messages yet: %s.' % (data,))
        elif 'delivery' in msg:
            raise UnsupportedMessage(
                'Not supporting delivery messages yet: %s.' % (data,))
        elif 'postback' in msg:
            payload = json.loads(msg['postback']['payload'])
            return cls(
                to_addr=msg['recipient']['id'],
                from_addr=msg['sender']['id'],
                mid=None,
                content=payload['content'],
                in_reply_to=payload.get('in_reply_to'),
                timestamp=datetime.fromtimestamp(msg['timestamp'] / 1000)
            )
        else:
            raise UnsupportedMessage(
                'Not supporting %r.: %s.' % (data,))


class MessengerTransportException(Exception):
    pass


class UnsupportedMessage(MessengerTransportException):
    pass


class MessengerTransport(HttpRpcTransport):

    CONFIG_CLASS = MessengerTransportConfig
    transport_type = 'facebook'
    clock = reactor

    SEND_FAIL_TYPES = {
        100: 'no_matching_user_found',
        10: 'application_does_not_have_permissions',
        2: 'internal_server_error',
    }

    @inlineCallbacks
    def setup_transport(self):
        yield super(MessengerTransport, self).setup_transport()
        self.pool = HTTPConnectionPool(self.clock, persistent=False)
        if self.config.get('welcome_message'):
            if not self.config.get('app_id'):
                self.log.error('app_id is required for welcome_message')
                return
            try:
                data = yield self.setup_welcome_message(
                    self.config['welcome_message'],
                    self.config['app_id'])
                self.log.info('Set welcome message: %s' % (data,))
            except (MessengerTransport,), e:
                self.log.error('Failed to setup welcome message: %s' % (e,))

    @inlineCallbacks
    def teardown_transport(self):
        if hasattr(self, 'web_resource'):
            yield self.web_resource.loseConnection()
            if self.request_gc.running:
                self.request_gc.stop()

    @inlineCallbacks
    def setup_welcome_message(self, welcome_message_payload, app_id):
        response = yield self.request(
            'POST',
            "https://graph.facebook.com/v2.5/%s/thread_settings?%s" % (
                app_id,
                urlencode({
                    'access_token': self.config['access_token'],
                })),
            data=json.dumps({
                'setting_type': 'call_to_actions',
                'thread_state': 'new_thread',
                'call_to_actions': welcome_message_payload
            }),
            headers={
                'Content-Type': ['application/json']
            })

        data = yield response.json()
        if response.code == http.OK:
            returnValue(data)

        raise MessengerTransportException(data)

    def respond(self, message_id, code, body=None):
        if body is None:
            body = {}

        self.finish_request(message_id, json.dumps(body), code=code)

    def request(self, method, url, data, **kwargs):
        return treq.request(method=method, url=url, data=data, **kwargs)

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):

        if 'hub.challenge' in request.args:
            self.finish_request(message_id, request.args['hub.challenge'][0],
                                code=http.OK)
            return

        try:
            page = Page.from_fp(request.content)
            self.log.info("MessengerTransport inbound %r" % (page,))
        except (UnsupportedMessage,), e:
            self.respond(message_id, http.OK, {
                'warning': 'Accepted unsuppported message: %s' % (e,)
            })
            self.log.error(e)
            return

        if self.config.get('retrieve_profile'):
            helper_metadata = yield self.get_user_profile(page.from_addr)
        else:
            helper_metadata = {}

        yield self.publish_message(
            message_id=message_id,
            from_addr=page.from_addr,
            from_addr_type='facebook_messenger',
            to_addr=page.to_addr,
            in_reply_to=page.in_reply_to,
            content=page.content,
            provider='facebook',
            transport_type=self.transport_type,
            transport_metadata={
                'messenger': {
                    'mid': page.mid,
                }
            },
            helper_metadata={
                'messenger': helper_metadata
            })

        self.respond(message_id, http.OK, {})

        yield self.add_status(
            component='inbound',
            status='ok',
            type='request_success',
            message='Request successful')

    @inlineCallbacks
    def get_user_profile(self, user_id):
        response = yield self.request(
            method='GET',
            url='https://graph.facebook.com/v2.5/%s?%s' % (
                user_id, urlencode({
                    'fields': 'first_name,last_name,profile_pic',
                    'access_token': self.config['access_token'],
                })
            ),
            data='')
        data = yield response.json()
        if response.code == http.OK:
            returnValue(data)
        else:
            self.log.error('Unable to retrieve user profile: %s' % (data,))
            returnValue({})

    def construct_reply(self, message):
        helper_metadata = message.get('helper_metadata', {})
        messenger_metadata = helper_metadata.get('messenger', {})
        if messenger_metadata.get('template_type') == 'button':
            return self.construct_button_reply(message)

        if messenger_metadata.get('template_type') == 'generic':
            return self.construct_generic_reply(message)

        return self.construct_plain_reply(message)

    def construct_button_reply(self, message):
        button = message['helper_metadata']['messenger']
        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'button',
                        'text': button['text'],
                        'buttons': [
                            {
                                'type': 'postback',
                                'title': btn['title'],
                                'payload': json.dumps(btn['payload']),
                            } for btn in button['buttons']
                        ]
                    }
                }
            }
        }

    def construct_generic_reply(self, message):
        button = message['helper_metadata']['messenger']
        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'generic',
                        'elements': [{
                            'title': button['title'],
                            'subtitle': button['subtitle'],
                            'image_url': button.get('image_url'),
                            'buttons': [
                                {
                                    'type': 'postback',
                                    'title': btn['title'],
                                    'payload': json.dumps(btn['payload']),
                                } for btn in button['buttons']
                            ]
                        }]
                    }
                }
            }
        }

    def construct_plain_reply(self, message):
        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'text': message['content'],
            }
        }

    @inlineCallbacks
    def handle_outbound_message(self, message):
        self.log.info("MessengerTransport outbound %r" % (message,))
        reply = self.construct_reply(message)
        self.log.info("Reply: %s" % (reply,))
        try:
            resp = yield self.request(
                method='POST',
                url='%s?access_token=%s' % (self.config['outbound_url'],
                                            self.config['access_token']),
                data=json.dumps(reply),
                headers={
                    'Content-Type': 'application/json',
                },
                pool=self.pool)

            data = yield resp.json()
            self.log.info('API reply: %s' % (data,))

            if resp.code == http.OK:
                yield self.publish_ack(
                    user_message_id=message['message_id'],
                    sent_message_id=data['message_id'])
                yield self.add_status(
                    component='outbound',
                    status='ok',
                    type='request_success',
                    message='Request successful')
            else:
                yield self.nack(
                    message, data['error']['message'],
                    self.SEND_FAIL_TYPES.get(
                        data['error']['code'], 'request_fail_unknown'))
        except (TimeoutError,), e:
            yield self.nack(message, e, 'request_fail_unknown')

    @inlineCallbacks
    def nack(self, message, reason, status_type):
        yield self.publish_nack(
            user_message_id=message['message_id'],
            sent_message_id=message['message_id'],
            reason=reason)
        yield self.add_status(
            component='outbound',
            status='down',
            type=status_type,
            message=reason)

    # These seem to be standard things which allow a Junebug transport
    # to generate status reports for a channel

    def on_down_response_time(self, message_id, time):
        request = self.get_request(message_id)
        # We send different status events for error responses
        if request.code < 200 or request.code >= 300:
            return
        return self.add_status(
            component='response',
            status='down',
            type='very_slow_response',
            message='Very slow response',
            reasons=[
                'Response took longer than %fs' % (
                    self.response_time_down,)
            ],
            details={
                'response_time': time,
            })

    def on_degraded_response_time(self, message_id, time):
        request = self.get_request(message_id)
        # We send different status events for error responses
        if request.code < 200 or request.code >= 300:
            return
        return self.add_status(
            component='response',
            status='degraded',
            type='slow_response',
            message='Slow response',
            reasons=[
                'Response took longer than %fs' % (
                    self.response_time_degraded,)
            ],
            details={
                'response_time': time,
            })

    def on_good_response_time(self, message_id, time):
        request = self.get_request(message_id)
        # We send different status events for error responses
        if request.code < 200 or request.code >= 400:
            return
        return self.add_status(
            component='response',
            status='ok',
            type='response_sent',
            message='Response sent',
            details={
                'response_time': time,
            })

    def on_timeout(self, message_id, time):
        return self.add_status(
            component='response',
            status='down',
            type='timeout',
            message='Response timed out',
            reasons=[
                'Response took longer than %fs' % (
                    self.request_timeout,)
            ],
            details={
                'response_time': time,
            })

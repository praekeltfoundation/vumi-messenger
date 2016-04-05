import json
from datetime import datetime

import treq

from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks
from twisted.web import http
from twisted.web.client import HTTPConnectionPool

from vumi.config import ConfigText
from vumi.transports.httprpc import HttpRpcTransport


class MessengerTransportConfig(HttpRpcTransport.CONFIG_CLASS):

    access_token = ConfigText(
        "The access_token for the Messenger API",
        required=True)


class Page(object):
    """A thing that parses "Page" objects as received from Messenger"""

    def __init__(self, to_addr, from_addr, mid, content, timestamp):
        self.to_addr = to_addr
        self.from_addr = from_addr
        self.mid = mid
        self.content = content
        self.timestamp = timestamp

    def __str__(self):
        ("<Page to_addr: %s, from_addr: %s, content: %s, "
         "mid: %s, timestamp: %s>") % (self.to_addr,
                                       self.from_addr,
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
            raise UnsupportedMessage('Not supporting attachments yet.')
        elif 'optin' in msg:
            raise UnsupportedMessage('Not supporting optin messages yet.')
        elif 'delivery' in msg:
            raise UnsupportedMessage('Not supporting delivery messages yet.')
        else:
            raise UnsupportedMessage('Not supporting %r.' % (msg,))


class UnsupportedMessage(Exception):
    pass


class MessengerTransport(HttpRpcTransport):

    CONFIG_CLASS = MessengerTransportConfig
    transport_type = 'facebook'
    clock = reactor

    @inlineCallbacks
    def setup_transport(self):
        yield super(MessengerTransport, self).setup_transport()
        self.pool = HTTPConnectionPool(self.clock, persistent=False)

    def respond(self, message_id, code, body=None):
        if body is None:
            body = {}

        self.finish_request(message_id, json.dumps(body), code=code)

    def request(self, method, url, data, **kwargs):
        return treq.request(method=method, url=url, data=data, **kwargs)

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):
        try:
            page = Page.from_fp(request.content)
            self.emit("MessengerTransport inbound %r" % (page,))
        except (UnsupportedMessage,), e:
            self.respond(message_id, http.OK, {
                'warning': 'Accepted unsuppported message: %s' % (e,)
            })
            self.emit("MessengerTransport failed: %s" % (e,))
            return

        yield self.publish_message(
            message_id=message_id,
            from_addr=page.from_addr,
            from_addr_type='facebook_messenger',
            to_addr=page.to_addr,
            content=page.content,
            provider='facebook',
            transport_type=self.transport_type,
            transport_metadata={
                'messenger': {
                    'mid': page.mid,
                }
            })

        self.respond(message_id, http.OK, {})

        yield self.add_status(
            component='inbound',
            status='ok',
            type='request_success',
            message='Request successful')

    @inlineCallbacks
    def handle_outbound_message(self, message):
        self.emit("MessengerTransport outbound %r" % (message,))
        resp = yield self.request(
            method='POST',
            url='%s?access_token=%s' % (self.config['outbound_url'],
                                        self.config['access_token']),
            data=json.dumps({
                'recipient': {
                    'id': message['to_addr'],
                },
                'message': {
                    'text': message['content'],
                }
            }),
            headers={
                'Content-Type': 'application/json',
            },
            pool=self.pool)

        data = yield resp.json()
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
            yield self.publish_nack(
                user_message_id=message['message_id'],
                sent_message_id=message['message_id'],
                reason=data['error']['message'])
            yield self.add_status(
                component='outbound',
                status='down',
                type=self.get_send_fail_type(status['code']),
                message=status['message'])

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

import json
from datetime import datetime
from urllib import urlencode

import treq


from confmodel.fallbacks import SingleFieldFallback
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
    page_id = ConfigText(
        "The page id for the Messenger API",
        required=False,
        fallbacks=[SingleFieldFallback("app_id")])
    app_id = ConfigText(
        "DEPRECATED The page app id for the Messenger API",
        required=False)
    welcome_message = ConfigDict(
        ("The payload for setting up a welcome message. "
         "Requires a page_id to be set"),
        required=False)
    retrieve_profile = ConfigBool(
        "Set to true to include the user profile details in "
        "the helper_metadata", required=False, default=False)


class Page(object):
    """A thing that parses "Page" objects as received from Messenger"""

    def __init__(self, to_addr, from_addr,
                 mid, content, timestamp, in_reply_to=None, extra=None):
        self.to_addr = to_addr
        self.from_addr = from_addr
        self.in_reply_to = in_reply_to
        self.mid = mid
        self.content = content
        self.timestamp = timestamp
        self.extra = extra if extra else {}

    def __str__(self):
        return ("<Page to_addr: %s, from_addr: %s, in_reply_to: %s, "
                "content: %s, mid: %s, timestamp: %s, extra: %s>") % (
            self.to_addr,
            self.from_addr,
            self.in_reply_to,
            self.content,
            self.mid,
            self.timestamp,
            json.dumps(self.extra)
        )

    @classmethod
    def from_fp(cls, fp):

        def fb_timestamp(timestamp):
            return datetime.fromtimestamp(timestamp / 1000)

        try:
            data = json.load(fp)
        except (ValueError, KeyError), e:
            raise UnsupportedMessage('Unable to parse message: %s' % (e,))

        messages = []
        errors = []

        for entry in data.get('entry', []):
            for msg in entry.get('messaging', []):
                if ('message' in msg) and ('quick_reply' in msg['message']):
                    payload = json.loads(
                        msg['message']['quick_reply']['payload']
                    )
                    in_reply_to = payload.get('in_reply_to')
                    try:
                        del payload['in_reply_to']
                    except KeyError:
                        pass
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=msg['message']['mid'],
                        content=msg['message'].get('text', ''),
                        in_reply_to=in_reply_to,
                        timestamp=fb_timestamp(msg['timestamp']),
                        extra=payload
                    ))
                elif ('message' in msg) and ('text' in msg['message']):
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=msg['message']['mid'],
                        content=msg['message']['text'],
                        timestamp=fb_timestamp(msg['timestamp'])
                    ))
                elif ('message' in msg) and ('attachments' in msg['message']):
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=msg['message']['mid'],
                        content='',
                        extra={'attachments': msg['message']['attachments']},
                        timestamp=fb_timestamp(msg['timestamp'])
                    ))
                elif 'optin' in msg:
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=None,
                        content='',
                        extra={'optin': msg['optin']},
                        timestamp=fb_timestamp(msg['timestamp'])
                    ))
                elif 'delivery' in msg:
                    errors.append('Not supporting delivery messages yet: %s.'
                                  % (msg,))
                elif 'postback' in msg:
                    payload = json.loads(msg['postback']['payload'])
                    content = payload.get('content', '')
                    in_reply_to = payload.get('in_reply_to')
                    try:
                        del payload['content']
                    except KeyError:
                        pass
                    try:
                        del payload['in_reply_to']
                    except KeyError:
                        pass
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=None,
                        content=content,
                        in_reply_to=in_reply_to,
                        extra=payload,
                        timestamp=fb_timestamp(msg['timestamp'])
                    ))
                else:
                    errors.append('Not supporting: %s' % (msg,))
        return messages, errors


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
            if not self.config.get('page_id'):
                self.log.error('page_id is required for welcome_message')
                return
            try:
                data = yield self.setup_welcome_message(
                    self.config['welcome_message'],
                    self.config['page_id'])
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
    def setup_welcome_message(self, welcome_message_payload, page_id):
        response = yield self.request(
            'POST',
            "https://graph.facebook.com/v2.6/%s/thread_settings?%s" % (
                page_id,
                urlencode({
                    'access_token': self.config['access_token'],
                })),
            data=json.dumps({
                'setting_type': 'call_to_actions',
                'thread_state': 'new_thread',
                'call_to_actions': welcome_message_payload
            }, separators=(',', ':')),
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

        self.finish_request(message_id,
                            json.dumps(body, separators=(',', ':')),
                            code=code)

    def request(self, method, url, data, **kwargs):
        return treq.request(method=method, url=url, data=data, **kwargs)

    @inlineCallbacks
    def handle_raw_inbound_message(self, message_id, request):

        if 'hub.challenge' in request.args:
            self.finish_request(message_id, request.args['hub.challenge'][0],
                                code=http.OK)
            return

        try:
            pages, errors = Page.from_fp(request.content)
        except (UnsupportedMessage,), e:
            self.respond(message_id, http.OK, {
                'warning': 'Accepted unsuppported message: %s' % (e,)
            })
            self.log.error(e)
            return

        if pages:
            self.log.info("MessengerTransport inbound %r" % (pages,))
        for error in errors:
            self.log.error(error)

        for page in pages:
            if self.config.get('retrieve_profile'):
                helper_metadata = yield self.get_user_profile(page.from_addr)
            else:
                helper_metadata = {}
            transport_metadata = dict(page.extra, mid=page.mid)
            helper_metadata.update(transport_metadata)

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
                    'messenger': transport_metadata
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
            url='https://graph.facebook.com/v2.6/%s?%s' % (
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

        template_type = messenger_metadata.get('template_type')
        if template_type == 'button':
            return self.construct_button_reply(message)
        if template_type == 'generic':
            return self.construct_generic_reply(message)
        if template_type == 'quick':
            return self.construct_quick_reply(message)

        return self.construct_plain_reply(message)

    def construct_button(self, btn):
        typ = btn.get('type', 'postback')
        ret = {
            'type': typ,
            'title': btn['title'],
        }
        if typ == 'postback':
            ret['payload'] = json.dumps(btn['payload'], separators=(',', ':'))
        elif typ == 'web_url':
            ret['url'] = btn['url']
        elif typ == 'phone_number':
            ret['payload'] = btn['payload']
        else:
            raise UnsupportedMessage('Unknown button type "%s"' % typ)
        return ret

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
                        'buttons': [self.construct_button(btn)
                                    for btn in button['buttons']]
                    }
                }
            }
        }

    def construct_element(self, element):
        ret = {
            'title': element['title'],
        }
        if element.get('subtitle'):
            ret['subtitle'] = element['subtitle']
        if element.get('image_url'):
            ret['image_url'] = element['image_url']
        if element.get('item_url'):
            ret['item_url'] = element['item_url']
        buttons = [self.construct_button(btn) for btn in element['buttons']]
        if buttons:
            ret['buttons'] = buttons
        return ret

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
                        'elements': [self.construct_element(element)
                                     for element in button['elements']]
                    }
                }
            }
        }

    def construct_quick_button(self, btn):
        typ = btn.get('type', 'text')
        ret = {
            'content_type': typ
        }
        if typ == 'text':
            ret['title'] = btn['title']
            ret['payload'] = json.dumps(btn['payload'], separators=(',', ':'))
            if btn.get('image_url'):
                ret['image_url'] = btn['image_url']
        elif typ == 'location':
            pass
        else:
            raise UnsupportedMessage('Unknown quick reply type "%s"' % typ)
        return ret

    def construct_quick_reply(self, message):
        button = message['helper_metadata']['messenger']
        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'text': button['text'],
                'quick_replies': [self.construct_quick_button(btn)
                                  for btn in button['quick_replies']]
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
                data=json.dumps(reply, separators=(',', ':')),
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

import json
from datetime import datetime
from urllib import urlencode
from urlparse import urlsplit, urlunsplit

import treq
from confmodel.fallbacks import SingleFieldFallback
from twisted.internet import reactor
from twisted.internet.defer import inlineCallbacks, returnValue, DeferredLock
from twisted.internet.task import LoopingCall
from twisted.web import http
from twisted.web.client import HTTPConnectionPool

from vumi.config import (ConfigText, ConfigDict, ConfigBool, ConfigInt,
                         ConfigFloat)
from vumi.persist.txredis_manager import TxRedisManager
from vumi.transports.httprpc import HttpRpcTransport


class MessengerTransportConfig(HttpRpcTransport.CONFIG_CLASS):

    access_token = ConfigText(
        "The access_token for the Messenger API",
        required=True)
    page_id = ConfigText(
        "The page id for the Messenger API",
        required=False, fallbacks=[SingleFieldFallback("app_id")])
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
    request_batch_size = ConfigInt(
        "The maximum number of requests to send using using batch API calls",
        required=False, default=50, static=True)
    request_batch_wait_time = ConfigFloat(
        "The time to wait between batch API calls (in seconds)",
        required=False, default=0.1, static=True)
    redis_manager = ConfigDict(
        "Parameters to connect to Redis with",
        required=False, default={}, static=True)


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
                    if 'referral' in msg['postback']:
                        payload['referral'] = msg['postback']['referral']
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=None,
                        content=content,
                        in_reply_to=in_reply_to,
                        extra=payload,
                        timestamp=fb_timestamp(msg['timestamp'])
                    ))
                elif 'referral' in msg:
                    source = msg['referral']['source']
                    extra = {
                        'referral': {
                            'source': source,
                            'ref': msg['referral'].get('ref'),
                        }
                    }
                    if source == 'ADS':
                        extra['referral']['ad_id'] = msg['referral']['ad_id']
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=None,
                        content='',
                        extra=extra,
                        timestamp=fb_timestamp(msg['timestamp'])
                    ))
                elif 'account_linking' in msg:
                    messages.append(cls(
                        to_addr=msg['recipient']['id'],
                        from_addr=msg['sender']['id'],
                        mid=None,
                        content='',
                        extra={'account_linking': msg['account_linking']},
                        timestamp=fb_timestamp(msg['timestamp']),
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

        self.outbound_url = self.config.get('outbound_url')
        scheme, domain, path, query, fragment = urlsplit(self.outbound_url)
        self.BATCH_API_URL = urlunsplit([scheme, domain, '', '', ''])
        self.MESSAGES_API_PATH = path.lstrip('/')

        static_config = self.get_static_config()
        self.batch_size = static_config.request_batch_size
        self.batch_time = static_config.request_batch_wait_time

        self.redis = yield TxRedisManager.from_config(
            static_config.redis_manager)

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

        self.queue_len = 0
        self.pending_requests = []
        self._lock = DeferredLock()
        self._request_loop = LoopingCall(self.dispatch_requests)
        self._start_request_loop(self._request_loop)

    @inlineCallbacks
    def teardown_transport(self):
        if hasattr(self, 'web_resource'):
            yield self.web_resource.loseConnection()
            if self.request_gc.running:
                self.request_gc.stop()
        if self._request_loop.running:
            self._request_loop.stop()

    def _start_request_loop(self, loop):
        if not loop.running:
            loop.start(self.batch_time).addErrback(self._request_loop_error)

    def _request_loop_error(self, failure):
        self.log.info('Error in request_loop: %s' % failure.value)
        self.log.info('Restarting request_loop...')
        self._start_request_loop(self._request_loop)

    @inlineCallbacks
    def add_request(self, request):
        req_string = json.dumps(request)
        self.queue_len = yield self.redis.rpush('request_queue', req_string)

    @inlineCallbacks
    def dispatch_requests(self):
        yield self._lock.acquire()
        try:
            yield self._dispatch_requests()
        finally:
            yield self._lock.release()

    @inlineCallbacks
    def _dispatch_requests(self):
        batch_size = (self.batch_size if self.batch_size <= self.queue_len
                      else self.queue_len)
        if batch_size == 0:
            return

        data = {
            'access_token': self.config['access_token'],
            'include_headers': False,
            'batch': [],
        }
        for i in range(0, batch_size):
            req_string = yield self.redis.lpop('request_queue')
            if req_string is None:
                continue
            request = json.loads(req_string)
            self.pending_requests.append(request)
            data['batch'].append({
                'method': request['method'],
                'relative_url': request['relative_url'],
                'body': request.get('body', ''),
            })

        data['batch'] = json.dumps(data['batch'])
        response = yield self.request('POST', self.BATCH_API_URL, data,
                                      pool=self.pool)
        if response.code == http.OK:
            yield self.handle_batch_response(response)
        else:
            yield self.handle_batch_error(response)

    @inlineCallbacks
    def handle_batch_response(self, response):
        content = yield response.json()
        for i, res in enumerate(content):
            req = self.pending_requests[i]
            if res is None:
                # Request was not completed, add to queue again
                yield self.add_request(req)
            elif res.get('code') == http.OK:
                if res['body'].get('message_id') is None:
                    # TODO: acknowledge success of non-message requests
                    continue
                yield self.handle_outbound_success(
                    req['message_id'], res['body']['message_id'])
            else:
                body = json.loads(res['body'])
                fail_type = self.SEND_FAIL_TYPES.get(
                    body['error']['code'], 'request_fail_unknown')
                yield self.handle_outbound_failure(
                    req['message_id'], body['error']['message'], fail_type)

        self.pending_requests = []

    @inlineCallbacks
    def handle_batch_error(self, response):
        # It's possible that some requests might still have been completed
        try:
            yield self.handle_batch_response(response)
        except (ValueError, KeyError, AttributeError):
            pass

        code = response.code
        for req in self.pending_requests:
            yield self.handle_outbound_failure(
                req['message_id'], 'Batch request failed (%s)' % code,
                'batch_request_fail')

    @inlineCallbacks
    def handle_outbound_success(self, user_message_id, sent_message_id):
        yield self.publish_ack(
            user_message_id=user_message_id,
            sent_message_id=sent_message_id)
        yield self.add_status(
            component='outbound',
            status='ok',
            type='request_success',
            message='Request successful')

    @inlineCallbacks
    def handle_outbound_failure(self, message_id, reason, status_type):
        yield self.publish_nack(
            user_message_id=message_id,
            sent_message_id=message_id,
            reason=reason)
        yield self.add_status(
            component='outbound',
            status='down',
            type=status_type,
            message=reason)

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
        meta = message.get('helper_metadata', {}).get('messenger', {})

        SENDER_ACTIONS = {'typing_on', 'typing_off', 'mark_seen'}
        if meta.get('sender_action') in SENDER_ACTIONS:
            return {
                'recipient': {'id': message['to_addr']},
                'sender_action': meta['sender_action']
            }

        template_type = meta.get('template_type')
        reply = {}
        media = meta.get('media', {})

        # TODO: handle media messages properly
        if template_type == 'button':
            reply = self.construct_button_reply(message)
        elif template_type == 'generic':
            reply = self.construct_generic_reply(message)
        elif template_type == 'list':
            reply = self.construct_list_reply(message)
        elif template_type == 'receipt':
            reply = self.construct_receipt_reply(message)
        elif media.get('type', '') in {'audio', 'video', 'image', 'file'}:
            reply = self.construct_media_reply(message)
        else:
            reply = self.construct_plain_reply(message)

        if meta.get('quick_replies') is not None:
            reply['message']['quick_replies'] = [
                self.construct_quick_button(btn)
                for btn in meta['quick_replies']]
        return reply

    def construct_button(self, btn):
        typ = btn.get('type', 'postback')
        if typ == 'element_share':
            share_btn = {'type': 'element_share'}
            if 'share_contents' in btn:
                share_btn['share_contents'] = btn['share_contents']
            return share_btn
        if typ == 'account_linking':
            return {
                'type': 'account_linking',
                'url': btn['url'],
            }
        if typ == 'account_unlinking':
            return {'type': 'account_unlinking'}
        ret = {
            'type': typ,
            'title': btn['title'],
        }
        if typ == 'postback':
            ret['payload'] = json.dumps(btn['payload'], separators=(',', ':'))
        elif typ == 'web_url':
            ret['url'] = btn['url']
            if 'webview_height_ratio' in btn:
                ret['webview_height_ratio'] = btn['webview_height_ratio']
            if 'messenger_extensions' in btn:
                ret['messenger_extensions'] = btn['messenger_extensions']
                if 'fallback_url' in btn:
                    ret['fallback_url'] = btn['fallback_url']
            if 'webview_share_button' in btn:
                ret['webview_share_button'] = btn['webview_share_button']
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
                        'sharable': button.get('sharable', True),
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
        if element.get('default_action'):
            act = element['default_action']
            if 'url' in act:
                defa = {
                    'type': 'web_url',
                    'url': act['url'],
                }
                if 'webview_height_ratio' in act:
                    defa['webview_height_ratio'] = act['webview_height_ratio']
                    if 'messenger_extensions' in act:
                        defa['messenger_extensions'] =\
                            act['messenger_extensions']
                    if 'fallback_url' in act:
                        defa['fallback_url'] = act['fallback_url']
                ret['default_action'] = defa
        buttons = [self.construct_button(btn) for btn in element['buttons']]
        if buttons:
            ret['buttons'] = buttons
        return ret

    def construct_generic_reply(self, message):
        template = message['helper_metadata']['messenger']
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
                                     for element in template['elements']]
                    }
                }
            }
        }

    def construct_list_reply(self, message):
        template = message['helper_metadata']['messenger']
        payload = {
            'template_type': 'list',
            'top_element_style': template.get('top_element_style', 'compact'),
            'elements': [self.construct_element(element)
                         for element in template['elements']]
        }
        if 'buttons' in template:
            btns = [self.construct_button(btn) for btn in template['buttons']]
            if btns:
                payload['buttons'] = btns
        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'attachment': {
                    'type': 'template',
                    'payload': payload
                }
            }
        }

    def construct_receipt_reply(self, message):
        template = message['helper_metadata']['messenger']

        def construct_receipt_element(element):
            ret = {
                'title': element['title'],
                'price': element['price'],
            }
            if element.get('image_url'):
                ret['image_url'] = element['image_url']
            if element.get('subtitle'):
                ret['subtitle'] = element['subtitle']
            if element.get('quantity'):
                ret['quantity'] = element['quantity']
            if element.get('currency'):
                ret['currency'] = element['currency']
            return ret

        payload = {
            'template_type': 'receipt',
            'order_number': template['order_number'],
            'currency': template['currency'],
            'payment_method': template['payment_method'],
            'recipient_name': template['recipient_name'],
            'summary': {
                'total_cost': template['summary']['total_cost'],
            },
        }
        if template.get('elements'):
            payload['elements'] = [construct_receipt_element(element)
                                   for element in template['elements']]
        if template.get('address'):
            payload['address'] = {
                'street_1': template['address']['street_1'],
                'street_2': template['address'].get('street_2', ''),
                'city': template['address']['city'],
                'state': template['address']['state'],
                'country': template['address']['country'],
                'postal_code': template['address']['postal_code'],
            }

        # Remaining non-required fields
        for field in {'merchant_name', 'order_url', 'timestamp',
                      'adjustments'}:
            if template.get(field):
                payload[field] = template[field]
        for field in {'subtotal', 'total_tax', 'shipping_cost'}:
            if template['summary'].get(field):
                payload['summary'][field] = template['summary'][field]

        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'attachment': {
                    'type': 'template',
                    'payload': payload,
                },
            },
        }

    def construct_media_reply(self, message):
        media = message['helper_metadata']['messenger']['media']
        return {
            'recipient': {
                'id': message['to_addr'],
            },
            'message': {
                'attachment': {
                    'type': media['type'],
                    'payload': {
                        'url': media['url'],
                    },
                },
            },
        }

    def construct_quick_button(self, btn):
        typ = btn.get('content_type', 'text')
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

        request = {
            'message_id': message['message_id'],
            'method': 'POST',
            'relative_url': self.MESSAGES_API_PATH,
            'body': urlencode(reply),
        }
        yield self.add_request(request)

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

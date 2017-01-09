import json

from vumi.tests.helpers import VumiTestCase
from vumi.tests.utils import MockHttpServer

from twisted.internet import reactor
from twisted.internet.defer import (
    inlineCallbacks, returnValue, DeferredQueue, Deferred)
from twisted.internet.task import Clock
from twisted.web import http
from twisted.web.client import HTTPConnectionPool

from vxmessenger.transport import MessengerTransport, UnsupportedMessage
from vumi.transports.httprpc.tests.helpers import HttpRpcTransportHelper
from vumi.tests.helpers import MessageHelper

import treq


class DummyResponse(object):

    def __init__(self, code, content):
        self.code = code
        self.content = content

    def json(self):
        d = Deferred()
        reactor.callLater(0, d.callback, json.loads(self.content))
        return d


class PatchedMessengerTransport(MessengerTransport):

    def __init__(self, *args, **kwargs):
        super(PatchedMessengerTransport, self).__init__(*args, **kwargs)
        self.request_queue = DeferredQueue()

    def request(self, method, url, data, **kwargs):
        d = Deferred()
        self.request_queue.put((d, (method, url, data), kwargs))
        return d


class TestMessengerTransport(VumiTestCase):

    timeout = 1

    @inlineCallbacks
    def setUp(self):
        self.clock = Clock()

        MessengerTransport.clock = self.clock

        self.remote_server = MockHttpServer(lambda _: 'OK')
        yield self.remote_server.start()
        self.addCleanup(self.remote_server.stop)

        self.tx_helper = self.add_helper(
            HttpRpcTransportHelper(PatchedMessengerTransport))
        self.msg_helper = self.add_helper(MessageHelper())

        connection_pool = HTTPConnectionPool(reactor, persistent=False)
        treq._utils.set_global_pool(connection_pool)

    @inlineCallbacks
    def mk_transport(self, **kw):
        config = {
            'web_port': 0,
            'web_path': '/api',
            'publish_status': True,
            'outbound_url': self.remote_server.url,
            'username': 'root',
            'password': 't00r',
        }
        config.update(kw)

        transport = yield self.tx_helper.get_transport(config)
        transport.clock = self.clock
        returnValue(transport)

    @inlineCallbacks
    def test_add_request(self):
        transport = yield self.mk_transport()
        request = {'foo': 'bar'}
        yield transport.add_request(request)

        self.assertEqual(transport.queue_len, 1)

    @inlineCallbacks
    def test_dispatch_requests(self):
        transport = yield self.mk_transport(access_token='access-token')
        requests = [
            {
                'message_id': '123',
                'method': 'POST',
                'relative_url': 'foo',
                'body': {'param': 'value'},
            },
            {
                'message_id': '456',
                'method': 'GET',
                'relative_url': 'bar',
                'body': '',
            },
        ]
        batch = []
        for i, req in enumerate(requests):
            batch.append(req)
            del batch[i]['message_id']
            yield transport.add_request(req)

        d = transport.dispatch_requests()
        request_d, args, kwargs = yield transport.request_queue.get()
        request_d.callback(DummyResponse(200, json.dumps({})))

        method, url, data = args
        self.assertEqual(method, 'POST')
        self.assertEqual(url, 'https://graph.facebook.com')
        self.assertEqual(data, {
            'access_token': 'access-token',
            'include_headers': False,
            'batch': batch,
        })
        yield d

    @inlineCallbacks
    def test_handle_batch_response_all_types(self):
        transport = yield self.mk_transport()
        transport.pending_requests = [
            {'message_id': '1'}, {'message_id': '2'}, {'message_id': '3'},
        ]
        response = DummyResponse(200, json.dumps([
            {
                'code': 200,
                'body': 'success!',
            },
            {
                'code': 400,
                'body': json.dumps({'error': {
                    'code': 400,
                    'message': 'bad request',
                }}),
            },
            None,   # the request could not be completed or timed out
        ]))

        yield transport.handle_batch_response(response)
        self.assertEqual(transport.pending_requests, [])

        request = yield transport.redis.lpop('request_queue')
        self.assertEqual(request, json.dumps({'message_id': '3'}))

    @inlineCallbacks
    def test_hub_challenge(self):
        yield self.mk_transport()
        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            params={
                'hub.challenge': 'foo',
            }
        )
        self.assertEqual(res.code, http.OK)
        self.assertEqual(res.delivered_body, 'foo')

    @inlineCallbacks
    def test_setup_welcome_message(self):
        transport = yield self.mk_transport(
            access_token='access-token')
        d = transport.setup_welcome_message({
            'message': {
                'text': 'This is the welcome message!'
            }
        }, 'app-id')

        (request_d, args, kwargs) = yield transport.request_queue.get()
        request_d.callback(DummyResponse(200, json.dumps({})))
        method, url, data = args
        self.assertTrue('app-id' in url)
        self.assertTrue('?access_token=access-token' in url)
        self.assertEqual(json.loads(data)['call_to_actions'], {
            'message': {
                'text': 'This is the welcome message!'
            }
        })
        yield d

    @inlineCallbacks
    def test_inbound(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "message": {
                            "mid": "mid.1457764197618:41d102a3e1ae206a38",
                            "seq": 73,
                            "text": "hello, world!"
                        }
                    }]
                }]
            }))

        self.assertEqual(res.code, http.OK)

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg['from_addr'], 'USER_ID')
        self.assertEqual(msg['to_addr'], 'PAGE_ID')
        self.assertEqual(msg['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg['content'], 'hello, world!')
        self.assertEqual(msg['provider'], 'facebook')
        self.assertEqual(msg['transport_metadata'], {
            'messenger': {
                'mid': 'mid.1457764197618:41d102a3e1ae206a38'
            }
        })

        statuses = self.tx_helper.get_dispatched_statuses()
        [response_status, inbound_status] = statuses

        self.assertEqual(response_status['status'], 'ok')
        self.assertEqual(response_status['component'], 'response')
        self.assertEqual(response_status['type'], 'response_sent')
        self.assertEqual(response_status['message'], 'Response sent')

        self.assertEqual(inbound_status['status'], 'ok')
        self.assertEqual(inbound_status['component'], 'inbound')
        self.assertEqual(inbound_status['type'], 'request_success')
        self.assertEqual(inbound_status['message'], 'Request successful')

    @inlineCallbacks
    def test_inbound_multiple(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID1"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "message": {
                            "mid": "mid.1457764197618:41d102a3e1ae206a38",
                            "seq": 73,
                            "text": "hello, world!"
                        }
                    }, {
                        "sender": {
                            "id": "USER_ID2"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "message": {
                            "mid": "mid.1457764197618:41d102a3e1ae206a39",
                            "seq": 74,
                            "text": "hello, again!"
                        }
                    }]
                }]
            }))

        self.assertEqual(res.code, http.OK)

        [msg1, msg2] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg1['from_addr'], 'USER_ID1')
        self.assertEqual(msg1['to_addr'], 'PAGE_ID')
        self.assertEqual(msg1['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg1['content'], 'hello, world!')
        self.assertEqual(msg1['provider'], 'facebook')
        self.assertEqual(msg1['transport_metadata'], {
            'messenger': {
                'mid': 'mid.1457764197618:41d102a3e1ae206a38'
            }
        })

        self.assertEqual(msg2['from_addr'], 'USER_ID2')
        self.assertEqual(msg2['to_addr'], 'PAGE_ID')
        self.assertEqual(msg2['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg2['content'], 'hello, again!')
        self.assertEqual(msg2['provider'], 'facebook')
        self.assertEqual(msg2['transport_metadata'], {
            'messenger': {
                'mid': 'mid.1457764197618:41d102a3e1ae206a39'
            }
        })

        statuses = self.tx_helper.get_dispatched_statuses()
        [response_status, inbound_status] = statuses

        self.assertEqual(response_status['status'], 'ok')
        self.assertEqual(response_status['component'], 'response')
        self.assertEqual(response_status['type'], 'response_sent')
        self.assertEqual(response_status['message'], 'Response sent')

        self.assertEqual(inbound_status['status'], 'ok')
        self.assertEqual(inbound_status['component'], 'inbound')
        self.assertEqual(inbound_status['type'], 'request_success')
        self.assertEqual(inbound_status['message'], 'Request successful')

    @inlineCallbacks
    def test_inbound_attachments(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "message": {
                            "mid": "mid.1457764197618:41d102a3e1ae206a37",
                            "seq": 63,
                            "attachments": [{
                                "type": "image",
                                "payload": {
                                    "url": "IMAGE_URL"
                                }
                            }]
                        }
                    }]
                }]
            }))

        self.assertEqual(res.code, http.OK)

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg['from_addr'], 'USER_ID')
        self.assertEqual(msg['to_addr'], 'PAGE_ID')
        self.assertEqual(msg['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg['provider'], 'facebook')
        self.assertEqual(msg['content'], '')
        self.assertEqual(msg['transport_metadata'], {
            'messenger': {
                'mid': "mid.1457764197618:41d102a3e1ae206a37",
                "attachments": [{
                    "type": "image",
                    "payload": {
                        "url": "IMAGE_URL"
                    }
                }]
            }
        })

    @inlineCallbacks
    def test_inbound_optin(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "optin": {
                            "ref": "PASS_THROUGH_PARAM"
                        }
                    }]
                }]
            }))

        self.assertEqual(res.code, http.OK)

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg['from_addr'], 'USER_ID')
        self.assertEqual(msg['to_addr'], 'PAGE_ID')
        self.assertEqual(msg['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg['provider'], 'facebook')
        self.assertEqual(msg['content'], '')
        self.assertEqual(msg['transport_metadata'], {
            'messenger': {
                'mid': None,
                "optin": {
                    "ref": "PASS_THROUGH_PARAM"
                }
            }
        })

    @inlineCallbacks
    def test_inbound_postback(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "postback": {
                            "payload": json.dumps({
                                "content": "1",
                                "in_reply_to": "12345",
                            })
                        }
                    }]
                }]
            }))

        self.assertEqual(res.code, http.OK)

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg['from_addr'], 'USER_ID')
        self.assertEqual(msg['to_addr'], 'PAGE_ID')
        self.assertEqual(msg['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg['provider'], 'facebook')
        self.assertEqual(msg['content'], '1')
        self.assertEqual(msg['in_reply_to'], '12345')
        self.assertEqual(msg['transport_metadata'], {
            'messenger': {
                'mid': None
            }
        })

    @inlineCallbacks
    def test_inbound_postback_other(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "postback": {
                            "payload": json.dumps({
                                "postback": "ocean"
                            })
                        }
                    }]
                }]
            }))

        self.assertEqual(res.code, http.OK)

        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg['from_addr'], 'USER_ID')
        self.assertEqual(msg['to_addr'], 'PAGE_ID')
        self.assertEqual(msg['from_addr_type'], 'facebook_messenger')
        self.assertEqual(msg['provider'], 'facebook')
        self.assertEqual(msg['content'], '')
        self.assertEqual(msg['in_reply_to'], None)
        self.assertEqual(msg['transport_metadata'], {
            'messenger': {
                'mid': None,
                "postback": "ocean"
            }
        })

    @inlineCallbacks
    def test_inbound_with_user_profile(self):
        transport = yield self.mk_transport(
            access_token='the-access-token',
            retrieve_profile=True)

        d = self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                "object": "page",
                "entry": [{
                    "id": "PAGE_ID",
                    "time": 1457764198246,
                    "messaging": [{
                        "sender": {
                            "id": "USER_ID"
                        },
                        "recipient": {
                            "id": "PAGE_ID"
                        },
                        "timestamp": 1457764197627,
                        "message": {
                            "mid": "mid.1457764197618:41d102a3e1ae206a38",
                            "seq": 73,
                            "text": "hello, world!"
                        }
                    }]
                }]
            }))

        (request_d, args, kwargs) = yield transport.request_queue.get()
        method, url, data = args
        # NOTE: this is the URLencoding
        self.assertTrue('first_name%2Clast_name%2Cprofile_pic' in url)
        self.assertTrue('the-access-token' in url)
        request_d.callback(DummyResponse(200, json.dumps({
            'first_name': 'first-name',
            'last_name': 'last-name',
            'profile_pic': 'rather unpleasant',
        })))

        res = yield d
        self.assertEqual(res.code, http.OK)
        [msg] = yield self.tx_helper.wait_for_dispatched_inbound(1)

        self.assertEqual(msg['helper_metadata'], {
            'messenger': {
                'mid': 'mid.1457764197618:41d102a3e1ae206a38',
                'first_name': 'first-name',
                'last_name': 'last-name',
                'profile_pic': 'rather unpleasant'
            }
        })

    @inlineCallbacks
    def test_outbound(self):
        transport = yield self.mk_transport(access_token='access_token')

        d = self.tx_helper.make_dispatch_outbound(
            from_addr='456',
            to_addr='+123',
            content='hi')

        (request_d, args, kwargs) = yield transport.request_queue.get()
        method, url, data = args
        self.assertEqual(json.loads(data), {
            'message': {
                'text': 'hi',
            },
            'recipient': {
                'id': '+123',
            }
        })
        request_d.callback(DummyResponse(200, json.dumps({
            'message_id': 'the-message-id'
        })))

        msg = yield d
        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)

        self.assertEqual(ack['user_message_id'], msg['message_id'])
        self.assertEqual(ack['sent_message_id'], 'the-message-id')

        [status] = self.tx_helper.get_dispatched_statuses()

        self.assertEqual(status['status'], 'ok')
        self.assertEqual(status['component'], 'outbound')
        self.assertEqual(status['type'], 'request_success')
        self.assertEqual(status['message'], 'Request successful')

    @inlineCallbacks
    def test_construct_plain_reply(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound('hello world', to_addr='123')

        self.assertEqual(
            transport.construct_reply(msg),
            {
                'message': {
                    'text': 'hello world'
                },
                'recipient': {
                    'id': '123'
                }
            })

    @inlineCallbacks
    def test_construct_button_reply(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound(
            'hello world', to_addr='123', helper_metadata={
                'messenger': {
                    'template_type': 'button',
                    'text': 'hello world',
                    'buttons': [{
                        'title': 'Jupiter',
                        'payload': {
                            'content': '1',
                        },
                    }, {
                        'type': 'web_url',
                        'title': 'Mars',
                        'url': 'http://test',
                    }, {
                        'type': 'phone_number',
                        'title': 'Venus',
                        'payload': '+271234567',
                    }]
                }
            })

        self.assertEqual(
            transport.construct_reply(msg),
            {
                'recipient': {
                    'id': '123',
                },
                'message': {
                    'attachment': {
                        'type': 'template',
                        'payload': {
                            'template_type': 'button',
                            'text': 'hello world',
                            'buttons': [
                                {
                                    'type': 'postback',
                                    'title': 'Jupiter',
                                    'payload': '{"content":"1"}',
                                },
                                {
                                    'type': 'web_url',
                                    'title': 'Mars',
                                    'url': 'http://test',
                                },
                                {
                                    'type': 'phone_number',
                                    'title': 'Venus',
                                    'payload': '+271234567',
                                }
                            ]
                        }
                    }
                }
            })

    @inlineCallbacks
    def test_construct_bad_button(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound(
            'hello world', to_addr='123', helper_metadata={
                'messenger': {
                    'template_type': 'button',
                    'text': 'hello world',
                    'buttons': [{
                        'title': 'Jupiter',
                        'payload': {
                            'content': '1',
                        },
                    }, {
                        'type': 'unknown',
                        'title': 'Mars',
                    }]
                }
            })

        with self.assertRaisesRegexp(
                UnsupportedMessage,
                'Unknown button type "unknown"'):
            transport.construct_reply(msg)

    @inlineCallbacks
    def test_construct_quick_reply(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound(
            'hello world', to_addr='123', helper_metadata={
                'messenger': {
                    'template_type': 'quick',
                    'text': 'hello world',
                    'quick_replies': [{
                        'title': 'Jupiter',
                        'payload': {
                            'content': '1',
                        },
                    }, {
                        'type': 'text',
                        'title': 'Mars',
                        'payload': {
                            'content': '2',
                        },
                    }, {
                        'type': 'text',
                        'title': 'Venus',
                        'image_url': 'http://image',
                        'payload': {
                            'content': '3',
                        },
                    }, {
                        'type': 'location',
                    }]
                }
            })

        self.assertEqual(
            transport.construct_reply(msg),
            {
                'recipient': {
                    'id': '123',
                },
                'message': {
                    'text': 'hello world',
                    'quick_replies': [
                        {
                            'content_type': 'text',
                            'title': 'Jupiter',
                            'payload': '{"content":"1"}',
                        },
                        {
                            'content_type': 'text',
                            'title': 'Mars',
                            'payload': '{"content":"2"}',
                        },
                        {
                            'content_type': 'text',
                            'title': 'Venus',
                            'payload': '{"content":"3"}',
                            'image_url': 'http://image',
                        },
                        {
                            'content_type': 'location',
                        },
                    ]
                }
            })

    @inlineCallbacks
    def test_construct_bad_quick_reply(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound(
            'hello world', to_addr='123', helper_metadata={
                'messenger': {
                    'template_type': 'quick',
                    'text': 'hello world',
                    'quick_replies': [{
                        'title': 'Jupiter',
                        'payload': {
                            'content': '1',
                        },
                    }, {
                        'type': 'unknown',
                        'title': 'Mars',
                    }]
                }
            })

        with self.assertRaisesRegexp(
                UnsupportedMessage,
                'Unknown quick reply type "unknown"'):
            transport.construct_reply(msg)

    @inlineCallbacks
    def test_construct_generic_reply(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound(
            'hello world', to_addr='123', helper_metadata={
                'messenger': {
                    'template_type': 'generic',
                    'elements': [{
                        'title': 'hello world',
                        'subtitle': 'arf',
                        'item_url': 'http://test',
                        'buttons': [{
                            'title': 'Jupiter',
                            'payload': {
                                'content': '1',
                            },
                        }, {
                            'type': 'web_url',
                            'title': 'Mars',
                            'url': 'http://test',
                        }, {
                            'type': 'element_share',
                        }]
                    }, {
                        'title': 'hello again',
                        'image_url': 'http://image',
                        'buttons': [{
                            'title': 'Mercury',
                            'payload': {
                                'content': '1',
                            },
                        }, {
                            'type': 'web_url',
                            'title': 'Venus',
                            'url': 'http://test',
                        }]
                    }
                    ]
                }
            })

        self.maxDiff = None

        self.assertEqual(
            transport.construct_reply(msg),
            {
                'recipient': {
                    'id': '123',
                },
                'message': {
                    'attachment': {
                        'type': 'template',
                        'payload': {
                            'template_type': 'generic',
                            'elements': [{
                                'title': 'hello world',
                                'subtitle': 'arf',
                                'item_url': 'http://test',
                                'buttons': [{
                                    'type': 'postback',
                                    'title': 'Jupiter',
                                    'payload': '{"content":"1"}',
                                }, {
                                    'type': 'web_url',
                                    'title': 'Mars',
                                    'url': 'http://test',
                                }, {
                                    'type': 'element_share',
                                }]
                            }, {
                                'title': 'hello again',
                                'image_url': 'http://image',
                                'buttons': [{
                                    'type': 'postback',
                                    'title': 'Mercury',
                                    'payload': '{"content":"1"}',
                                }, {
                                    'type': 'web_url',
                                    'title': 'Venus',
                                    'url': 'http://test',
                                }]
                            }]
                        }
                    }
                }
            })

    @inlineCallbacks
    def test_construct_list_reply(self):
        transport = yield self.mk_transport()
        msg = self.msg_helper.make_outbound(
            'hello world', to_addr='123', helper_metadata={
                'messenger': {
                    'template_type': 'list',
                    # 'top_element_style': 'compact',
                    'elements': [{
                        'title': 'hello world',
                        'subtitle': 'arf',
                        'default_action': {
                            'url': 'http://test',
                        },
                        'buttons': [{
                            'title': 'Jupiter',
                            'payload': {
                                'content': '1',
                            },
                        }, {
                            'type': 'web_url',
                            'title': 'Mars',
                            'url': 'http://test',
                        }]
                    }, {
                        'title': 'hello again',
                        'image_url': 'http://image',
                        'default_action': {
                            'url': 'http://test',
                            'webview_height_ratio': 'compact',
                            'messenger_extensions': False,
                            'fallback_url': 'http://moo'
                        },
                        'buttons': [{
                            'title': 'Mercury',
                            'payload': {
                                'content': '2',
                            },
                        }, {
                            'type': 'web_url',
                            'title': 'Venus',
                            'url': 'http://test',
                            'webview_height_ratio': 'tall',
                            'messenger_extensions': True,
                            'fallback_url': 'http://moo'
                        }]
                    }
                    ],
                    'buttons': [{
                        'title': 'Europa',
                        'payload': {
                            'content': '3',
                        },
                    }]
                }
            })

        self.maxDiff = None

        self.assertEqual(
            transport.construct_reply(msg),
            {
                'recipient': {
                    'id': '123',
                },
                'message': {
                    'attachment': {
                        'type': 'template',
                        'payload': {
                            'template_type': 'list',
                            'top_element_style': 'compact',
                            'elements': [{
                                'title': 'hello world',
                                'subtitle': 'arf',
                                'default_action': {
                                    'type': 'web_url',
                                    'url': 'http://test',
                                },
                                'buttons': [{
                                    'type': 'postback',
                                    'title': 'Jupiter',
                                    'payload': '{"content":"1"}',
                                }, {
                                    'type': 'web_url',
                                    'title': 'Mars',
                                    'url': 'http://test',
                                }]
                            }, {
                                'title': 'hello again',
                                'image_url': 'http://image',
                                'default_action': {
                                    'type': 'web_url',
                                    'url': 'http://test',
                                    'webview_height_ratio': 'compact',
                                    'messenger_extensions': False,
                                    'fallback_url': 'http://moo'
                                },
                                'buttons': [{
                                    'type': 'postback',
                                    'title': 'Mercury',
                                    'payload': '{"content":"2"}',
                                }, {
                                    'type': 'web_url',
                                    'title': 'Venus',
                                    'url': 'http://test',
                                    'webview_height_ratio': 'tall',
                                    'messenger_extensions': True,
                                    'fallback_url': 'http://moo'
                                }]
                            }],
                            'buttons': [{
                                'type': 'postback',
                                'title': 'Europa',
                                'payload': '{"content":"3"}',
                            }]
                        }
                    }
                }
            })

import json
from urlparse import parse_qs

import treq
from twisted.internet import reactor
from twisted.internet.defer import (inlineCallbacks, returnValue,
                                    DeferredQueue, Deferred)
from twisted.internet.task import Clock
from twisted.web import http
from twisted.web.client import HTTPConnectionPool

from vumi.tests.helpers import VumiTestCase, MessageHelper
from vumi.tests.utils import MockHttpServer
from vumi.transports.httprpc.tests.helpers import HttpRpcTransportHelper

from vxmessenger.transport import MessengerTransport


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
            'outbound_url': 'https://graph.facebook.com/v2.8/me/messages',
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
    def test_batch_error_no_json(self):
        transport = yield self.mk_transport()
        transport.pending_requests = [{'message_id': '1'}]

        yield transport.handle_batch_error(DummyResponse(400, 'fail'))
        yield self.assert_outbound_failure('1', 'Batch request failed (400)',
                                           'batch_request_fail')

    @inlineCallbacks
    def test_batch_error_with_json(self):
        transport = yield self.mk_transport()
        transport.pending_requests = [{'message_id': '1'}]

        yield transport.handle_batch_error(DummyResponse(400, json.dumps({
            'this': 'is',
            'nonsense': 'json',
        })))
        yield self.assert_outbound_failure('1', 'Batch request failed (400)',
                                           'batch_request_fail')

    @inlineCallbacks
    def test_dispatch_requests(self):
        transport = yield self.mk_transport(access_token='access-token')
        requests = [
            {
                'message_id': '123',
                'method': 'POST',
                'relative_url': 'foo',
                'body': 'recipient=%7B%22id%22%3A%221168578783215492%22%7D'
            },
            {
                'message_id': '456',
                'method': 'GET',
                'relative_url': 'bar',
                'body': 'recipient=%7B%22id%22%3A%221168588783215492%22%7D'
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

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'access-token')
        self.assertEqual(json.loads(data['batch']), batch)

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
                'body': json.dumps({
                    'message_id': '123',
                }),
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

        request = yield transport.redis.lpop(transport.REQ_QUEUE_KEY)
        self.assertEqual(request, '{"message_id":"3"}')

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
    def test_inbound_referral(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                'object': 'page',
                'entry': [{
                    'id': 'PAGE_ID',
                    'time': 1457764198246,
                    'messaging': [{
                        'sender': {'id': 'USER_ID'},
                        'recipient': {'id': 'PAGE_ID'},
                        'timestamp': 1457764198246,
                        'referral': {
                            'ref': 'REFERRAL_DATA',
                            'ad_id': '123',
                            'source': 'ADS',
                            # This field appears in all referral types it seems
                            'type': 'OPEN_THREAD',
                        },
                    }],
                }],
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
                'referral': {
                    'ref': 'REFERRAL_DATA',
                    'ad_id': '123',
                    'source': 'ADS',
                },
            },
        })

    @inlineCallbacks
    def test_inbound_account_linking(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                'object': 'page',
                'entry': [{
                    'id': 'PAGE_ID',
                    'time': 1457764198246,
                    'messaging': [{
                        'sender': {'id': 'USER_ID'},
                        'recipient': {'id': 'PAGE_ID'},
                        'timestamp': 1457764198246,
                        'account_linking': {
                            'status': 'linked',
                            'authorization_code': 'AUTH_CODE',
                        },
                    }],
                }],
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
                'account_linking': {
                    'status': 'linked',
                    'authorization_code': 'AUTH_CODE',
                },
            },
        })

    @inlineCallbacks
    def test_inbound_account_unlinking(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                'object': 'page',
                'entry': [{
                    'id': 'PAGE_ID',
                    'time': 1457764198246,
                    'messaging': [{
                        'sender': {'id': 'USER_ID'},
                        'recipient': {'id': 'PAGE_ID'},
                        'timestamp': 1457764198246,
                        'account_linking': {
                            'status': 'unlinked',
                        },
                    }],
                }],
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
                'account_linking': {
                    'status': 'unlinked',
                },
            },
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
                'mid': None,
            }
        })

    @inlineCallbacks
    def test_inbound_postback_with_referral(self):
        yield self.mk_transport()

        res = yield self.tx_helper.mk_request_raw(
            method='POST',
            data=json.dumps({
                'object': 'page',
                'entry': [{
                    'id': 'PAGE_ID',
                    'time': 1457764198246,
                    'messaging': [{
                        'sender': {'id': 'USER_ID'},
                        'recipient': {'id': 'PAGE_ID'},
                        'timestamp': 1457764198246,
                        'postback': {
                            'payload': json.dumps({"payload": "here"}),
                            'referral': {
                                'ref': 'REFERRAL_DATA',
                                'source': 'SHORTLINK',
                                'type': 'OPEN_THREAD',
                            },
                        },
                    }],
                }],
            }))

        self.assertEqual(res.code, http.OK)

        postback, ref = yield self.tx_helper.wait_for_dispatched_inbound(2)

        self.assertEqual(postback['from_addr'], 'USER_ID')
        self.assertEqual(postback['to_addr'], 'PAGE_ID')
        self.assertEqual(postback['content'], '')
        self.assertEqual(postback['transport_metadata'], {
            'messenger': {
                'mid': None,
                "payload": "here",
            },
        })

        self.assertEqual(ref['from_addr'], 'USER_ID')
        self.assertEqual(ref['to_addr'], 'PAGE_ID')
        self.assertEqual(ref['content'], '')
        self.assertEqual(ref['transport_metadata'], {
            'messenger': {
                'mid': None,
                'referral': {
                    'ref': 'REFERRAL_DATA',
                    'source': 'SHORTLINK',
                    'type': 'OPEN_THREAD',
                },
            },
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
    def test_sender_action(self):
        transport = yield self.mk_transport(access_token='access_token')

        d = self.tx_helper.make_dispatch_outbound(
            from_addr='456',
            to_addr='+123',
            content=None,
            helper_metadata={'messenger': {'sender_action': 'typing_on'}})

        (request_d, args, kwargs) = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'access_token')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        sender_action = req_body['sender_action'][0]
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(sender_action, 'typing_on')
        self.assertEqual(recipient, {'id': '+123'})

        request_d.callback(DummyResponse(200, json.dumps([
            {
                'code': 200,
                'body': {'recipient_id': 'the-recipient-id'},
            },
        ])))

        yield d

    @inlineCallbacks
    def test_good_outbound(self):
        transport = yield self.mk_transport(access_token='access_token')

        d = self.tx_helper.make_dispatch_outbound(
            from_addr='456',
            to_addr='+123',
            content='hi')

        (request_d, args, kwargs) = yield transport.request_queue.get()
        method, url, data = args
        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'access_token')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {'text': 'hi'})
        self.assertEqual(recipient, {'id': '+123'})

        request_d.callback(DummyResponse(200, json.dumps([
            {
                'code': 200,
                'body': json.dumps({
                    'message_id': 'the-message-id',
                }),
            },
        ])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'the-message-id')

    @inlineCallbacks
    def test_bad_outbound(self):
        transport = yield self.mk_transport(access_token='access_token',
                                            request_batch_wait_time=0.00001)

        d = self.tx_helper.make_dispatch_outbound(
            from_addr='456',
            to_addr='+123',
            content='bye')

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        # We send a 200 response because we only want to emulate a single
        # request failing - the batch request should succeed
        request_d.callback(DummyResponse(200, json.dumps([
            {
                'code': 401,
                'body': json.dumps({
                    'error': {
                        'code': 10,
                        'message': 'not authourized'
                    },
                }),
            },
        ])))

        msg = yield d
        yield self.assert_outbound_failure(
            msg['message_id'], 'not authourized',
            'application_does_not_have_permissions')

    @inlineCallbacks
    def test_outbound_text_message(self):
        transport = yield self.mk_transport(access_token='TOKEN')

        d = self.tx_helper.make_dispatch_outbound(
            to_addr='USER_ID',
            content='Hello, world!',
            helper_metadata={'messenger': {
                'quick_replies': [{
                    'content_type': 'text',
                    'title': 'OPTION',
                    'payload': 'PAYLOAD',
                }],
                'notification_type': 'REGULAR',
                'metadata': 'METADATA',
            }})

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'TOKEN')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {
            'text': 'Hello, world!',
            'metadata': 'METADATA',
            'quick_replies': [{
                'content_type': 'text',
                'title': 'OPTION',
                'payload': 'PAYLOAD',
            }],
        })
        self.assertEqual(recipient, {'id': 'USER_ID'})

        request_d.callback(DummyResponse(200, json.dumps([{
            'code': 200,
            'body': json.dumps({'message_id': 'MESSAGE_ID'}),
        }])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'MESSAGE_ID')

    @inlineCallbacks
    def test_outbound_media_message(self):
        transport = yield self.mk_transport(access_token='TOKEN')

        d = self.tx_helper.make_dispatch_outbound(
            to_addr='USER_ID',
            content=None,
            helper_metadata={'messenger': {
                'attachment': {
                    'type': 'image',
                    'payload': {
                        'url': 'https://example.com/image.jpg',
                        'is_reusable': True,
                    }
                }
            }})

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'TOKEN')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {
            'attachment': {
                'type': 'image',
                'payload': {
                    'url': 'https://example.com/image.jpg',
                    'is_reusable': True,
                },
            },
        })
        self.assertEqual(recipient, {'id': 'USER_ID'})

        request_d.callback(DummyResponse(200, json.dumps([{
            'code': 200,
            'body': json.dumps({
                'message_id': 'MESSAGE_ID',
                'attachment_id': 'ATTACHMENT_ID',
            }),
        }])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'MESSAGE_ID')

    @inlineCallbacks
    def test_outbound_generic_message(self):
        transport = yield self.mk_transport(access_token='TOKEN')

        d = self.tx_helper.make_dispatch_outbound(
            to_addr='USER_ID',
            content='',
            helper_metadata={'messenger': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'generic',
                        'image_aspect_ratio': 'horizontal',
                        'elements': [{
                            'title': 'TITLE',
                            'image_url': 'https://example.com/image.jpg',
                            'default_action': {
                                'type': 'web_url',
                                'url': 'https://example.com/do_thing',
                            },
                            'buttons': [{
                                'type': 'web_url',
                                'title': 'Website',
                                'url': 'https://example.com',
                            }],
                        }],
                    },
                },
            }})

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'TOKEN')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {
            'attachment': {
                'type': 'template',
                'payload': {
                    'template_type': 'generic',
                    'image_aspect_ratio': 'horizontal',
                    'elements': [{
                        'title': 'TITLE',
                        'image_url': 'https://example.com/image.jpg',
                        'default_action': {
                            'type': 'web_url',
                            'url': 'https://example.com/do_thing',
                        },
                        'buttons': [{
                            'type': 'web_url',
                            'title': 'Website',
                            'url': 'https://example.com',
                        }],
                    }],
                },
            },
        })
        self.assertEqual(recipient, {'id': 'USER_ID'})

        request_d.callback(DummyResponse(200, json.dumps([{
            'code': 200,
            'body': json.dumps({'message_id': 'MESSAGE_ID'})
        }])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'MESSAGE_ID')

    @inlineCallbacks
    def test_outbound_list_message(self):
        transport = yield self.mk_transport(access_token='TOKEN')

        d = self.tx_helper.make_dispatch_outbound(
            to_addr='USER_ID',
            content=None,
            helper_metadata={'messenger': {
                'quick_replies': [{
                    'content_type': 'location',
                }],
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'list',
                        'top_element_style': 'compact',
                        'elements': [{
                            'title': 'ELEMENT',
                            'image_url': 'https://example.com/image.jpg',
                            'default_action': {
                                'type': 'web_url',
                                'url': 'https://example.com',
                            },
                            'buttons': [{
                                'type': 'postback',
                                'title': 'Postback button',
                                'payload': 'PAYLOAD',
                            }],
                        }],
                        'buttons': [{
                            'type': 'postback',
                            'title': 'Postback button',
                            'payload': 'PAYLOAD',
                        }],
                    },
                },
            }})

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'TOKEN')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {
            'attachment': {
                'type': 'template',
                'payload': {
                    'template_type': 'list',
                    'top_element_style': 'compact',
                    'elements': [{
                        'title': 'ELEMENT',
                        'image_url': 'https://example.com/image.jpg',
                        'default_action': {
                            'type': 'web_url',
                            'url': 'https://example.com',
                        },
                        'buttons': [{
                            'type': 'postback',
                            'title': 'Postback button',
                            'payload': 'PAYLOAD',
                        }],
                    }],
                    'buttons': [{
                        'type': 'postback',
                        'title': 'Postback button',
                        'payload': 'PAYLOAD',
                    }],
                },
            },
            'quick_replies': [{'content_type': 'location'}]
        })
        self.assertEqual(recipient, {'id': 'USER_ID'})

        request_d.callback(DummyResponse(200, json.dumps([{
            'code': 200,
            'body': json.dumps({'message_id': 'MESSAGE_ID'}),
        }])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'MESSAGE_ID')

    @inlineCallbacks
    def test_outbound_receipt_message(self):
        transport = yield self.mk_transport(access_token='TOKEN')

        d = self.tx_helper.make_dispatch_outbound(
            to_addr='USER_ID',
            content=None,
            helper_metadata={'messenger': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'receipt',
                        'recipient_name': 'John Doe',
                        'order_number': '1234',
                        'currency': 'ZAR',
                        'payment_method': 'VISA',
                        'order_url': 'https://example.com/order',
                        'timestamp': '1428444852',
                        'elements': [{
                            'title': 'Item',
                            'quantity': 2,
                            'price': 40,
                            'currency': 'ZAR',
                            'image_url': 'https://example.com/image.jpg',
                        }],
                        'address': {
                            'street_1': '44 Stanley',
                            'street_2': '',
                            'city': 'Johannesburg',
                            'postal_code': '1234',
                            'state': 'Gauteng',
                            'country': 'RSA',
                        },
                        'summary': {
                            'subtotal': 40,
                            'shipping_cost': 20.40,
                            'total_tax': 0.60,
                            'total_cost': 61,
                        },
                        'adjustments': [{
                            'name': 'Discount',
                            'amount': 10,
                        }],
                    },
                },
            }})

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'TOKEN')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {
            'attachment': {
                'type': 'template',
                'payload': {
                    'template_type': 'receipt',
                    'recipient_name': 'John Doe',
                    'order_number': '1234',
                    'currency': 'ZAR',
                    'payment_method': 'VISA',
                    'order_url': 'https://example.com/order',
                    'timestamp': '1428444852',
                    'elements': [{
                        'title': 'Item',
                        'quantity': 2,
                        'price': 40,
                        'currency': 'ZAR',
                        'image_url': 'https://example.com/image.jpg',
                    }],
                    'address': {
                        'street_1': '44 Stanley',
                        'street_2': '',
                        'city': 'Johannesburg',
                        'postal_code': '1234',
                        'state': 'Gauteng',
                        'country': 'RSA',
                    },
                    'summary': {
                        'subtotal': 40,
                        'shipping_cost': 20.40,
                        'total_tax': 0.60,
                        'total_cost': 61,
                    },
                    'adjustments': [{
                        'name': 'Discount',
                        'amount': 10,
                    }],
                },
            }
        })
        self.assertEqual(recipient, {'id': 'USER_ID'})

        request_d.callback(DummyResponse(200, json.dumps([{
            'code': 200,
            'body': json.dumps({'message_id': 'MESSAGE_ID'}),
        }])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'MESSAGE_ID')

    @inlineCallbacks
    def test_outbound_button_message(self):
        transport = yield self.mk_transport(access_token='TOKEN')

        d = self.tx_helper.make_dispatch_outbound(
            to_addr='USER_ID',
            content=None,
            helper_metadata={'messenger': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'button',
                        'text': 'Buttons!',
                        'buttons': [
                            {
                                'type': 'postback',
                                'title': 'Postback',
                                'payload': 'PAYLOAD',
                            },
                            {
                                'type': 'web_url',
                                'title': 'Website',
                                'url': 'https://example.com',
                            },
                        ],
                    },
                },
            }})

        request_d, args, kwargs = yield transport.request_queue.get()
        method, url, data = args

        self.assertEqual(data['include_headers'], 'false')
        self.assertEqual(data['access_token'], 'TOKEN')

        req_body = parse_qs(json.loads(data['batch'])[0]['body'])
        message = json.loads(req_body['message'][0])
        recipient = json.loads(req_body['recipient'][0])

        self.assertEqual(message, {
            'attachment': {
                'type': 'template',
                'payload': {
                    'template_type': 'button',
                    'text': 'Buttons!',
                    'buttons': [
                        {
                            'type': 'postback',
                            'title': 'Postback',
                            'payload': 'PAYLOAD',
                        },
                        {
                            'type': 'web_url',
                            'title': 'Website',
                            'url': 'https://example.com',
                        },
                    ],
                },
            },
        })
        self.assertEqual(recipient, {'id': 'USER_ID'})

        request_d.callback(DummyResponse(200, json.dumps([{
            'code': 200,
            'body': json.dumps({'message_id': 'MESSAGE_ID'}),
        }])))

        msg = yield d
        yield self.assert_outbound_success(msg['message_id'], 'MESSAGE_ID')

    @inlineCallbacks
    def test_handle_outbound_success(self):
        transport = yield self.mk_transport()
        yield transport.handle_outbound_success('1', '2')
        yield self.assert_outbound_success('1', '2')

    @inlineCallbacks
    def test_handle_outbound_failure(self):
        transport = yield self.mk_transport()
        yield transport.handle_outbound_failure('1', 'fail', 'status')
        yield self.assert_outbound_failure('1', 'fail', 'status')

    @inlineCallbacks
    def assert_outbound_success(self, user_message_id, sent_message_id):
        [ack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assertEqual(ack['user_message_id'], user_message_id)
        self.assertEqual(ack['sent_message_id'], sent_message_id)

        [status] = self.tx_helper.get_dispatched_statuses()
        self.assertEqual(status['status'], 'ok')
        self.assertEqual(status['component'], 'outbound')
        self.assertEqual(status['type'], 'request_success')
        self.assertEqual(status['message'], 'Request successful')

    @inlineCallbacks
    def assert_outbound_failure(self, message_id, reason, status_type):
        [nack] = yield self.tx_helper.wait_for_dispatched_events(1)
        self.assertEqual(nack['user_message_id'], message_id)
        self.assertEqual(nack['sent_message_id'], message_id)

        [status] = self.tx_helper.get_dispatched_statuses()
        self.assertEqual(status['status'], 'down')
        self.assertEqual(status['component'], 'outbound')
        self.assertEqual(status['type'], status_type)
        self.assertEqual(status['message'], reason)

import json

from vumi.tests.helpers import VumiTestCase
from vumi.tests.utils import MockHttpServer

from twisted.internet import reactor
from twisted.internet.defer import (
    inlineCallbacks, returnValue, DeferredQueue, Deferred)
from twisted.internet.task import Clock
from twisted.web import http
from twisted.web.client import HTTPConnectionPool

from vxmessenger.transport import MessengerTransport
from vumi.transports.httprpc.tests.helpers import HttpRpcTransportHelper

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

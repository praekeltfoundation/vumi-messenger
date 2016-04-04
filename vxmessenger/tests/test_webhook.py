from urllib import urlencode

from twisted.trial.unittest import TestCase
from twisted.internet.defer import inlineCallbacks
from twisted.internet.endpoints import serverFromString
from twisted.internet import reactor
from twisted.web.client import HTTPConnectionPool
from twisted.web.server import Site

import treq

from vxmessenger.webhook import WebhookService


class TestWebhookService(TestCase):

    @inlineCallbacks
    def setUp(self):
        self.ws = WebhookService('token')

        endpoint = serverFromString(reactor, 'tcp:0')
        listener = yield endpoint.listen(Site(self.ws.app.resource()))

        port = listener.getHost().port
        self.url = 'http://127.0.0.1:%s/' % (port,)
        self.addCleanup(listener.loseConnection)

        # cleanup stuff for treq's global http request pool
        self.pool = HTTPConnectionPool(reactor, persistent=False)
        self.addCleanup(self.pool.closeCachedConnections)

    @inlineCallbacks
    def test_success(self):
        response = yield treq.get('%s/?%s' % (self.url, urlencode({
            'hub.mode': 'subscribe',
            'hub.challenge': 'challenge',
            'hub.verify_token': 'token',
        })), pool=self.pool)
        self.assertEqual((yield response.content()), 'challenge')

    @inlineCallbacks
    def test_token_fail(self):
        response = yield treq.get('%s/?%s' % (self.url, urlencode({
            'hub.mode': 'subscribe',
            'hub.challenge': 'challenge',
            'hub.verify_token': 'foo',
        })), pool=self.pool)
        self.assertEqual((yield response.content()), 'Bad Request')

    @inlineCallbacks
    def test_mode_fail(self):
        response = yield treq.get('%s/?%s' % (self.url, urlencode({
            'hub.mode': 'foo',
            'hub.challenge': 'challenge',
            'hub.verify_token': 'token',
        })), pool=self.pool)
        self.assertEqual((yield response.content()), 'Bad Request')

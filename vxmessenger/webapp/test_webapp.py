from urllib import urlencode
from django.test import TestCase
from django.core.urlresolvers import reverse


class DefaultTestCase(TestCase):

    def test_challenge(self):
        response = self.client.get(
            '%s?%s' % (reverse('challenge'), urlencode({
                'hub.challenge': 'challenge',
                'hub.verify_token': 'token',
                'hub.mode': 'subscribe',
            })))
        self.assertEqual(response.content, 'challenge')

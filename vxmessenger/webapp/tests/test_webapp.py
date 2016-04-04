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

    def test_privacy(self):
        response = self.client.get(reverse('privacy'))
        self.assertTemplateUsed(response, 'privacy.html')

    def test_home(self):
        response = self.client.get(reverse('home'))
        self.assertTemplateUsed(response, 'home.html')

from django.test import TestCase, override_settings
from vxmessenger.webapp.context_processors import constants


class TeseContextProcessors(TestCase):

    @override_settings(FB_APP_ID='foo', FB_PAGE_ID='bar')
    def test_context_processors(self):
        self.assertEqual(constants({}), {
            "FB_APP_ID": 'foo',
            "FB_PAGE_ID": 'bar',
        })

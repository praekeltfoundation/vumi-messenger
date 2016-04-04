from django.conf import settings


def constants(context):
    return {
        'FB_APP_ID': settings.FB_APP_ID,
        'FB_PAGE_ID': settings.FB_PAGE_ID,
    }

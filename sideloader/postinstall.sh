export DJANGO_SETTINGS_MODULE=vxmessenger.webapp.settings
django-admin migrate --no-input
django-admin collectstatic --no-input

# Post install scripts for sideloader
${PIP} install -e $INSTALLDIR/$NAME

export DJANGO_SETTINGS_MODULE=vxmessenger.webapp.settings
django-admin migrate --no-input
django-admin collectstatic --no-input

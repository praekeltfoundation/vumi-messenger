#!/bin/bash -e

JUNEBUG_INTERFACE=${JUNEBUG_INTERFACE:-0.0.0.0}
JUNEBUG_PORT=${JUNEBUG_PORT:-8080}
REDIS_HOST=${REDIS_HOST:-127.0.0.1}
REDIS_PORT=${REDIS_PORT:-6379}
REDIS_DB=${REDIS_DB:-1}
AMQP_HOST=${AMQP_HOST:-127.0.0.1}
AMQP_VHOST=${AMQP_VHOST:-/guest}
AMQP_PORT=${AMQP_PORT:-5672}
AMQP_USER=${AMQP_USER:-guest}
AMQP_PASSWORD=${AMQP_PASSWORD:-guest}

echo "Starting Junebug with redis://$REDIS_HOST:$REDIS_PORT/$REDIS_DB and amqp://$AMQP_USER:$AMQP_PASSWORD@$AMQP_HOST:$AMQP_PORT/$AMQP_VHOST"

exec jb \
    --interface $JUNEBUG_INTERFACE \
    --port $JUNEBUG_PORT \
    --redis-host $REDIS_HOST \
    --redis-port $REDIS_PORT \
    --redis-db $REDIS_DB \
    --amqp-host $AMQP_HOST \
    --amqp-vhost $AMQP_VHOST \
    --amqp-port $AMQP_PORT \
    --amqp-user $AMQP_USER \
    --amqp-password $AMQP_PASSWORD \
    --channels whatsapp:vxyowsup.whatsapp.WhatsAppTransport \
    --channels vumigo:vumi.transports.vumi_bridge.GoConversationTransport \
    --channels facebook:vxmessenger.transport.MessengerTransport \
    --plugin '{"type": "junebug.plugins.nginx.NginxPlugin", "server_name": "_", "vhost_template": "/config/vhost.template"}' \
    --logging-path .

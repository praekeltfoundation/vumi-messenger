from junebug.plugins.nginx import NginxPlugin
from junebug.utils import channel_public_http_properties

from twisted.python import log


class MessengerPlugin(NginxPlugin):

    def start_plugin(self, config, junebug_config):
        log.msg('Starting plugin: %s, %s.' % (
            config, junebug_config._config_data))
        return super(MessengerPlugin, self).start_plugin(
            config, junebug_config)

    def stop_plugin(self):
        log.msg('Stopping plugin.')
        return super(MessengerPlugin, self).stop_plugin()

    def channel_started(self, channel):
        log.msg('Channel started: %s, %s from %s' % (
            channel.id, channel_public_http_properties(channel._properties),
            channel._properties))
        return super(MessengerPlugin, self).channel_started(channel)

    def channel_stopped(self, channel):
        log.msg('Channel stopped: %s' % (channel.id,))
        return super(MessengerPlugin, self).channel_stopped(channel)

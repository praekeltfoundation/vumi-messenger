from vumi.transports.httprpc import HttpRpcTransport


class MessengerTransportConfig(HttpRpcTransport.CONFIG_CLASS):
    pass


class MessengerTransport(HttpRpcTransport):

    CONFIG_CLASS = MessengerTransportConfig
    transport_type = 'facebook'

    def setup_transport(self):
        return super(MessengerTransport, self).setup_transport()

    def handle_raw_inbound_message(self, message_id, request):
        pass

    def handle_outbound_message(self, message):
        self.emit("Vas2NetsUssdTransport consuming %r" % (message,))
        pass

    def on_down_response_time(self, message_id, time):
        request = self.get_request(message_id)
        # We send different status events for error responses
        if request.code < 200 or request.code >= 300:
            return
        return self.add_status(
            component='response',
            status='down',
            type='very_slow_response',
            message='Very slow response',
            reasons=[
                'Response took longer than %fs' % (
                    self.response_time_down,)
            ],
            details={
                'response_time': time,
            })

    def on_degraded_response_time(self, message_id, time):
        request = self.get_request(message_id)
        # We send different status events for error responses
        if request.code < 200 or request.code >= 300:
            return
        return self.add_status(
            component='response',
            status='degraded',
            type='slow_response',
            message='Slow response',
            reasons=[
                'Response took longer than %fs' % (
                    self.response_time_degraded,)
            ],
            details={
                'response_time': time,
            })

    def on_good_response_time(self, message_id, time):
        request = self.get_request(message_id)
        # We send different status events for error responses
        if request.code < 200 or request.code >= 400:
            return
        return self.add_status(
            component='response',
            status='ok',
            type='response_sent',
            message='Response sent',
            details={
                'response_time': time,
            })

    def on_timeout(self, message_id, time):
        return self.add_status(
            component='response',
            status='down',
            type='timeout',
            message='Response timed out',
            reasons=[
                'Response took longer than %fs' % (
                    self.request_timeout,)
            ],
            details={
                'response_time': time,
            })

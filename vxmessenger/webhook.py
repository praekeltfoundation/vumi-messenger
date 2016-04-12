from klein import Klein

import click


class WebhookService(object):
    app = Klein()

    def __init__(self, verify_token):
        self.verify_token = verify_token

    @app.route('/', methods=['GET'])
    def items(self, request):
        [mode] = request.args.get('hub.mode')
        [challenge] = request.args.get('hub.challenge')
        [verify_token] = request.args.get('hub.verify_token')
        if (verify_token == self.verify_token and mode == 'subscribe'):
            return challenge
        request.setResponseCode(404)
        return 'Bad Request'


@click.command()
@click.option('--interface', default='127.0.0.1',
              help='Which interface to listen on.', type=str)
@click.option('--port', default=8050,
              help='Which port to listen on.', type=int)
@click.option('--token',
              help='The token to verify', type=str)
def cli(interface, port, token):
    store = WebhookService(token)
    store.app.run(interface, port)

if __name__ == '__main__':
    cli()

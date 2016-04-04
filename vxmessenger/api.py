import sys

from klein import Klein

from twisted.python import log
from twisted.internet import reactor
from twisted.internet.endpoints import serverFromString
from twisted.web.server import Site

import click


class ApiService(object):
    app = Klein()

    def __init__(self):
        pass

    @app.route('/api')
    def items(self, request):
        log.msg('Received request: %r' % (dict(request.args,)))
        log.msg('Headers: %r' % (
            dict(request.requestHeaders.getAllRawHeaders()),))
        log.msg('Received data: %r' % (request.content.read(),))
        return 'hello'


@click.command()
@click.option('--endpoint', default='tcp:8051',
              help='Which endpoint to listen on.', type=str)
@click.option('--logfile',
              help='Where to log output to.',
              type=click.File('a'),
              default=sys.stdout)
def cli(endpoint, logfile):
    log.startLogging(logfile)
    endpoint = serverFromString(reactor, str(endpoint))
    endpoint.listen(Site(ApiService().app.resource()))
    reactor.run()


if __name__ == '__main__':
    cli()

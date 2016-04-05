FROM praekeltfoundation/vumi
MAINTAINER Praekelt Foundation <dev@praekeltfoundation.org>

COPY . /vxmessenger
RUN pip install -e /vxmessenger

ENV WORKER_CLASS vxmessenger.transport.MessengerTransport

EXPOSE 8080

CMD []

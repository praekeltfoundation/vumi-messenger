FROM qa-mesos-persistence.za.prk-host.net:5000/junebug
MAINTAINER Praekelt Foundation <dev@praekeltfoundation.org>

COPY . /vxmessenger
RUN pip install -e /vxmessenger

COPY ./junebug-entrypoint.sh /scripts/
EXPOSE 80

ENTRYPOINT ["eval-args.sh", "dinit", "junebug-entrypoint.sh"]

CMD []

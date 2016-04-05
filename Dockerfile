FROM praekeltfoundation/python-base
MAINTAINER Praekelt Foundation <dev@praekeltfoundation.org>

RUN apt-get-install.sh gcc
RUN apt-get-install.sh python-dev
RUN apt-get-install.sh libjpeg-dev
RUN apt-get-install.sh zlib1g-dev
RUN pip install junebug==0.1.1
RUN pip install vxyowsup==0.1.5
COPY . /vxmessenger
RUN pip install -e /vxmessenger
COPY ./junebug-entrypoint.sh /scripts/
EXPOSE 8080

ENTRYPOINT ["eval-args.sh", "dinit", "junebug-entrypoint.sh"]

CMD []

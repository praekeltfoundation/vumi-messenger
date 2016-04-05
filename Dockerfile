FROM qa-mesos-persistence.za.prk-host.net:5000/junebug

COPY ./junebug-entrypoint.sh /scripts/
COPY . /vxmessenger
RUN pip install -e /vxmessenger

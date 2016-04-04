vxmessenger
=============================

.. image:: https://img.shields.io/travis/praekeltfoundation/vxmessenger.svg
        :target: https://travis-ci.org/praekeltfoundation/vxmessenger

.. image:: https://img.shields.io/pypi/v/vxmessenger.svg
        :target: https://pypi.python.org/pypi/vxmessenger

.. image:: https://coveralls.io/repos/praekeltfoundation/vxmessenger/badge.png?branch=develop
    :target: https://coveralls.io/r/praekeltfoundation/vxmessenger?branch=develop
    :alt: Code Coverage

.. image:: https://readthedocs.org/projects/vxmessenger/badge/?version=latest
    :target: https://vxmessenger.readthedocs.org
    :alt: vxmessenger Docs

WebhookService
--------------

A simple service to respond to Facebook's callbacks when setting up a
messenger application's webhooks.

    $ python -m vxmessenger.webhook --port 8050 --token mytoken

It'll respond to the initial HTTP call Facebook makes in order to get going.

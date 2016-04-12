Vumi Messenger Transport
========================

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

All of Vumi's applications can be surfaced on Messenger with the Messenger Transport.
It provides a great experience for interactive mobile conversations at scale.


Getting Started
===============

Install Junebug_, the standalone Vumi transport launcher and the Vumi Transport::

    $ apt-get install redis-server rabbitmq-server
    $ pip install junebug
    $ pip install vumi-messenger

Launch the Junebug service with thet Vumi Messenger channel configured::

    $ jb -p 8000 \
        --channels facebook:vxmessenger.transport.MessengerTransport \
        --logging-path .

Using the template, below and update your FB_APP_ID, FB_ACCESS_TOKEN and
save it as a file called ``config.json``:

.. code-block:: json

    {
      "type": "facebook",
      "amqp_queue": "messenger_transport",
      "public_http": {
        "enabled": true,
        "web_path": "/api",
        "web_port": 8051
      },
      "config": {
        "web_path": "/api",
        "web_port": 8051,
        "noisy": true,
        "app_id": "YOUR_FB_APP_ID",
        "retrieve_profile": true,
        "welcome_message": [{
          "message": {
            "text": "Hi :) Welcome to our Messenger Bot!"
          }
        }],
        "outbound_url": "https://graph.facebook.com/v2.5/me/messages",
        "access_token": "YOUR_FB_ACCESS_TOKEN"
      }
    }

Post it to Junebug to start the channel::

    $ curl -X POST -d@config.json http://localhost:8000/channels/

You're now able to communicate with Facebook's Messenger API and can offer
your Vumi application there.

How about hooking up a simple game of hangman?::

    $ twistd -n vumi_worker \
        --worker-class=vumi.demos.hangman.HangmanWorker \
        --set-option=random_word_url:http://randomword.setgetgo.com/get.php \
        --set-option=transport_name:messenger_transport \
        --set-option=worker_name:hangman


Richer Templates
================

The Vumi Messenger Transport allows one to use the richer templates available,
including texts, images, hyperlinks and buttons.

To make use of these add the relevant ``helper_metadata`` to your outbound
Vumi message:

A Button Reply
~~~~~~~~~~~~~~

Please be aware of the limitations_ that Facebook applies to these messages.
A call to action may only have a maximum of 3 buttons and character count
limits appy.

.. code-block:: python

    self.publish_message(
        helper_metadata={
            'messenger': {
                'template_type': 'button'
                'text': 'The accompanying text with the button',
                'buttons': [{
                    'title': 'Button 1',
                    'payload': {
                        'content': 'The content expected when a button is pressed',
                        'in_reply_to': 'The ID of the previous message' # This can be left blank
                    }
                }]
            }
        })

A Generic Reply
~~~~~~~~~~~~~~

Please be aware of the limitations_ that Facebook applies to these messages.
A call to action may only have a maximum of 3 buttons and character count
limits appy.

.. code-block:: python

    self.publish_message(
        helper_metadata={
            'messenger': {
                'template_type': 'generic'
                'title': 'The title',
                'subtitle': 'The subtitle',
                'image_url': 'The image_url to use', # This can be left blank
                'buttons': [{
                    'title': 'Button 1',
                    'payload': {
                        'content': 'The content expected when a button is pressed',
                        'in_reply_to': 'The ID of the previous message' # This can be left blank
                    }
                }]
            }
        })

.. _Junebug: http://junebug.readthedocs.org
.. _limitations: https://developers.facebook.com/docs/messenger-platform/send-api-reference#guidelines

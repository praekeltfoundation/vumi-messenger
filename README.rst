Vumi Messenger Transport
========================

.. image:: https://img.shields.io/travis/praekeltfoundation/vumi-messenger.svg
        :target: https://travis-ci.org/praekeltfoundation/vumi-messenger

.. image:: https://img.shields.io/pypi/v/vxmessenger.svg
        :target: https://pypi.python.org/pypi/vxmessenger

.. image:: https://coveralls.io/repos/praekeltfoundation/vumi-messenger/badge.png?branch=develop
    :target: https://coveralls.io/r/praekeltfoundation/vumi-messenger?branch=develop
    :alt: Code Coverage

.. image:: https://readthedocs.org/projects/vumi-facebook-messenger/badge/?version=latest
    :target: http://vumi-facebook-messenger.readthedocs.org/
    :alt: vxmessenger Docs

All of Vumi's applications can be surfaced on Messenger with the Messenger Transport.
It provides a great experience for interactive mobile conversations at scale.

Note on compatibility
=====================

Please note that breaking changes to the transport interface were introduced in version 2.0.0.
If your setup uses the old interface, you should use a previous version (1.2.6 or earlier), as the changes are not backwards compatible.


Getting Started
===============

Install Junebug_, the standalone Vumi transport launcher and the Facebook Messenger Transport::

    $ apt-get install redis-server rabbitmq-server
    $ pip install junebug
    $ pip install vxmessenger

Launch the Junebug service with thet Vumi Messenger channel configured::

    $ jb -p 8000 \
        --channels facebook:vxmessenger.transport.MessengerTransport \
        --logging-path .

Using the template, below and update your FB_PAGE_ID, FB_ACCESS_TOKEN and
save it as a file called ``config.json``:

.. code-block:: json

    {
        "type": "facebook",
        "label": "Sample Facebook Channel",
        "mo_url": "http://127.0.0.1:8000/channel/message",
        "status_url": "http://127.0.0.1:8000/channel/status",
        "public_http": {
            "enabled": true,
            "web_path": "/api",
            "web_port": 8051
        },
        "config": {
            "web_path": "/api",
            "web_port": 8051,
            "noisy": true,
            "page_id": "YOUR_FB_PAGE_ID",
            "retrieve_profile": false,
            "outbound_url": "https://graph.facebook.com/v2.8/me/messages",
            "access_token": "YOUR_FB_PAGE_ID",
            "redis_manager": {
        },
            "request_batch_size": 20,
            "request_batch_wait_time": 0.1
        }
    }

The Messenger transport makes use of Facebook's Batch API, passing instructions
for multiple operations in a single HTTP request. The number of operations to
send in each request is set in the ``request_batch_size`` field, while the number
of seconds to wait between requests is set in the ``request_batch_wait_time`` field.
The batch size has a maximum of 50 (also the default), and the wait time defaults to
0.1 seconds.

The transport makes use of Redis to queue requests before sending them. If you'd like
to customize your Redis settings (eg. the port number), pass your desired configuration
as a JSON object in the ``redis_manager`` field. Passing an empty object causes the
transport to use the default configuration.

Post the config to Junebug to start the channel::

    $ curl -X POST -d@config.json http://localhost:8000/channels/

You're now able to communicate with Facebook's Messenger API and can offer
bot access to your Vumi application.

Facebook will want to verify your application, for that to work make sure it's served over SSL.
The API URL is::

    http://localhost:8051/api

If you've used a different ``web_port`` and ``web_path`` parameter you'll need to update the URL accordingly.

.. note::

    There is also a Dockerfile available that you can customise to run
    Junebug in a Docker container: http://github.com/praekeltfoundation/docker-junebug

    The Docker container includes Nginx and offers the Junebug_ API under the
    ``/jb/`` endpoint, all other transports are made available from the root path.
    For the example above the endpoint would be ``/api`` on port 80.


Hook up an Application to your Messenger integration
====================================================

All Vumi applications can be surfaced on Facebook Messenger as bots, how about
hooking up a simple game of hangman?::

    $ twistd -n vumi_worker \
        --worker-class=vumi.demos.hangman.HangmanWorker \
        --set-option=random_word_url:http://randomword.setgetgo.com/get.php \
        --set-option=transport_name:messenger_transport \
        --set-option=worker_name:hangman

Javascript Sandbox applications are also available.
Check out some of the examples below:

Sample FAQ browser
    https://github.com/smn/faqbrowser-docker

Sample Service rating application
    https://github.com/smn/servicerating-docker

.. note::

    Do you want to expose multiple applications within a single Bot?
    The Vumi Application Router allows you to do exactly that, have a look
    at the `example router specifically for Facebook Messenger <https://github.com/smn/vumi-app-router>`_.

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
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'button'
                        'text': 'The accompanying text with the button',
                        'buttons': [{ # Up to 3 buttons
                            'type': 'postback', # defaults to postback if not specified
                            'title': 'Button 1',
                            'payload': json.dumps({
                                # In here you can put whatever you want to
                                # 'content' and 'in_reply_to' will go into the standard vumi message
                                'content': 'The content expected when a button is pressed', # This can be left blank
                                'in_reply_to': 'The ID of the previous message', # This can be left blank
                                # Anything else will end up in transport_metadata.messenger and helper_metadata.messenger
                                'anything_extra': 'Bonus!'
                            })
                        }, {
                            'type': 'web_url',
                            'title': 'Button 2',
                            'url': 'http://some.url'
                        }, {
                            'type': 'phone_number',
                            'title': 'Button 3',
                            'url': '+271234567'
                        }]
                    }
                }
            }
        })

A Generic Reply
~~~~~~~~~~~~~~~

Please be aware of the limitations_ that Facebook applies to these messages.
A call to action may only have a maximum of 3 buttons, 10 elements, and character count
limits appy.

.. code-block:: python

    self.publish_message(
        helper_metadata={
            'messenger': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'generic'
                        'elements': [{ # Up to 10 elements
                            'title': 'The title',
                            'subtitle': 'The subtitle', # This can be left blank
                            'image_url': 'The image_url to use', # This can be left blank
                            'item_url': 'The url if you click on the element', # Optional, Cannot be used with default_action
                            'default_action': { # The action if you click on element, optional, Cannot be user with item_url
                                'url': 'http://test',
                                'webview_height_ratio': 'compact|tall|full',  # Leave out for regular link
                                'messenger_extensions': True,  # If you are using the js extensions api, optional
                                'fallback_url': 'http://moo' # Fallback URL if target doesn't support js extensions
                                                         # will use url if not found, optional
                            },
                            'buttons': [{ # Up to 3 buttons
                                'type': 'postback', # defaults to postback if not specified
                                'title': 'Button 1',
                                'payload': json.dumps({
                                    # In here you can put whatever you want to
                                    # 'content' and 'in_reply_to' will go into the standard vumi message
                                    'content': 'The content expected when a button is pressed', # This can be left blank
                                    'in_reply_to': 'The ID of the previous message', # This can be left blank
                                    # Anything else will end up in transport_metadata.messenger and helper_metadata.messenger
                                    'anything_extra': 'Bonus!'
                                })
                            }, {
                                'type': 'web_url',
                                'title': 'Button 2',
                                'url': 'http://some.url'
                            }, {
                                'type': 'phone_number',
                                'title': 'Button 3',
                                'url': '+271234567'
                            }, {
                                # Share this post with friends. Only available in Generic Reply
                                'type': 'element_share'
                            }]
                        }]
                    }
                }
            }
        })

A List Reply
~~~~~~~~~~~~~~~

Please be aware of the limitations_ that Facebook applies to these messages.
A call to action may only have a maximum of 1 button per element, 2-4 elements, 1 extra button, and character count
limits appy.

.. code-block:: python

    self.publish_message(
        helper_metadata={
            'messenger': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'list'
                        'top_element_style': 'compact|large', # Defines top element style
                                                              # Defaults to compact if not specified
                        'elements': [{ # 2 - 4 elements
                            'title': 'The title',
                            'subtitle': 'The subtitle', # This can be left blank
                            'image_url': 'The image_url to use', # This can be left blank
                            'default_action': { # The action if you click on element, optional
                                'url': 'http://test',
                                'webview_height_ratio': 'compact|tall|full',  # Leave out for regular link, optional
                                'messenger_extensions': True,  # If you are using the js extensions api, optional
                                'fallback_url': 'http://moo' # Fallback URL if target doesn't support js extensions
                                                         # will use url if not found, optional
                            },
                            'buttons': [{ # Up to 1 button
                                'type': 'postback', # defaults to postback if not specified
                                'title': 'Button 1',
                                'payload': json.dumps({
                                    # In here you can put whatever you want to
                                    # 'content' and 'in_reply_to' will go into the standard vumi message
                                    'content': 'The content expected when a button is pressed', # This can be left blank
                                    'in_reply_to': 'The ID of the previous message', # This can be left blank
                                    # Anything else will end up in transport_metadata.messenger and helper_metadata.messenger
                                    'anything_extra': 'Bonus!'
                                })
                            }, {
                                'type': 'web_url',
                                'title': 'Button 2',
                                'url': 'http://some.url'
                            }, {
                                'type': 'phone_number',
                                'title': 'Button 3',
                                'url': '+271234567'
                            }]
                        }],
                            'buttons': [{ # Up to 1 button at bottom of list
                            ...
                        }]
                    }
                }
            }
        })

A Receipt Reply
~~~~~~~~~~~~~~~

Please be aware of the limitations_ that Facebook applies to these messages.

.. code-block:: python

    self.publish_message(
        helper_metadata={
            'messenger': {
                'attachment': {
                    'type': 'template',
                    'payload': {
                        'template_type': 'receipt',
                        'order_number': '123', # Must be unique
                        'currency' 'ZAR'
                        'payment_method': 'EFT',
                        'recipient_name': 'John Doe',
                        'summary': {
                            'total_cost': 100.0,
                            # Optional fields
                            'subtotal': 40.0,
                            'total_tax': 10.0,
                            'shipping_cost': 10.00,
                        },
                        # Optional fields
                        'merchant_name': 'XYZ Inc.'
                        'order_url': 'http://example.com',
                        'timestamp': '12345678',
                        'elements': [
                            # Up to 100 elements
                            {
                                'title': 'element title',
                                'price': 10
                                # Optional fields
                                'subtitle': 'element subtitle',
                                'quantity': 1,
                                'currency': 'ZAR',
                                'image_url': 'http://example.com',
                            },
                        ],
                        'adjustments': [
                            {
                                'name': 'discount',
                                'amount': 1,
                            },
                        ],
                        'address': {
                            'street_1': '1 Main Street',
                            'street_2': 'Suburb' # This field is optional
                            'city': 'Cape Town',
                            'state': 'WC',
                            'country': 'RSA',
                            'postal_code': '1234',
                        },
                    }
                }
            }
        })

Quick replies
~~~~~~~~~~~~~

A Quick Reply is similar to a button reply, but Messenger shows the buttons
temporarily and they disappear after being pressed. You can also have up to
10 buttons. Only ``postback`` buttons is allowed in this mode.

Quick replies can be appended to any message format.

.. code-block:: python

    self.publish_message(
        helper_metadata={
            'messenger': {
                ...
                'quick_replies': [{ # Up to 10 quick replies
                    'type': 'text', # defaults to text if not specified
                    'title': 'Button 1',
                    'image_url': 'The image_url to use', # This can be left blank
                    'payload': json.dumps({
                        # In here you can put whatever you want to
                        # 'content' and 'in_reply_to' will go into the standard vumi message
                        'content': 'The content expected when a button is pressed', # This can be left blank
                        'in_reply_to': 'The ID of the previous message', # This can be left blank
                        # Anything else will end up in transport_metadata.messenger and helper_metadata.messenger
                        'anything_extra': 'Bonus!'
                    })
                }, {
                    'type': 'location',
                }]
            }
        })


Message format
==============

Due to some extra features of the messenger platform, there is some extra data that you may need to pay attention to:

**transport_metadata:**

Contains a dict ``messenger`` with the following keys:

``mid``:
    Messenger message id.

``attachments``:
    List containing dictionaries as such:

    .. code-block:: json

        {
            "type":"image",
            "payload": {
                "url":"IMAGE_URL"
            }
        }

``optin``:
    Dict containing a ``ref`` key, which is the PASS_THROUGH_PARAM as defined by:

    https://developers.facebook.com/docs/messenger-platform/plugin-reference#send_to_messenger

Other items defined in ``payload``:
    e.g. ``"anything_extra": "Bonus"``

**helper_metadata:**

Contains a dict ``messenger`` with the same data as ``transport_metadata`` and the user profile as such:

Note: only if ``retrieve_profile`` is configured as ``true``

.. code-block:: json

    {
        "first_name": "Firstname",
        "last_name": "Lastname",
        "profile_pic": "IMAGE_URL"
    }

Supported webhooks
~~~~~~~~~~~~~~~~~~

``messages``:
    Standard conversational messages & attachments.

``messaging_postbacks``:
    Postback buttons.

``messaging_optins``:
    Send-to-Messenger / authentication callback.


.. _Junebug: http://junebug.readthedocs.org
.. _limitations: https://developers.facebook.com/docs/messenger-platform/send-api-reference#guidelines

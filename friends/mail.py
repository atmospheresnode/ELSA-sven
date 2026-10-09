# -*- coding: utf-8 -*-
"""
Branded account email: sign-in codes, security notices, confirmation links.

Each message is rendered from a pair of templates, <name>.txt and <name>.html,
and sent as multipart so clients that refuse HTML still get a readable body.
"""
from __future__ import unicode_literals

import logging
import os
from email.mime.image import MIMEImage
from functools import lru_cache

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string


logger = logging.getLogger(__name__)

FROM_ADDRESS = 'atm-elsa@nmsu.edu'

EMAIL_LOGO_CID = 'elsa_logo'

# The white logo pre-scaled to 240px (twice its 120px display width), flattened
# onto the teal band and reduced to a 128-colour palette: 7KB against the 70KB
# source.  Built once as a checked-in asset rather than resized on every send,
# so no image processing happens on the sign-in path.
EMAIL_LOGO_PATH = os.path.join(settings.STATIC_DIR, 'images', 'ELSA-Logo-email.png')


@lru_cache(maxsize=1)
def _elsa_logo_png():
    """The email logo asset, read once per process."""
    with open(EMAIL_LOGO_PATH, 'rb') as handle:
        return handle.read()


def _elsa_logo_attachment():
    """
    The logo as an inline MIME part, or None if the asset cannot be read.  A
    missing logo must never stop a sign-in code from going out, so failure
    here is swallowed and the header falls back to its text wordmark.
    """
    try:
        image = MIMEImage(_elsa_logo_png())
    except Exception:
        return None

    image.add_header('Content-ID', '<{0}>'.format(EMAIL_LOGO_CID))
    image.add_header('Content-Disposition', 'inline', filename='elsa-logo.png')
    return image


def build_branded(to, subject, template, context):
    """The message for ``template`` (a path without extension), not yet sent."""
    # Carried with the message rather than linked, so the logo renders without
    # the reader having to approve remote images.
    logo = _elsa_logo_attachment()
    context = dict(context, logo_cid=EMAIL_LOGO_CID if logo else '')

    email = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string(template + '.txt', context),
        from_email=FROM_ADDRESS,
        to=to,
    )
    email.attach_alternative(render_to_string(template + '.html', context), 'text/html')

    if logo:
        # multipart/related tells the client the image belongs to the HTML part
        # rather than being a file the user is meant to download.
        email.mixed_subtype = 'related'
        email.attach(logo)

    return email


def send_branded(to, subject, template, context):
    """
    Sends one branded message and reports whether it went.  Never raises: a
    mail server hiccup must not turn an already-saved change into a 500.
    """
    try:
        build_branded(to, subject, template, context).send()
        return True
    except Exception:
        logger.exception('account email "%s" to %d recipient(s) failed', subject, len(to))
        return False

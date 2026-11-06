# -*- coding: utf-8 -*-
"""Account email: sign-in codes, security notices, confirmation links.

Rendered from <name>.txt and <name>.html and sent as multipart.
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

# White logo scaled to 240px on the teal band with a 128-colour palette, about 7KB.
# A checked-in asset so there's no image processing on sign-in.
EMAIL_LOGO_PATH = os.path.join(settings.STATIC_DIR, 'images', 'ELSA-Logo-email.png')


@lru_cache(maxsize=1)
def _elsa_logo_png():
    """The email logo asset, read once per process."""
    with open(EMAIL_LOGO_PATH, 'rb') as handle:
        return handle.read()


def _elsa_logo_attachment():
    """The logo as an inline MIME part, or None. A missing logo can't stop a sign-in code."""
    try:
        image = MIMEImage(_elsa_logo_png())
    except Exception:
        return None

    image.add_header('Content-ID', '<{0}>'.format(EMAIL_LOGO_CID))
    image.add_header('Content-Disposition', 'inline', filename='elsa-logo.png')
    return image


def build_branded(to, subject, template, context):
    """The message for ``template`` (a path without extension), not yet sent."""
    # Attached so it shows without the reader approving remote images.
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
        # multipart/related marks the image as part of the HTML, not a download.
        email.mixed_subtype = 'related'
        email.attach(logo)

    return email


def send_branded(to, subject, template, context):
    """Send one message and report whether it went. Never raises."""
    try:
        build_branded(to, subject, template, context).send()
        return True
    except Exception:
        logger.exception('account email "%s" to %d recipient(s) failed', subject, len(to))
        return False

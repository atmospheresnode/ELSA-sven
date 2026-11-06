# -*- coding: utf-8 -*-
"""Browser security headers, added by AccountSessionMiddleware.

Every page gets Permissions-Policy and a small Content-Security-Policy that won't break
existing pages. /staff/ and /support/ get a strict policy: scripts only from ELSA, the
CDN files in the base template, and inline blocks with this request's nonce.

HSTS is not set here because it would cover the whole host name, not just ELSA. Set
SECURE_HSTS_SECONDS in settings.py once the whole host is HTTPS-only.
"""
from __future__ import unicode_literals

import secrets

PERMISSIONS_POLICY = 'camera=(), microphone=(), geolocation=(), payment=(), usb=()'

# The third-party files base-derk.html loads. Anything else is refused.
CDN_SCRIPTS = (
    'https://cdn.jsdelivr.net/npm/bootstrap@5.0.2/dist/js/bootstrap.bundle.min.js',
    'https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js',
    'https://cdn.jsdelivr.net/npm/dompurify@3.1.6/dist/purify.min.js',
    'https://use.fontawesome.com/dc496c8662.js',
)
CDN_STYLES = ('https://cdn.jsdelivr.net', 'https://maxcdn.bootstrapcdn.com', 'https://use.fontawesome.com')

STRICT_NAMESPACES = ('staff', 'staff_public')


def new_nonce():
    return secrets.token_urlsafe(18)


def _framing(response):
    option = (response.get('X-Frame-Options') or '').upper()
    if option == 'DENY':
        return "frame-ancestors 'none'"
    if option == 'SAMEORIGIN':
        return "frame-ancestors 'self'"
    return ''


def strict_policy(nonce, framing):
    parts = [
        "default-src 'self'",
        "script-src 'self' 'nonce-{0}' {1}".format(nonce, ' '.join(CDN_SCRIPTS)),
        # Style attributes are used throughout ELSA; scripts are what matter.
        "style-src 'self' 'unsafe-inline' " + ' '.join(CDN_STYLES),
        "font-src 'self' data: " + ' '.join(CDN_STYLES),
        "img-src 'self' data: blob:",
        "connect-src 'self' https://use.fontawesome.com",
        "frame-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    if framing:
        parts.append(framing)
    return '; '.join(parts)


def basic_policy(framing):
    parts = ["object-src 'none'", "base-uri 'self'"]
    if framing:
        parts.append(framing)
    return '; '.join(parts)


def is_strict(request):
    match = getattr(request, 'resolver_match', None)
    return bool(match and set(match.namespaces) & set(STRICT_NAMESPACES))


def apply(request, response):
    """Adds the headers a view has not already set itself."""
    if 'Permissions-Policy' not in response:
        response['Permissions-Policy'] = PERMISSIONS_POLICY
    if 'Content-Security-Policy' not in response:
        framing = _framing(response)
        nonce = getattr(request, 'csp_nonce', '')
        if nonce and is_strict(request):
            response['Content-Security-Policy'] = strict_policy(nonce, framing)
        else:
            response['Content-Security-Policy'] = basic_policy(framing)
    return response

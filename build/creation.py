"""Handles bundle creation so a form sent twice only makes one bundle.

Each form gets a one-time key, and the key that made a bundle is kept in the session.
Sending the same form again goes back to that bundle.
"""
import secrets

from build.models import Bundle

TOKENS_KEY = 'bundle_create_tokens'
JUST_CREATED_KEY = 'bundle_just_created'
KEEP = 20  # open forms per session, older keys get dropped


def issue_token(request):
    """A fresh key for one showing of the create form."""
    token = secrets.token_urlsafe(16)
    tokens = dict(request.session.get(TOKENS_KEY) or {})
    tokens[token] = None
    # Oldest first (dicts keep insertion order); keep the newest few.
    for stale in list(tokens)[:-KEEP]:
        tokens.pop(stale, None)
    request.session[TOKENS_KEY] = tokens
    return token


def already_created(request, token, name):
    """Returns the bundle this form already made, or None. Name has to match too."""
    pk = (request.session.get(TOKENS_KEY) or {}).get(token or '')
    if not pk:
        return None
    bundle = Bundle.objects.filter(pk=pk, user=request.user).first()
    if bundle is None or bundle.name.lower() != (name or '').lower():
        return None
    return bundle


def remember(request, token, bundle):
    tokens = dict(request.session.get(TOKENS_KEY) or {})
    if token in tokens:
        tokens[token] = bundle.pk
        request.session[TOKENS_KEY] = tokens


def mark_just_created(request, bundle, again=False):
    """``again``: the form was sent a second time and led back to this bundle."""
    request.session[JUST_CREATED_KEY] = {'pk': bundle.pk, 'again': bool(again)}


def pop_just_created(request, bundle):
    """Returns {'again': bool} the first time the new bundle's page is shown, else None."""
    data = request.session.get(JUST_CREATED_KEY)
    if not data or data.get('pk') != bundle.pk:
        return None
    request.session.pop(JUST_CREATED_KEY, None)
    return {'again': bool(data.get('again'))}

# -*- coding: utf-8 -*-
"""WebAuthn helpers.

Registration and sign-in each issue options with a challenge, then verify the response.
The challenge stays in the session and is single use.
"""
from __future__ import unicode_literals

import json
import logging

from django.conf import settings

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from .models import Passkey


logger = logging.getLogger(__name__)

RP_NAME = 'ELSA: Educational Labeling System @Atmospheres'

REGISTER_CHALLENGE_KEY = 'webauthn_register_challenge'
AUTH_CHALLENGE_KEY = 'webauthn_auth_challenge'


def relying_party_id(request):
    """The domain the credential is bound to. From the request unless a setting overrides it."""
    configured = getattr(settings, 'WEBAUTHN_RP_ID', None)
    if configured:
        return configured
    return request.get_host().split(':')[0]


def expected_origin(request):
    """Scheme and host as the browser saw it, port included.

    request.scheme says http when TLS ends upstream, so outside DEBUG the scheme is pinned to
    https. WEBAUTHN_ORIGIN overrides this.
    """
    configured = getattr(settings, 'WEBAUTHN_ORIGIN', None)
    if configured:
        return configured

    scheme = 'https' if not settings.DEBUG else request.scheme
    return '{0}://{1}'.format(scheme, request.get_host())


def stash_challenge(request, key, challenge):
    """Parks a challenge for the second half of a ceremony."""
    from webauthn.helpers import bytes_to_base64url
    request.session[key] = bytes_to_base64url(challenge)


def take_challenge(request, key):
    """Pops the pending challenge, so a replayed response cannot verify twice."""
    stored = request.session.pop(key, None)
    if not stored:
        return None
    try:
        return base64url_to_bytes(stored)
    except Exception:
        return None


def registration_options(request, user):
    """Options for adding a passkey. Resident keys are required so sign-in works with no username."""
    options = generate_registration_options(
        rp_id=relying_party_id(request),
        rp_name=RP_NAME,
        user_id=str(user.pk).encode('utf-8'),
        user_name=user.username,
        user_display_name=user.get_full_name() or user.username,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
            for passkey in user.passkeys.all()
        ],
    )

    stash_challenge(request, REGISTER_CHALLENGE_KEY, options.challenge)
    return json.loads(options_to_json(options))


def verify_registration(request, user, credential, label):
    """Checks an enrollment response and stores the credential."""
    challenge = take_challenge(request, REGISTER_CHALLENGE_KEY)
    if not challenge:
        return None, 'Your enrollment session expired. Please try again.'

    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=relying_party_id(request),
            expected_origin=expected_origin(request),
        )
    except Exception:
        # Don't show the user the reason, but log it so a bad origin can be diagnosed.
        logger.warning('passkey registration rejected (rp_id=%s origin=%s)',
                       relying_party_id(request), expected_origin(request),
                       exc_info=True)
        return None, 'That passkey could not be verified. Please try again.'

    from webauthn.helpers import bytes_to_base64url

    credential_id = bytes_to_base64url(verified.credential_id)
    if Passkey.objects.filter(credential_id=credential_id).exists():
        return None, 'That passkey is already registered.'

    transports = credential.get('response', {}).get('transports') or []

    passkey = Passkey.objects.create(
        user=user,
        credential_id=credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=','.join(t for t in transports if isinstance(t, str))[:255],
        name=(label or 'Passkey')[:100],
        backed_up=verified.credential_backed_up,
    )
    return passkey, None


def authentication_options(request, user=None, challenge_key=AUTH_CHALLENGE_KEY):
    """Options for signing in.

    Credential list is left empty on purpose. Nobody has proved who they are yet, so naming
    credentials would show which usernames have passkeys. verify_authentication checks the account.

    Only callers already signed in pass ``user`` (the Admin Console re-check). They also pass
    their own ``challenge_key`` so a re-check can't use up a sign-in challenge in another tab.

    User verification is required so a passkey can count as two factors.
    """
    allow = None
    if user is not None:
        allow = [PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id))
                 for passkey in user.passkeys.all()]

    options = generate_authentication_options(
        rp_id=relying_party_id(request),
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    stash_challenge(request, challenge_key, options.challenge)
    return json.loads(options_to_json(options))


def verify_authentication(request, credential, user=None, challenge_key=AUTH_CHALLENGE_KEY):
    """Check a sign-in response. If ``user`` is given the credential has to be theirs."""
    challenge = take_challenge(request, challenge_key)
    if not challenge:
        return None, 'Your sign-in session expired. Please try again.'

    raw_id = credential.get('rawId') or credential.get('id')
    if not raw_id:
        return None, 'That passkey could not be verified. Please try again.'

    try:
        passkey = Passkey.objects.select_related('user').get(credential_id=raw_id)
    except Passkey.DoesNotExist:
        return None, 'That passkey is not registered with ELSA.'

    if user is not None and passkey.user_id != user.pk:
        return None, 'That passkey belongs to a different account.'

    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=relying_party_id(request),
            expected_origin=expected_origin(request),
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception:
        logger.warning('passkey assertion rejected (rp_id=%s origin=%s credential=%s)',
                       relying_party_id(request), expected_origin(request),
                       raw_id[:16], exc_info=True)
        return None, 'That passkey could not be verified. Please try again.'

    # A counter that doesn't advance could mean a cloned authenticator. Ignore 0, plenty always send it.
    if verified.new_sign_count and verified.new_sign_count <= passkey.sign_count:
        return None, 'That passkey could not be verified. Please try again.'

    passkey.mark_used(verified.new_sign_count)
    return passkey, None

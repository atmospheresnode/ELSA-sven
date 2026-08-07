# -*- coding: utf-8 -*-
"""
WebAuthn ceremony helpers.

Registration and authentication each run in two halves: issue options carrying
a challenge, then verify what the authenticator signed.  The challenge lives in
the session between the halves and must be single use.
"""
from __future__ import unicode_literals

import json

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


RP_NAME = 'ELSA: Educational Labeling System @Atmospheres'

REGISTER_CHALLENGE_KEY = 'webauthn_register_challenge'
AUTH_CHALLENGE_KEY = 'webauthn_auth_challenge'


def relying_party_id(request):
    """
    The domain the credential is bound to, without port or path.

    Taken from the request so dev and production need no configuration.  Safe
    because ALLOWED_HOSTS has already rejected anything else by the time a view
    runs.  A setting wins if ELSA ever sits behind a differing host.
    """
    configured = getattr(settings, 'WEBAUTHN_RP_ID', None)
    if configured:
        return configured
    return request.get_host().split(':')[0]


def expected_origin(request):
    """Scheme and host exactly as the browser saw it, port included."""
    configured = getattr(settings, 'WEBAUTHN_ORIGIN', None)
    if configured:
        return configured
    return '{0}://{1}'.format(request.scheme, request.get_host())


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
    """
    Options for enrolling a new passkey.

    Resident keys are required so the credential is discoverable, which is what
    allows sign-in without a username.  Baked in at creation: changing it later
    forces everyone to re-enroll.
    """
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
        # The reason is withheld deliberately: it would help an attacker.
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


def authentication_options(request):
    """
    Options for signing in.

    The credential list is deliberately left empty rather than scoped to the
    account: options are issued before anyone has proved who they are, so
    naming credentials would tell a stranger which usernames own passkeys.
    The account binding happens in verify_authentication instead.

    User verification is required, so a passkey proves possession plus a
    biometric or PIN, which is what lets one stand in for two factors.
    """
    options = generate_authentication_options(
        rp_id=relying_party_id(request),
        user_verification=UserVerificationRequirement.REQUIRED,
    )

    stash_challenge(request, AUTH_CHALLENGE_KEY, options.challenge)
    return json.loads(options_to_json(options))


def verify_authentication(request, credential, user=None):
    """
    Checks a sign-in response.

    When ``user`` is given the credential must belong to them, which stops a
    valid passkey for one account satisfying a challenge staged for another.
    """
    challenge = take_challenge(request, AUTH_CHALLENGE_KEY)
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
        return None, 'That passkey could not be verified. Please try again.'

    # A counter that fails to advance suggests a cloned authenticator.  Plenty
    # never implement it and always send 0, so only act on a non-zero one.
    if verified.new_sign_count and verified.new_sign_count <= passkey.sign_count:
        return None, 'That passkey could not be verified. Please try again.'

    passkey.mark_used(verified.new_sign_count)
    return passkey, None

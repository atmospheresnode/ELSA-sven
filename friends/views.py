# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function

import json
from urllib.parse import unquote
import logging
import os

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.backends import AllowAllUsersModelBackend
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import (MinimumLengthValidator,
                                                     get_default_password_validators)
from django.core.mail import EmailMessage
from django.db import transaction
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_POST
from datetime import timedelta

from build.models import Bundle
from . import passkeys, throttle
from . import devices, drafting
from .closure import close_account
from .forms import (AgencyRequestForm, AgencySendForm, CloseAccountForm,
                    ElsaPasswordChangeForm, EmailChangeForm, EmailCodeForm, NameForm,
                    PasskeyNameForm, ResearchProfileForm, UserForm,
                    UserProfileForm, UsernameForm, flag_invalid_fields)
from .rename import RenameRefused, rename_user
# Re-exported: other apps send branded mail with these.
from .mail import (EMAIL_LOGO_CID, FROM_ADDRESS, _elsa_logo_attachment,
                   _elsa_logo_png, build_branded, send_branded)
from .middleware import BROWSER_ZONE_COOKIE, zone_for
from .models import OTP_TTL, AccountEvent, Passkey, TrustedDevice, UserProfile, UserSession


logger = logging.getLogger(__name__)


#----------------------------------------------------------------------------------------
# The archive directory.  A username becomes a directory under ARCHIVE_DIR, so
# every path built from one is checked to stay there.
#----------------------------------------------------------------------------------------

def is_inside(path, root):
    """Whether ``path`` resolves to somewhere strictly below ``root``."""
    path = os.path.realpath(path)
    root = os.path.realpath(root)
    return os.path.commonpath([path, root]) == root and path != root


def archive_dir_for(username):
    path = os.path.join(settings.ARCHIVE_DIR, username)
    if not is_inside(path, settings.ARCHIVE_DIR):
        raise ValueError('username {0!r} escapes the archive'.format(username))
    return path


def profile_for(user):
    """The user's profile, created if somehow missing (the signal makes one)."""
    try:
        return user.userprofile
    except UserProfile.DoesNotExist:
        return UserProfile.objects.create(user=user, directory=archive_dir_for(user.username))


def absolute_url(request, path):
    """
    A link for an email.  https outside DEBUG, for the reason in
    passkeys.expected_origin: behind TLS terminated upstream request.scheme
    says http, and a reset or confirmation link should never travel that way.
    """
    scheme = request.scheme if settings.DEBUG else 'https'
    return '{0}://{1}{2}'.format(scheme, request.get_host(), path)


def create_archive_dir(username):
    """
    Creates the user's archive folder and returns its path.  Real failures
    (permissions, a full disk) propagate, so registration rolls back instead of
    leaving an account with nowhere to put its bundles.
    """
    path = archive_dir_for(username)
    if os.path.isdir(path):
        # Usernames are unique, so this is a folder left behind by a deleted
        # account.  Worth a look by an admin, but not worth refusing sign-up.
        logger.warning('archive folder for new user %s already exists', username)
    os.makedirs(path, exist_ok=True)
    return path


# The old Bundle Hub address, /accounts/<profile id>/.  Kept so bookmarks still
# work.  It used to render the hub itself and, on every visit, delete the
# record of any bundle whose folder it could not see: a disk or NFS hiccup
# became permanent data loss, and it ran before the ownership check, so anyone
# signed in could trigger it on someone else's bundles.
@login_required
def profile(request, pk_user):
    if not UserProfile.objects.filter(pk=pk_user, user=request.user).exists():
        return redirect('main:restricted_access')
    return redirect('friends:bundle_hub')



def admin_login(request, extra_context=None):
    """
    Stands in for AdminSite.login, which never renders its password form.
    Staff arrive already signed in through ELSA, second factor included.
    """
    if not request.user.is_authenticated:
        messages.info(request, "Sign in to ELSA first, then open the admin again.")
        return HttpResponseRedirect(reverse('main:index'))
    if request.user.is_active and request.user.is_staff:
        return redirect('admin:index')
    return redirect('main:restricted_access')


# Keys holding the half-finished login while the user reads their email.  The
# session only names the pending user; it grants nothing on its own.
OTP_SESSION_KEY = 'pre_otp_user_id'
OTP_PURPOSE_KEY = 'pre_otp_purpose'
OTP_STARTED_KEY = 'pre_otp_started_at'
OTP_SESSION_TTL = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Throttles.  Counted in the database by friends/throttle.py, one UPDATE per
# hit, so parallel requests cannot slip past a limit together.
# ---------------------------------------------------------------------------

SIGNUP_PER_HOUR = 5
SIGNUP_PER_DAY = 20

PASSWORD_WINDOW = 15 * 60
PASSWORD_FAILURES_PER_ACCOUNT = 10
PASSWORD_FAILURES_PER_IP = 30

# Each of these sends an email, so they cap how hard one client can make ELSA
# mail somebody.
RESET_PER_HOUR_PER_IP = 10
RESET_PER_HOUR_PER_ADDRESS = 3
EMAIL_CHANGE_PER_HOUR = 5


def _client_ip(request):
    """
    Best-effort client address.

    X-Forwarded-For is only read when settings say a proxy sets it, because
    otherwise any client can forge the header and sidestep every throttle here.
    Behind Apache without that setting every request looks like the proxy, and
    these limits become global rather than per-client.
    """
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or 'unknown'


def _signup_throttled(request):
    """
    Caps sign-up attempts per address.  Unique emails already stop one inbox
    being bombed repeatedly; this stops a script working through many.
    """
    ip = _client_ip(request)
    if throttle.hit('signup-hour-{0}'.format(ip), 3600) > SIGNUP_PER_HOUR:
        return True
    return throttle.hit('signup-day-{0}'.format(ip), 86400) > SIGNUP_PER_DAY


def _password_key(username):
    return 'pwd-user-{0}'.format((username or '').lower()[:120])


def _password_ip_key(request):
    return 'pwd-ip-{0}'.format(_client_ip(request))


def _password_throttled(request, username):
    """
    Password guessing had no ceiling at all: the OTP had an attempt cap but the
    password in front of it did not.  Counted per account and per address, and
    only failures count, so signing in normally never uses up the allowance.
    """
    if throttle.count(_password_key(username)) >= PASSWORD_FAILURES_PER_ACCOUNT:
        return True
    return throttle.count(_password_ip_key(request)) >= PASSWORD_FAILURES_PER_IP


def _record_password_failure(request, username):
    throttle.hit(_password_key(username), PASSWORD_WINDOW)
    throttle.hit(_password_ip_key(request), PASSWORD_WINDOW)


def _clear_password_failures(username):
    throttle.reset(_password_key(username))


def _mask_email(address):
    """
    Turns 'vhartwick@nmsu.edu' into 'vh*****k@nmsu.edu' so the page can confirm
    where the code went without printing the address in full.
    """
    if not address or '@' not in address:
        return ''

    name, domain = address.rsplit('@', 1)
    # Short local parts get a fixed mask, otherwise a three-character name
    # would come through entirely unmasked.
    if len(name) <= 4:
        return '{0}{1}@{2}'.format(name[:1], '*' * max(2, len(name) - 1), domain)
    return '{0}{1}{2}@{3}'.format(name[:2], '*' * (len(name) - 3), name[-1], domain)


def _send_otp(user, code, purpose):
    """Emails a one-time code.  purpose is 'login' or 'register'."""
    subject = ("ELSA: confirm your email address" if purpose == 'register'
               else "ELSA: your sign-in code")

    send_branded([user.email], subject, 'friends/email/otp_code', {
        'code': code,
        'purpose': purpose,
        'first_name': user.first_name,
        'ttl_minutes': int(OTP_TTL.total_seconds() // 60),
    })


def _user_zone(user):
    zone = zone_for(UserProfile.objects.filter(user=user).values_list('timezone', flat=True).first())
    return zone or timezone.get_default_timezone()


def _send_security_notice(request, user, to, subject, headline, lead, detail=''):
    """
    Tells someone that a sensitive change was made to their account, with
    the way out if it was not them.  Sent after the change is saved; a failure
    is logged and does not undo it.
    """
    return send_branded(to, subject, 'friends/email/account_notice', {
        'headline': headline,
        'first_name': user.first_name,
        'lead': lead,
        'detail': detail,
        'when': timezone.localtime(timezone=_user_zone(user)).strftime('%B %d, %Y at %I:%M %p %Z'),
        'reset_url': absolute_url(request, reverse('friends:password_reset')),
        'contact_url': absolute_url(request, reverse('main:contact')),
    })


def _stage_pending_user(request, user, purpose):
    """
    Parks a user who has cleared the password check but is not logged in yet.
    Holds for both second factors: the session entry names the account and
    grants nothing on its own.
    """
    request.session[OTP_SESSION_KEY] = user.id
    request.session[OTP_PURPOSE_KEY] = purpose
    request.session[OTP_STARTED_KEY] = timezone.now().isoformat()


def _stage_otp_challenge(request, user, profile, purpose):
    """
    Issues a code (honouring the resend cooldown), parks the pending user in
    the session, and sends them to the verify page.
    """
    if profile.seconds_until_resend():
        messages.info(request, "A code has already been sent to your email.  "
                               "Check your inbox, or request another one below.")
    else:
        _send_otp(user, profile.generate_otp(), purpose)

    _stage_pending_user(request, user, purpose)

    return redirect('friends:otp_verify')


def _clear_otp_session(request):
    for key in (OTP_SESSION_KEY, OTP_PURPOSE_KEY, OTP_STARTED_KEY):
        request.session.pop(key, None)


def _pending_otp_user(request):
    """
    Resolves the user mid-verification, or None if there is no live challenge.
    Also enforces an outer time limit on the staged session entry.
    """
    user_id = request.session.get(OTP_SESSION_KEY)
    if not user_id:
        return None

    started = request.session.get(OTP_STARTED_KEY)
    if not started:
        return None
    try:
        if parse_datetime(started) + OTP_SESSION_TTL < timezone.now():
            return None
    except (TypeError, ValueError):
        return None

    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return None


def _otp_lock_message(request, seconds):
    minutes = max(1, int(round(seconds / 60.0)))
    messages.error(request, "Too many incorrect codes.  For your security this "
                            "account is locked for {0} more minute{1}.".format(
                                minutes, '' if minutes == 1 else 's'))


#----------------------------------------------------------------------------------------
# Sign-in.
#
# Identifier first, following the FIDO Alliance design guidelines: ask who you
# are, then show one method on its own screen.  Their research ranks the ways
# of offering a passkey as autofill, then identifier-first, then a dedicated
# button, and warns against putting a passkey button and a password form on the
# same screen.  So the landing page asks only for a username, with autofill
# armed on it, and the method screens live behind that.
#----------------------------------------------------------------------------------------

LOGIN_IDENTIFIER_KEY = 'login_identifier'
LOGIN_PASSWORD_CHOSEN_KEY = 'login_password_chosen'


def _clear_login_session(request):
    for key in (LOGIN_IDENTIFIER_KEY, LOGIN_PASSWORD_CHOSEN_KEY):
        request.session.pop(key, None)


def _identified_user(request):
    """
    The account named on the first screen, or None.

    Naming an account proves nothing on its own, so this is only ever used to
    decide which method screen to show and to bind a passkey to the claim.
    """
    username = request.session.get(LOGIN_IDENTIFIER_KEY)
    if not username:
        return None
    return User.objects.filter(username=username).first()


def login_identify(request):
    """
    Step one: who is signing in.

    Someone with a passkey goes to the passkey screen, everyone else to the
    password screen.  An unrecognised username also goes to the password
    screen, so a wrong guess cannot be told apart from an account that simply
    uses a password.
    """
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('main:index'))

    username = (request.POST.get('username') or '').strip()
    if not username:
        messages.error(request, "Please enter your username.")
        return HttpResponseRedirect(reverse('main:index'))

    _clear_login_session(request)
    request.session[LOGIN_IDENTIFIER_KEY] = username

    user = User.objects.filter(username=username).first()

    # Only send them to the passkey screen if a passkey has actually worked in
    # this browser.  Owning one on another machine is no help here, and landing
    # on a prompt this device cannot answer is the confusing part.
    if (user and user.is_active and user.passkeys.exists()
            and _device_knows_passkey(request, user)):
        return redirect('friends:login_passkey')

    return redirect('friends:login_password')


def login_passkey(request):
    """
    Step two, passkey route.  The ceremony runs over the JSON endpoints and
    signs the user straight in: a passkey with user verification proves both
    possession of the authenticator and a biometric or PIN.
    """
    username = request.session.get(LOGIN_IDENTIFIER_KEY)
    if not username:
        return HttpResponseRedirect(reverse('main:index'))

    return render(request, 'friends/login_passkey.html', {
        'username': username,
        'stage': 'identify',
    })


def login_password(request):
    """Step two, password route."""
    username = request.session.get(LOGIN_IDENTIFIER_KEY)
    if not username:
        return HttpResponseRedirect(reverse('main:index'))

    if request.method == 'POST':
        return _continue_with_password(request, username, request.POST.get('password'))

    user = User.objects.filter(username=username).first()
    return render(request, 'friends/login_password.html', {
        'username': username,
        # Only offer the way back if there is actually a passkey to go back to.
        'has_passkey': bool(user and user.is_active and user.passkeys.exists()),
    })


def login_use_password_instead(request):
    """
    Leaving the passkey screen for the password screen, for somebody without
    their authenticator to hand.  The choice is remembered so the second factor
    afterwards is an emailed code rather than the passkey they just declined.
    """
    if request.method != 'POST':
        return redirect('friends:login_password')

    request.session[LOGIN_PASSWORD_CHOSEN_KEY] = True
    return redirect('friends:login_password')


def _continue_with_password(request, username, password):
    """
    Checks a password and hands off to whichever second factor applies.
    Shared by the password screen and the legacy single-post login view.
    """
    if _password_throttled(request, username):
        messages.error(request, "Too many sign-in attempts. Please wait a few "
                                "minutes and try again.")
        return redirect('friends:login_password')

    # Inactive accounts are visible here on purpose.  authenticate() would turn
    # them away with "Invalid username or password", which stranded everyone
    # who signed up but did not enter their code in time: told their correct
    # password was wrong, refused a reset, and holding their username and
    # address forever.  One hash either way, so timing stays the same.
    user = AllowAllUsersModelBackend().authenticate(request, username=username,
                                                    password=password)

    if not user:
        _record_password_failure(request, username)
        messages.error(request, "Invalid username or password.")
        return redirect('friends:login_password')

    _clear_password_failures(username)

    profile = profile_for(user)

    if not user.is_active:
        if _never_verified(user, profile):
            lock = profile.otp_lock_remaining()
            if lock:
                _otp_lock_message(request, lock)
                return HttpResponseRedirect(reverse('main:index'))
            messages.info(request, "Your email address is not confirmed yet.  "
                                   "Enter the code we sent to finish setting up your account.")
            return _stage_otp_challenge(request, user, profile, 'register')
        return render(request, 'friends/inactive.html', {'user': user})

    # A browser the user asked ELSA to remember has already passed the second
    # factor once; with the right password it signs straight in.  The OTP lock
    # guards code guessing, which does not happen on this path.
    if devices.trusted_device_for(request, user):
        request.elsa_sign_in_method = 'password on a remembered device'
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        _clear_login_session(request)
        return HttpResponseRedirect(reverse('main:index'))

    # A registered passkey is the stronger second factor, but only ask for one
    # this browser has actually used.  Owning a passkey on another machine is
    # no help here, and the same unanswerable prompt one step later is exactly
    # what the device check upstream exists to avoid.
    declined = request.session.get(LOGIN_PASSWORD_CHOSEN_KEY)
    if user.passkeys.exists() and not declined and _device_knows_passkey(request, user):
        _stage_pending_user(request, user, 'login')
        return redirect('friends:passkey_verify')

    lock = profile.otp_lock_remaining()
    if lock:
        _otp_lock_message(request, lock)
        return HttpResponseRedirect(reverse('main:index'))

    return _stage_otp_challenge(request, user, profile, 'login')


def _never_verified(user, profile):
    """
    An account that registered and never confirmed its address, as opposed to
    one an administrator switched off.  Only the first may reactivate itself.
    Both tests, so an account from before email_verified_at existed that has
    ever signed in counts as verified.
    """
    return profile.email_verified_at is None and user.last_login is None


# let's elsa's friends login
def friend_login(request):
    """
    Single-post sign-in: username and password together.

    The landing page now identifies first and posts the password separately,
    but this endpoint stays because other templates link to it and it is a
    valid way in.
    """
    if request.method != 'POST':
        # On GET, send the user to the landing page, which has the login form
        return HttpResponseRedirect(reverse('main:index'))

    username = (request.POST.get('username') or '').strip()
    request.session[LOGIN_IDENTIFIER_KEY] = username

    return _continue_with_password(request, username, request.POST.get('password'))


def otp_verify(request):
    """
    Verifies OTP, Activates User (if new), and Logs them in.
    """
    user = _pending_otp_user(request)

    if not user:
        _clear_otp_session(request)
        messages.error(request, "Session expired. Please login or register again.")
        return HttpResponseRedirect(reverse('main:index'))

    purpose = request.session.get(OTP_PURPOSE_KEY, 'login')
    profile = user.userprofile

    if request.method == 'POST':
        ok, reason = profile.verify_otp(request.POST.get('otp'))

        if ok:
            # Only the registration flow may flip a user live.  A login must
            # never resurrect an account an admin has deactivated.
            if purpose == 'register' and _never_verified(user, profile):
                user.is_active = True
                user.save(update_fields=['is_active'])

            if purpose == 'register' and profile.email_verified_at is None:
                profile.email_verified_at = timezone.now()
                profile.save(update_fields=['email_verified_at'])

            if not user.is_active:
                _clear_otp_session(request)
                return render(request, 'friends/inactive.html', {'user':user})

            request.elsa_sign_in_method = ('new account confirmed' if purpose == 'register'
                                           else 'password and emailed code')
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            _clear_otp_session(request)
            _clear_login_session(request)

            # Now that they are properly signed in, this is the moment to ask.
            # Enrolling can only ever happen after authentication: a passkey
            # created by anyone who merely typed a username would be a
            # permanent way in to that account.
            if purpose == 'register':
                # A brand-new account: one optional setup step, then the hub.
                request.session[ONBOARDING_KEY] = True
                response = redirect('friends:welcome')
            elif _should_offer_passkey(request, user):
                response = redirect('friends:passkey_offer')
            else:
                response = HttpResponseRedirect(reverse('main:index'))

            if purpose == 'login' and request.POST.get('remember_device'):
                devices.remember_device(request, response, user)
            return response

        if reason == 'locked':
            _clear_otp_session(request)
            _otp_lock_message(request, profile.otp_lock_remaining())
            return HttpResponseRedirect(reverse('main:index'))
        elif reason == 'expired':
            messages.error(request, "This code has expired. Request a new one below.")
        else:
            remaining = profile.attempts_remaining()
            messages.error(request, "Invalid code. {0} attempt{1} remaining.".format(
                remaining, '' if remaining == 1 else 's'))

    return render(request, 'friends/otp_verify.html', {
        'resend_wait': profile.seconds_until_resend(),
        'expires_in': profile.seconds_until_expiry(),
        'masked_email': _mask_email(user.email),
        'purpose': purpose,
    })


def otp_resend(request):
    """Sends a fresh code for a verification already in progress."""
    if request.method != 'POST':
        return redirect('friends:otp_verify')

    user = _pending_otp_user(request)

    if not user:
        _clear_otp_session(request)
        messages.error(request, "Session expired. Please login or register again.")
        return HttpResponseRedirect(reverse('main:index'))

    profile = user.userprofile
    purpose = request.session.get(OTP_PURPOSE_KEY, 'login')

    lock = profile.otp_lock_remaining()
    if lock:
        _clear_otp_session(request)
        _otp_lock_message(request, lock)
        return HttpResponseRedirect(reverse('main:index'))

    wait = profile.seconds_until_resend()
    if wait:
        messages.info(request, "Please wait {0} more second{1} before requesting "
                               "another code.".format(wait, '' if wait == 1 else 's'))
    else:
        _send_otp(user, profile.generate_otp(), purpose)
        messages.success(request, "A new code is on its way to your email.")

    return redirect('friends:otp_verify')




#----------------------------------------------------------------------------------------
# Passkeys (WebAuthn).  The ceremony logic lives in friends/passkeys.py; these
# views only move data between the browser and it.
#----------------------------------------------------------------------------------------


def _json_body(request):
    """Parses a JSON request body, or returns None if it is not usable."""
    try:
        return json.loads(request.body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError):
        return None


# A per-browser note of which accounts have used a passkey here, and who has
# recently said "not now" to setting one up.
#
# The server cannot ask the browser whether it holds a credential: WebAuthn
# withholds that on purpose, because it would be a fingerprinting signal.  So
# this cookie stands in for it.
#
# It is a routing hint and nothing more.  It decides which screen is shown
# first, never who gets in.  Forging or clearing it only changes which screen
# you land on, and both still demand real proof.
PASSKEY_HINT_COOKIE = 'elsa_passkey_hint'
PASSKEY_HINT_MAX_AGE = 60 * 60 * 24 * 365   # A year.
PASSKEY_OFFER_SILENCE = timedelta(days=60)  # How long a "not now" lasts.


def _read_passkey_hint(request):
    try:
        raw = request.get_signed_cookie(PASSKEY_HINT_COOKIE, default=None)
        parsed = json.loads(raw) if raw else {}
        return {
            'known': [int(i) for i in parsed.get('known') or []],
            'declined': {int(k): v for k, v in (parsed.get('declined') or {}).items()},
        }
    except Exception:
        # Tampered, stale or unparseable.  Start over rather than fail a login.
        return {'known': [], 'declined': {}}


def _write_passkey_hint(response, hint):
    response.set_signed_cookie(
        PASSKEY_HINT_COOKIE,
        json.dumps(hint),
        max_age=PASSKEY_HINT_MAX_AGE,
        secure=getattr(settings, 'SESSION_COOKIE_SECURE', False),
        httponly=True,
        samesite='Lax',
    )
    return response


def _remember_passkey_device(request, response, user):
    """Records that a passkey has now worked in this browser for this account."""
    hint = _read_passkey_hint(request)
    if user.pk not in hint['known']:
        hint['known'].append(user.pk)
    hint['declined'].pop(user.pk, None)
    return _write_passkey_hint(response, hint)


def _device_knows_passkey(request, user):
    return user.pk in _read_passkey_hint(request)['known']


def _should_offer_passkey(request, user):
    """
    Whether to invite this user to set up a passkey on this device.

    Only after they are fully signed in, and only on a device that has not
    used one for this account.  Having a passkey on another machine is exactly
    the case worth asking about, so account-wide credentials are not a reason
    to stay quiet.
    """
    hint = _read_passkey_hint(request)

    # The cookie can outlive the credential: removing a passkey from ELSA
    # cannot reach into a browser and rewrite what it remembers.  If the
    # account holds no passkeys at all, this browser certainly has no usable
    # one, whatever the cookie still claims.
    if user.pk in hint['known'] and user.passkeys.exists():
        return False

    declined = hint['declined'].get(user.pk)
    if declined:
        try:
            when = parse_datetime(declined)
        except (TypeError, ValueError):
            when = None
        if when and when + PASSKEY_OFFER_SILENCE > timezone.now():
            return False

    return True


@login_required
@require_POST
def passkey_register_options(request):
    """First half of enrollment: hand the browser a challenge."""
    return JsonResponse(passkeys.registration_options(request, request.user))


@login_required
@require_POST
def passkey_register_verify(request):
    """Second half of enrollment: check the response and keep the public key."""
    body = _json_body(request)
    if not body or 'credential' not in body:
        return JsonResponse({'error': 'Malformed request.'}, status=400)

    passkey, error = passkeys.verify_registration(
        request, request.user, body['credential'], body.get('name', ''))

    if error:
        return JsonResponse({'error': error}, status=400)

    messages.success(request, 'Passkey "{0}" added.'.format(passkey.name))
    devices.record(request.user, AccountEvent.PASSKEY_ADDED, request, detail=passkey.name)

    response = JsonResponse({'ok': True, 'name': passkey.name})
    return _remember_passkey_device(request, response, request.user)


@login_required
@require_POST
def passkey_delete(request, pk):
    """Removes one of the signed-in user's passkeys."""
    try:
        passkey = Passkey.objects.get(pk=pk, user=request.user)
    except Passkey.DoesNotExist:
        return redirect('main:restricted_access')

    name = passkey.name
    passkey.delete()
    devices.record(request.user, AccountEvent.PASSKEY_REMOVED, request, detail=name)
    messages.success(request, 'Passkey "{0}" removed.'.format(name))

    response = redirect('friends:useraccount')

    # With none left, this browser no longer knows a passkey for them, so stop
    # the hint claiming otherwise and let the setup offer come back.
    if not request.user.passkeys.exists():
        hint = _read_passkey_hint(request)
        if request.user.pk in hint['known']:
            hint['known'].remove(request.user.pk)
            return _write_passkey_hint(response, hint)

    return response


def _passkey_target_user(request):
    """
    The account a passkey response has to match, or None if the sign-in is
    fully anonymous (autofill from the landing page, where nobody has named an
    account and any registered passkey is a legitimate answer).
    """
    return _pending_otp_user(request) or _identified_user(request)


@require_POST
def passkey_auth_options(request):
    """First half of sign-in: hand the browser a challenge."""
    return JsonResponse(passkeys.authentication_options(request))


@require_POST
def passkey_auth_verify(request):
    """Second half of sign-in: verify the assertion and log the user in."""
    body = _json_body(request)
    if not body or 'credential' not in body:
        return JsonResponse({'error': 'Malformed request.'}, status=400)

    target = _passkey_target_user(request)
    raw_id = body['credential'].get('rawId') or body['credential'].get('id')

    passkey, error = passkeys.verify_authentication(request, body['credential'], user=target)
    if error:
        payload = {'error': error}
        # Deleting a passkey here cannot remove it from the authenticator that
        # holds it, so browsers keep offering credentials ELSA has forgotten.
        # Flagging it lets the page ask the browser to drop it.
        if raw_id and not Passkey.objects.filter(credential_id=raw_id).exists():
            payload['reason'] = 'unknown_credential'
            payload['credential_id'] = raw_id
        return JsonResponse(payload, status=400)

    user = passkey.user
    if not user.is_active:
        _clear_otp_session(request)
        _clear_login_session(request)
        return JsonResponse({'error': 'That account is not active.'}, status=403)

    request.elsa_sign_in_method = 'passkey'
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    _clear_otp_session(request)
    _clear_login_session(request)

    response = JsonResponse({'ok': True, 'redirect': reverse('main:index')})
    return _remember_passkey_device(request, response, user)


def passkey_verify(request):
    """
    The second-factor page shown after a password, for users who have a
    passkey.  The ceremony itself runs over the JSON endpoints above.
    """
    user = _pending_otp_user(request)

    if not user:
        _clear_otp_session(request)
        messages.error(request, "Session expired. Please login or register again.")
        return HttpResponseRedirect(reverse('main:index'))

    return render(request, 'friends/login_passkey.html', {
        'username': user.username,
        'masked_email': _mask_email(user.email),
        'stage': 'second_factor',
    })


@login_required
def passkey_offer(request):
    """
    The invitation to set up a passkey, shown once someone has fully signed in
    on a device that has never used one for their account.

    This is the only correct moment for it.  Enrolling adds a credential that
    can sign in as this account indefinitely, so it has to sit behind a
    completed authentication, not stand in for one.
    """
    # The last onboarding step, if this is one: the flag ends here.
    onboarding = bool(request.session.pop(ONBOARDING_KEY, False))
    done = reverse('friends:bundle_hub') if onboarding else reverse('main:index')

    if not _should_offer_passkey(request, request.user):
        return HttpResponseRedirect(done)

    return render(request, 'friends/passkey_offer.html', {
        'has_passkey_elsewhere': request.user.passkeys.exists(),
        'onboarding': onboarding,
        'done_url': done,
    })


@login_required
@require_POST
def passkey_offer_dismiss(request):
    """
    Records a "not now" so the offer stays quiet for a while.  A prompt that
    returns every single sign-in is how a helpful feature turns into a nuisance.
    """
    hint = _read_passkey_hint(request)
    hint['declined'][request.user.pk] = timezone.now().isoformat()

    # Only these two destinations: "then" comes from the page, not trusted input.
    target = 'friends:bundle_hub' if request.POST.get('then') == 'hub' else 'main:index'
    return _write_passkey_hint(redirect(target), hint)


@require_POST
def passkey_use_email_instead(request):
    """
    Escape hatch from the passkey page: sends a code to the staged user so
    somebody without their authenticator to hand can still get in.
    """
    user = _pending_otp_user(request)

    if not user:
        _clear_otp_session(request)
        messages.error(request, "Session expired. Please login or register again.")
        return HttpResponseRedirect(reverse('main:index'))

    profile = user.userprofile

    lock = profile.otp_lock_remaining()
    if lock:
        _clear_otp_session(request)
        _otp_lock_message(request, lock)
        return HttpResponseRedirect(reverse('main:index'))

    return _stage_otp_challenge(request, user, profile,
                                request.session.get(OTP_PURPOSE_KEY, 'login'))


# let's elsa's friends logout
@login_required
def friend_logout(request):
    # Messages default to cookie storage, which survives the session flush that
    # logout performs.  Anything still unread would then be shown to whoever
    # loads the public landing page next, on a shared machine possibly not the
    # person it was written for.  Draining marks them used so the cookie goes.
    list(messages.get_messages(request))

    logout(request)
    return HttpResponseRedirect(reverse('main:index'))



# let's people sign up to be one of elsa's friends
def register(request):
    if request.method == 'POST' and _signup_throttled(request):
        messages.error(request, "Too many sign-up attempts from this connection. "
                                "Please try again later.")
        return HttpResponseRedirect(reverse('main:index'))

    user_form = UserForm(request.POST or None)
    profile_form = UserProfileForm(request.POST or None)

    if user_form.is_valid() and profile_form.is_valid():

        # One transaction, and the folder is made inside it: if that fails the
        # user row rolls back rather than being left with nowhere to work.
        with transaction.atomic():
            user = user_form.save(commit=False)
            user.set_password(user.password)
            user.is_active = False
            user.save()

            profile = user.userprofile
            profile.agency = profile_form.cleaned_data.get('agency')
            profile.directory = create_archive_dir(user.username)
            profile.save(update_fields=['agency', 'directory'])

        return _stage_otp_challenge(request, user, profile, 'register')

    flag_invalid_fields(user_form, profile_form)

    return render(request, 'friends/register.html',
                  {'user_form': user_form, 'profile_form': profile_form})


#----------------------------------------------------------------------------------------
# Onboarding.  A new account gets one optional step (author details), then the
# passkey offer if this device can use one, then the Bundle Hub.  Nothing in it
# is required: sign-up already collected everything ELSA needs.
#----------------------------------------------------------------------------------------

ONBOARDING_KEY = 'elsa_onboarding'


def _onboarding_next(request):
    if _should_offer_passkey(request, request.user):
        return redirect('friends:passkey_offer')     # step 2; it ends onboarding
    request.session.pop(ONBOARDING_KEY, None)
    return redirect('friends:bundle_hub')


@login_required
def welcome(request):
    if not request.session.get(ONBOARDING_KEY):
        # Only right after sign-up; later visits belong on the account page.
        return redirect('friends:useraccount')

    profile = profile_for(request.user)
    form = ResearchProfileForm(request.POST or None,
                               initial={'orcid': profile.orcid, 'affiliation': profile.affiliation})

    if request.method == 'POST':
        if 'skip' in request.POST:
            return _onboarding_next(request)
        if form.is_valid():
            UserProfile.objects.filter(user=request.user).update(
                orcid=form.cleaned_data['orcid'], affiliation=form.cleaned_data['affiliation'])
            return _onboarding_next(request)

    return render(request, 'friends/welcome.html', {
        'form': form,
        'steps': 2 if _should_offer_passkey(request, request.user) else 1,
    })


#----------------------------------------------------------------------------------------
# The account page.
#
# One page for everything about the account: each setting is a row with its
# current value, and each Edit opens a small modal for that one thing.  The
# modals post to the JSON endpoints below and the page updates in place, so
# nothing reloads and nobody is sent elsewhere to change one field.
#----------------------------------------------------------------------------------------

STAFF_RECIPIENTS = ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu']

# Which modal ?edit= may open on arrival, for links from elsewhere.
ACCOUNT_MODALS = ('name', 'email', 'username', 'password', 'agency', 'research', 'close')

USERNAME_CHANGES_PER_DAY = 3
AGENCY_DRAFTS_PER_HOUR = 10
AGENCY_REQUESTS_PER_DAY = 3


@login_required
def friend_useraccount(request):
    profile = profile_for(request.user)
    edit = request.GET.get('edit')
    bundles = Bundle.objects.filter(user=request.user)
    return render(request, 'friends/useraccount.html', {
        'initials': _initials(request.user),
        'archive_count': bundles.filter(bundle_type='Archive').count(),
        'external_count': bundles.filter(bundle_type='External').count(),
        'zone_name': timezone.get_current_timezone_name(),
        'zone_from_browser': zone_for(unquote(request.COOKIES.get(BROWSER_ZONE_COOKIE, ''))) is not None,
        'sessions': devices.active_sessions(request),
        'remembered': devices.trusted_devices(request),
        'events': devices.recent_events(request.user),
        'userprofile': profile,
        'agency_label': profile.get_agency_display(),
        'agency_choices': [c for c in UserProfile.AGENCY_CHOICES if c[0] != profile.agency],
        'bundle_count': Bundle.objects.filter(user=request.user).count(),
        'passkeys': request.user.passkeys.all(),
        'email_pending': profile.email_change_pending(),
        'email_resend_wait': profile.seconds_until_email_resend(),
        'code_ttl_minutes': int(OTP_TTL.total_seconds() // 60),
        'open_modal': edit if edit in ACCOUNT_MODALS else '',
        'password_min_length': _password_min_length(),
    })



def _initials(user):
    """'VH' for Victoria Hartwick; the username's first two letters otherwise."""
    parts = [p for p in (user.first_name.strip(), user.last_name.strip()) if p]
    if parts:
        return ''.join(p[0] for p in parts[:2]).upper()
    return (user.username[:2] or '?').upper()


def _password_min_length():
    """The configured minimum, so the on-page checklist cannot drift from the validator."""
    for validator in get_default_password_validators():
        if isinstance(validator, MinimumLengthValidator):
            return validator.min_length
    return 1


# profile_settings NOTE:  It is important to NOT rename this as simply settings.  Since we import django.conf import settings, when our user goes to register, settings.ARCHIVE_DIR does not pull from our settings.py file.  Rather, Django comes to this function (if named settings) and notices there is no ARCHIVE_DIR declared here.  Big boo boo that cost me (k) a couple days to figure out.
@login_required
def friend_settings(request, pk_user):
    """The old Profile Settings address.  Settings now live on the account page."""
    if request.user.pk != int(pk_user):
        return redirect('main:restricted_access')
    return HttpResponseRedirect(reverse('friends:useraccount') + '?edit=name')


def _form_errors(form):
    """A form's errors as {field: [message, ...]}, with '__all__' for the rest."""
    return {field: [str(message) for message in messages_]
            for field, messages_ in form.errors.items()}


def _json_error(message, status=400, field='__all__'):
    return JsonResponse({'ok': False, 'errors': {field: [message]}}, status=status)


def _json_form_errors(form):
    return JsonResponse({'ok': False, 'errors': _form_errors(form)}, status=400)


def _password_guard(request):
    """A 429 once password guessing is throttled, else None."""
    if _password_throttled(request, request.user.username):
        return _json_error("Too many incorrect passwords. Please wait a few minutes "
                           "and try again.", status=429)
    return None


@login_required
@require_POST
def account_name(request):
    form = NameForm(request.POST, instance=User.objects.get(pk=request.user.pk))
    if not form.is_valid():
        return _json_form_errors(form)
    user = form.save()
    return JsonResponse({'ok': True, 'message': 'Your name has been updated.',
                         'first_name': user.first_name, 'last_name': user.last_name,
                         'full_name': user.get_full_name()})


def _send_email_code(user, new_email, code):
    return send_branded([new_email], "ELSA: confirm your new email address",
                        'friends/email/email_change_code', {
                            'first_name': user.first_name,
                            'username': user.username,
                            'code': code,
                            'ttl_minutes': int(OTP_TTL.total_seconds() // 60),
                        })


def _email_step_payload(profile):
    return {'ok': True, 'pending_email': profile.pending_email,
            'resend_wait': profile.seconds_until_email_resend()}


@login_required
@require_POST
def account_email_start(request):
    """Step one: the new address and the current password.  Mails a code to the address."""
    blocked = _password_guard(request)
    if blocked:
        return blocked

    user = request.user
    form = EmailChangeForm(user, request.POST)
    if not form.is_valid():
        if 'current_password' in form.errors:
            _record_password_failure(request, user.username)
        return _json_form_errors(form)

    if throttle.hit('email-change-{0}'.format(user.pk), 3600) > EMAIL_CHANGE_PER_HOUR:
        return _json_error("Too many email changes requested. Please try again later.", 429)

    profile = profile_for(user)
    new_email = form.cleaned_data['new_email']
    if not _send_email_code(user, new_email, profile.start_email_change(new_email)):
        profile.cancel_email_change()
        return _json_error("We could not send the code. Please try again in a few minutes.", 503)

    return JsonResponse(_email_step_payload(profile))


@login_required
@require_POST
def account_email_verify(request):
    """Step two: the code from the new inbox.  Switches the address."""
    profile = profile_for(request.user)
    if not profile.pending_email:
        return _json_error("There is no email change waiting. Please start again.", 409)

    form = EmailCodeForm(request.POST)
    if not form.is_valid():
        return _json_form_errors(form)

    new_email, reason = profile.verify_email_change(form.cleaned_data['code'])
    if reason == 'invalid':
        remaining = profile.email_attempts_remaining()
        return _json_error("That code is not right. {0} attempt{1} remaining.".format(
            remaining, '' if remaining == 1 else 's'), field='code')
    if reason == 'expired':
        return _json_error("That code has expired. Send a new one below.", field='code')
    if reason == 'exhausted':
        return _json_error("Too many incorrect codes. Please start the change again.", 409)

    with transaction.atomic():
        user = User.objects.select_for_update().get(pk=request.user.pk)
        # Checked again: another account may have claimed it meanwhile.
        if User.objects.filter(email__iexact=new_email).exclude(pk=user.pk).exists():
            profile.cancel_email_change()
            return _json_error("An account already uses that email address.", 409)
        old_email = user.email
        user.email = new_email
        user.save(update_fields=['email'])
        UserProfile.objects.filter(user=user).update(
            email_verified_at=timezone.now(), pending_email=None)

    devices.record(user, AccountEvent.EMAIL_CHANGED, request, detail='{0} to {1}'.format(old_email, new_email))

    # To the old address: if this was not them, that inbox is the one they
    # still have.
    _send_security_notice(
        request, user, [old_email],
        "ELSA: your email address was changed",
        "Your email address was changed",
        "The email address on your ELSA account ({0}) was changed from {1} to {2}. "
        "Sign-in codes and password resets now go to the new address.".format(
            user.username, old_email, new_email))

    return JsonResponse({'ok': True, 'email': new_email,
                         'message': 'Your email address is now {0}.'.format(new_email)})


@login_required
@require_POST
def account_email_resend(request):
    profile = profile_for(request.user)
    if not profile.pending_email:
        return _json_error("There is no email change waiting. Please start again.", 409)

    wait = profile.seconds_until_email_resend()
    if wait:
        return _json_error("Please wait {0} more second{1}.".format(wait, '' if wait == 1 else 's'), 429)

    # A resend is another email to an address that is not verified yet, so it
    # counts against the same hourly allowance as starting a change.
    if throttle.hit('email-change-{0}'.format(request.user.pk), 3600) > EMAIL_CHANGE_PER_HOUR:
        return _json_error("Too many codes requested. Please try again later.", 429)

    if not _send_email_code(request.user, profile.pending_email, profile.reissue_email_code()):
        return _json_error("We could not send the code. Please try again in a few minutes.", 503)

    payload = _email_step_payload(profile)
    payload['message'] = 'A new code is on its way to {0}.'.format(profile.pending_email)
    return JsonResponse(payload)


@login_required
@require_POST
def account_email_cancel(request):
    profile_for(request.user).cancel_email_change()
    return JsonResponse({'ok': True})


@login_required
@require_POST
def account_password(request):
    blocked = _password_guard(request)
    if blocked:
        return blocked

    user = request.user
    form = ElsaPasswordChangeForm(user, request.POST)
    if not form.is_valid():
        if 'old_password' in form.errors:
            _record_password_failure(request, user.username)
        return _json_form_errors(form)

    form.save()
    # Changing the password invalidates every session, including this one.
    # Keep this one: the user just proved who they are.
    update_session_auth_hash(request, form.user)
    _clear_password_failures(user.username)
    UserProfile.objects.filter(user=user).update(password_changed_at=timezone.now())
    # The other sessions just ended with the old password; so does any trust.
    UserSession.objects.filter(user=user).exclude(session_key=request.session.session_key).delete()
    devices.forget_all_devices(user)
    devices.record(user, AccountEvent.PASSWORD_CHANGED, request)

    _send_security_notice(
        request, user, [user.email],
        "ELSA: your password was changed",
        "Your password was changed",
        "The password for your ELSA account ({0}) was just changed, and every other "
        "device signed in to it has been signed out.".format(user.username))

    return JsonResponse({'ok': True, 'message': 'Your password has been changed.',
                         'changed_label': 'Last changed just now'})


@login_required
@require_POST
def account_close(request):
    """Closes the account.  Its bundles stay, for the ELSA team only."""
    blocked = _password_guard(request)
    if blocked:
        return blocked

    user = request.user
    if user.is_superuser:
        # Closing one's own admin account could leave nobody able to administer
        # ELSA; another admin deactivates it instead.
        return _json_error("Administrator accounts cannot be closed here. Ask another "
                           "ELSA administrator to deactivate it.", 403)

    form = CloseAccountForm(user, request.POST)
    if not form.is_valid():
        if 'current_password' in form.errors:
            _record_password_failure(request, user.username)
        return _json_form_errors(form)

    first_name = user.first_name
    record = close_account(user)

    send_branded([record['email']], "Goodbye from ELSA: your account has been closed",
                 'friends/email/account_closed', {
                     'first_name': first_name,
                     'username': record['username'],
                     'bundle_count': len(record['bundles']),
                     'contact_url': absolute_url(request, reverse('main:contact')),
                 })
    _notify_staff_of_closure(record)

    list(messages.get_messages(request))
    logout(request)
    messages.success(request, "Your account has been closed. Goodbye from ELSA, and thank you.")
    return JsonResponse({'ok': True, 'redirect': reverse('main:index')})


def _notify_staff_of_closure(record):
    """Plain mail to the team, in the codebase's usual EmailMessage form."""
    lines = [
        'An ELSA user closed their account.',
        '',
        'Username:  {0}'.format(record['username']),
        'Name:      {0}'.format(record['full_name'] or '(not given)'),
        'Email:     {0}'.format(record['email']),
        'Closed:    {0}'.format(timezone.localtime(record['closed_at']).strftime('%B %d, %Y at %I:%M %p %Z')),
        'Archive:   {0}'.format(record['archive_path'] or '(no folder on disk)'),
        '',
        'Bundles retained ({0}):'.format(len(record['bundles'])),
    ] + ['  - {0}'.format(name) for name in record['bundles']] + [
        '',
        'The name and email above were removed from ELSA\'s database and appear only '
        'in this message. The bundles are listed in the admin under Bundles, '
        'filtered by "Owner: closed by user", and the folder is linked from '
        'archive/_closed_accounts/.',
    ]
    try:
        EmailMessage(subject='ELSA: account closed by user ({0})'.format(record['username']),
                     body='\n'.join(lines), from_email=FROM_ADDRESS,
                     to=STAFF_RECIPIENTS).send()
    except Exception:
        logger.exception('closure notice for %s failed', record['username'])


@login_required
@require_POST
def account_username(request):
    blocked = _password_guard(request)
    if blocked:
        return blocked

    user = request.user
    form = UsernameForm(user, request.POST)
    if not form.is_valid():
        if 'current_password' in form.errors:
            _record_password_failure(request, user.username)
        return _json_form_errors(form)

    if throttle.hit('username-change-{0}'.format(user.pk), 86400) > USERNAME_CHANGES_PER_DAY:
        return _json_error("You have changed your username several times today. "
                           "Please try again tomorrow.", 429)

    try:
        old_username = rename_user(user, form.cleaned_data['new_username'])
    except RenameRefused as refusal:
        return _json_error(str(refusal), 409, field='new_username')
    except OSError:
        logger.exception('rename of %s failed on disk', user.username)
        return _json_error("ELSA could not move your files. Nothing was changed; please "
                           "contact the ELSA team.", 500)

    devices.record(user, AccountEvent.USERNAME_CHANGED, request,
                   detail='{0} to {1}'.format(old_username, user.username))
    _send_security_notice(
        request, user, [user.email],
        "ELSA: your username was changed",
        "Your username was changed",
        "The username on your ELSA account was changed from {0} to {1}. Sign in with "
        "{1} from now on; your bundles moved with it.".format(old_username, user.username))

    return JsonResponse({'ok': True, 'username': user.username,
                         'message': 'Your username is now {0}.'.format(user.username)})


def _agency_facts(user, profile, requested, note=''):
    labels = dict(UserProfile.AGENCY_CHOICES)
    bundles = Bundle.objects.filter(user=user)
    return {
        'name': user.get_full_name(),
        'username': user.username,
        'current': profile.agency,
        'current_label': labels.get(profile.agency, profile.agency),
        'requested': requested,
        'requested_label': labels.get(requested, requested),
        'bundle_count': bundles.count(),
        'archive_count': bundles.filter(bundle_type='Archive').count(),
        'external_count': bundles.filter(bundle_type='External').count(),
        'note': note or '',
    }


@login_required
@require_POST
def account_agency_draft(request):
    """Drafts the request email.  Nothing is sent: the user reviews it first."""
    profile = profile_for(request.user)
    form = AgencyRequestForm(profile, request.POST)
    if not form.is_valid():
        return _json_form_errors(form)

    if throttle.hit('agency-draft-{0}'.format(request.user.pk), 3600) > AGENCY_DRAFTS_PER_HOUR:
        subject, body = drafting.template_draft(_agency_facts(
            request.user, profile, form.cleaned_data['agency'], form.cleaned_data['note']))
        source = 'template'
    else:
        subject, body, source = drafting.draft(_agency_facts(
            request.user, profile, form.cleaned_data['agency'], form.cleaned_data['note']))

    return JsonResponse({'ok': True, 'subject': subject, 'body': body, 'source': source})


@login_required
@require_POST
def account_agency_send(request):
    user = request.user
    profile = profile_for(user)
    form = AgencySendForm(profile, request.POST)
    if not form.is_valid():
        return _json_form_errors(form)

    if throttle.hit('agency-send-{0}'.format(user.pk), 86400) > AGENCY_REQUESTS_PER_DAY:
        return _json_error("You have sent several requests today. The team will reply "
                           "by email; please wait for their answer.", 429)

    facts = _agency_facts(user, profile, form.cleaned_data['agency'])
    # Written by ELSA, not by the user or the model, so the team can trust it
    # whatever the message below says.
    verified = '\n'.join([
        'Agency change request, sent from the ELSA account page.',
        '',
        'Account:          {username}',
        'Email:            {email}',
        'Current agency:   {current_label} ({current})',
        'Requested agency: {requested_label} ({requested})',
        'Bundles:          {bundle_count} ({archive_count} Archive, {external_count} External)',
        'Admin:            {admin}',
        '',
        '---- Message from the user (they may have edited it) ----',
        '',
    ]).format(email=user.email,
              admin=absolute_url(request, reverse('admin:friends_userprofile_change', args=[profile.pk])),
              **facts)

    try:
        EmailMessage(subject='ELSA agency request ({0}): {1}'.format(user.username, form.cleaned_data['subject']),
                     body=verified + form.cleaned_data['body'], from_email=FROM_ADDRESS,
                     to=STAFF_RECIPIENTS, reply_to=[user.email]).send()
    except Exception:
        logger.exception('agency request from %s failed', user.username)
        return _json_error("Your request could not be sent. Please try again in a few minutes.", 503)

    # Their own copy, so they have a record of what was asked.
    try:
        EmailMessage(subject='Copy of your request: {0}'.format(form.cleaned_data['subject']),
                     body='This is a copy of the request you sent to the ELSA team. They will '
                          'reply to this address.\n\n' + form.cleaned_data['body'],
                     from_email=FROM_ADDRESS, to=[user.email]).send()
    except Exception:
        logger.exception('agency request copy to %s failed', user.username)

    return JsonResponse({'ok': True,
                         'message': 'Request sent. The ELSA team will reply to {0}.'.format(user.email)})


@login_required
@require_POST
def account_research(request):
    form = ResearchProfileForm(request.POST)
    if not form.is_valid():
        return _json_form_errors(form)
    UserProfile.objects.filter(user=request.user).update(
        orcid=form.cleaned_data['orcid'], affiliation=form.cleaned_data['affiliation'])
    return JsonResponse({'ok': True, 'orcid': form.cleaned_data['orcid'],
                         'affiliation': form.cleaned_data['affiliation'],
                         'message': 'Your researcher details have been saved.'})


@login_required
@require_POST
def account_session_end(request, pk):
    row = UserSession.objects.filter(pk=pk, user=request.user).first()
    if row is None:
        return _json_error("That device is no longer signed in.", 404)
    if row.session_key == request.session.session_key:
        return _json_error("To sign out this device, use Sign out in the menu.", 400)
    label = devices.device_label(row.user_agent)
    devices.end_session(request.user, row.session_key)
    devices.record(request.user, AccountEvent.SESSION_ENDED, request, detail=label)
    return JsonResponse({'ok': True, 'message': '{0} has been signed out.'.format(label)})


@login_required
@require_POST
def account_sessions_end_others(request):
    count = devices.end_other_sessions(request)
    # Being remembered only helps someone who also knows the password, but a
    # lost laptop is exactly when to stop trusting it.
    current = devices.current_trusted_device(request)
    others = TrustedDevice.objects.filter(user=request.user)
    if current is not None:
        others = others.exclude(pk=current.pk)
    others.delete()
    devices.record(request.user, AccountEvent.OTHERS_SIGNED_OUT, request)
    return JsonResponse({'ok': True, 'count': count,
                         'message': 'Every other device has been signed out.'})


@login_required
@require_POST
def account_device_forget(request, pk):
    device = TrustedDevice.objects.filter(pk=pk, user=request.user).first()
    if device is None:
        return _json_error("That device is no longer remembered.", 404)
    label = devices.device_label(device.user_agent)
    device.delete()
    devices.record(request.user, AccountEvent.DEVICE_FORGOTTEN, request, detail=label)
    return JsonResponse({'ok': True, 'message': '{0} will ask for an emailed code again.'.format(label)})


@login_required
@require_POST
def passkey_rename(request, pk):
    passkey = Passkey.objects.filter(pk=pk, user=request.user).first()
    if passkey is None:
        return _json_error("That passkey was not found.", 404)
    form = PasskeyNameForm(request.POST)
    if not form.is_valid():
        return _json_form_errors(form)
    passkey.name = form.cleaned_data['name']
    passkey.save(update_fields=['name'])
    return JsonResponse({'ok': True, 'name': passkey.name,
                         'message': 'Passkey renamed to "{0}".'.format(passkey.name)})


#----------------------------------------------------------------------------------------
# Password reset.  Django's views, pointed at ELSA's templates and mail, and
# rate limited.
#----------------------------------------------------------------------------------------

class ElsaPasswordResetForm(PasswordResetForm):
    """Sends the reset link in the same branded mail as every other account email."""

    def send_mail(self, subject_template_name, email_template_name, context,
                  from_email, to_email, html_email_template_name=None):
        # One line, as Django's own send_mail insists: a newline in a subject
        # is a header injection.
        subject = ''.join(render_to_string(subject_template_name, context).splitlines())
        protocol = context['protocol'] if settings.DEBUG else 'https'
        context = dict(context, protocol=protocol, reset_url='{0}://{1}{2}'.format(
            protocol, context['domain'],
            reverse('friends:password_reset_confirm',
                    kwargs={'uidb64': context['uid'], 'token': context['token']})))
        message = build_branded([to_email], subject,
                                'friends/registration/password_reset_email', context)
        try:
            message.send()
        except Exception:
            logger.exception('password reset email failed')


class ElsaPasswordResetView(auth_views.PasswordResetView):
    form_class = ElsaPasswordResetForm
    template_name = 'friends/registration/password_reset_form.html'
    subject_template_name = 'friends/registration/password_reset_subject.txt'
    success_url = reverse_lazy('friends:password_reset_done')

    def form_valid(self, form):
        address = form.cleaned_data['email'].lower()
        over_ip = throttle.hit('reset-ip-{0}'.format(_client_ip(self.request)), 3600) > RESET_PER_HOUR_PER_IP
        over_address = throttle.hit('reset-addr-{0}'.format(address), 3600) > RESET_PER_HOUR_PER_ADDRESS
        if over_ip or over_address:
            # The same page as a sent email, so the limit does not reveal
            # whether the address has an account.
            return HttpResponseRedirect(self.get_success_url())
        return super(ElsaPasswordResetView, self).form_valid(form)


class ElsaPasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'friends/registration/password_reset_confirm.html'
    success_url = reverse_lazy('friends:password_reset_complete')

    def form_valid(self, form):
        response = super(ElsaPasswordResetConfirmView, self).form_valid(form)
        user = form.user
        UserProfile.objects.filter(user=user).update(password_changed_at=timezone.now())
        UserSession.objects.filter(user=user).delete()
        devices.forget_all_devices(user)
        devices.record(user, AccountEvent.PASSWORD_RESET, self.request)
        # Owning the inbox is proof enough to lift a guessing lockout.
        _clear_password_failures(user.username)
        _send_security_notice(
            self.request, user, [user.email],
            "ELSA: your password was reset",
            "Your password was reset",
            "The password for your ELSA account ({0}) was just reset using a link "
            "sent to this address.".format(user.username))
        return response


#----------------------------------------------------------------------------------------
# Bundle Hub.
#----------------------------------------------------------------------------------------

@login_required
def bundle_hub(request):
    bundles = list(Bundle.objects.filter(user=request.user))
    for bundle in bundles:
        # Shown as a badge, never acted on: a folder that is missing for a
        # moment (NFS, a restore in progress) must not cost the user a bundle.
        bundle.files_missing = not os.path.isdir(bundle.directory())
    return render(request, 'friends/bundle_hub.html', {
        'bundles': bundles,
        'total_count': len(bundles),
        'archive_count': sum(1 for b in bundles if b.bundle_type == 'Archive'),
        'external_count': sum(1 for b in bundles if b.bundle_type == 'External'),
    })


@login_required
@require_POST
def delete_bundles(request):
    bundle_ids = [i for i in request.POST.getlist('bundle_ids') if i.isdigit()]
    if not bundle_ids:
        messages.warning(request, "No bundles were selected.")
        return redirect('friends:bundle_hub')

    bundles = list(Bundle.objects.filter(id__in=bundle_ids, user=request.user))
    if not bundles:
        messages.error(request, "You cannot delete bundles that do not belong to you.")
        return redirect('friends:bundle_hub')

    deleted, failed = 0, []
    for bundle in bundles:
        try:
            # remove_bundle refuses to touch a folder outside the owner's
            # archive, then deletes the record.
            bundle.remove_bundle()
            Bundle.objects.filter(pk=bundle.pk).delete()
            deleted += 1
        except Exception:
            logger.exception('deleting bundle %s for %s failed', bundle.pk, request.user.username)
            failed.append(bundle.name)

    if deleted:
        messages.success(request, "{0} bundle{1} deleted.".format(deleted, '' if deleted == 1 else 's'))
    if failed:
        messages.error(request, "Could not delete: {0}. Please contact ELSA support.".format(
            ', '.join(failed)))
    return redirect('friends:bundle_hub')

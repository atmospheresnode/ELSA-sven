# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from __future__ import print_function
from email.message import EmailMessage

from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import transaction
from django.urls import reverse
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect
from django.views import generic
from .forms import *
from .models import *
from .models import OTP_TTL
from build.models import Bundle
from . import passkeys
import json
import os

from email.mime.image import MIMEImage
from functools import lru_cache

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from datetime import timedelta

#----------------------------------------------------------------------------------------


# Useful functions.
def makedirs(path):
    try:
        os.mkdir(path)
    except OSError as e:
        if e.errno ==17:
            # Dir already exists.  This shouldn't happen since pk is unique.
            print("Something is wrong.  Registration is trying to create two users with non-unique pk")
            pass

def check():
    print(settings.MEDIA_DIR)
    print(os.path.join(settings.MEDIA_DIR, 'user'))


#----------------------------------------------------------------------------------------










# Create your views here.




# Saving Team for when Teams are implemented
#@login_required
#class Team(generic.ListView):
#    model = User
#    context_object_name = 'friend_list'
    #queryset = User.objects.all()
#    template_name = 'friends/index.html'



# Redirecting accounts/login to elsa/ (taking the user to the new UI)
def redirect_to_elsa_home(request): 
    return HttpResponseRedirect(reverse('main:index'))



# Normal profile page for users.  Displays the user, associated userprofile, and a list of related bundles.
@login_required
def profile(request, pk_user):
    
    context_dict = {}
    context_dict['userprofile'] = UserProfile.objects.get(pk=pk_user)
    context_dict['user'] = User.objects.get(userprofile=context_dict['userprofile'])
    context_dict['bundles'] = Bundle.objects.filter(user=context_dict['user'])
    context_dict['bundle_count'] = Bundle.objects.filter(user=context_dict['user']).count()
    context_dict['archive_bundles'] = context_dict['bundles'].filter(bundle_type='Archive')
    context_dict['external_bundles'] = context_dict['bundles'].filter(bundle_type='External')

    #This block checks that all bundles actually exist in the archive-
    #if not, it deletes that bundle from the database.
    for b in context_dict['bundles']:
        if os.path.isdir(b.directory()):
            pass
        else:
            b.remove_bundle()
            context_dict['bundles'] = Bundle.objects.filter(user=context_dict['user'])
            context_dict['bundle_count'] = Bundle.objects.filter(user=context_dict['user']).count()


    if request.user == context_dict['user']:
        return render(request, 'friends/bundle_hub.html', context_dict)

    else:
        return redirect('main:restricted_access')



# Keys holding the half-finished login while the user reads their email.  The
# session only names the pending user; it grants nothing on its own.
OTP_SESSION_KEY = 'pre_otp_user_id'
OTP_PURPOSE_KEY = 'pre_otp_purpose'
OTP_STARTED_KEY = 'pre_otp_started_at'
OTP_SESSION_TTL = timedelta(minutes=15)


# ---------------------------------------------------------------------------
# Throttles.  Same cache-counter shape the assistant app uses, so the DB-backed
# cache is shared across worker processes rather than resetting per process.
# ---------------------------------------------------------------------------

SIGNUP_PER_HOUR = 5
SIGNUP_PER_DAY = 20

PASSWORD_WINDOW = 15 * 60
PASSWORD_FAILURES_PER_ACCOUNT = 10
PASSWORD_FAILURES_PER_IP = 30


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


def _bump(key, timeout):
    """Increment a cache counter, tolerating the add/incr expiry race."""
    cache.add(key, 0, timeout=timeout)
    try:
        return cache.incr(key)
    except ValueError:
        return 1


def _signup_throttled(request):
    """
    Caps sign-up attempts per address.  Unique emails already stop one inbox
    being bombed repeatedly; this stops a script working through many.
    """
    ip = _client_ip(request)
    if _bump('signup-rl-hour-{0}'.format(ip), 3600) > SIGNUP_PER_HOUR:
        return True
    return _bump('signup-rl-day-{0}'.format(ip), 86400) > SIGNUP_PER_DAY


def _password_key(username):
    return 'pwd-rl-user-{0}'.format((username or '').lower()[:120])


def _password_throttled(request, username):
    """
    Password guessing had no ceiling at all: the OTP had an attempt cap but the
    password in front of it did not.  Counted per account and per address, and
    only failures count, so signing in normally never uses up the allowance.
    """
    if cache.get(_password_key(username), 0) >= PASSWORD_FAILURES_PER_ACCOUNT:
        return True
    return cache.get('pwd-rl-ip-{0}'.format(_client_ip(request)), 0) >= PASSWORD_FAILURES_PER_IP


def _record_password_failure(request, username):
    _bump(_password_key(username), PASSWORD_WINDOW)
    _bump('pwd-rl-ip-{0}'.format(_client_ip(request)), PASSWORD_WINDOW)


def _clear_password_failures(username):
    cache.delete(_password_key(username))


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


def _send_otp(user, code, purpose):
    """Emails a one-time code.  purpose is 'login' or 'register'."""
    subject = ("ELSA: confirm your email address" if purpose == 'register'
               else "ELSA: your sign-in code")

    # Carried with the message rather than linked, so the logo renders without
    # the reader having to approve remote images.
    logo = _elsa_logo_attachment()

    context = {
        'code': code,
        'purpose': purpose,
        'first_name': user.first_name,
        'ttl_minutes': int(OTP_TTL.total_seconds() // 60),
        'logo_cid': EMAIL_LOGO_CID if logo else '',
    }

    # Rendered from templates rather than an inline string so the markup is
    # editable without touching the view, and sent as multipart so clients that
    # refuse HTML still get a readable code.
    email = EmailMultiAlternatives(
        subject=subject,
        body=render_to_string('friends/email/otp_code.txt', context),
        from_email='atm-elsa@nmsu.edu',
        to=[user.email]
    )
    email.attach_alternative(render_to_string('friends/email/otp_code.html', context), 'text/html')

    if logo:
        # multipart/related tells the client the image belongs to the HTML part
        # rather than being a file the user is meant to download.
        email.mixed_subtype = 'related'
        email.attach(logo)

    email.send(fail_silently=True)


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

    user = authenticate(username=username, password=password)

    if not user:
        _record_password_failure(request, username)
        messages.error(request, "Invalid username or password.")
        return redirect('friends:login_password')

    _clear_password_failures(username)

    if not user.is_active:
        return render(request, 'friends/inactive.html', {'user': user})

    try:
        profile = user.userprofile
    except UserProfile.DoesNotExist:
        user_path = os.path.join(settings.ARCHIVE_DIR, user.username)
        makedirs(user_path)
        profile = UserProfile.objects.create(user=user, directory=user_path)

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


# friends/views.py

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
            if purpose == 'register':
                user.is_active = True
                user.save()

            if not user.is_active:
                _clear_otp_session(request)
                return render(request, 'friends/inactive.html', {'user':user})

            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            _clear_otp_session(request)
            _clear_login_session(request)

            # Now that they are properly signed in, this is the moment to ask.
            # Enrolling can only ever happen after authentication: a passkey
            # created by anyone who merely typed a username would be a
            # permanent way in to that account.
            if _should_offer_passkey(request, user):
                return redirect('friends:passkey_offer')

            return HttpResponseRedirect(reverse('main:index'))

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
    if not _should_offer_passkey(request, request.user):
        return HttpResponseRedirect(reverse('main:index'))

    return render(request, 'friends/passkey_offer.html', {
        'has_passkey_elsewhere': request.user.passkeys.exists(),
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

    return _write_passkey_hint(redirect('main:index'), hint)


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
# friends/views.py

# friends/views.py

def register(request):
    if request.method == 'POST' and _signup_throttled(request):
        messages.error(request, "Too many sign-up attempts from this connection. "
                                "Please try again later.")
        return HttpResponseRedirect(reverse('main:index'))

    user_form = UserForm(request.POST or None)
    profile_form = UserProfileForm(request.POST or None)

    if user_form.is_valid() and profile_form.is_valid():

        # One transaction: makedirs raising anything other than "already
        # exists" used to leave a user row behind with no archive directory.
        with transaction.atomic():
            user = user_form.save(commit=False)
            user.set_password(user.password)
            user.is_active = False
            user.save()

            user_path = os.path.join(settings.ARCHIVE_DIR, user.username)
            makedirs(user_path)

            profile = user.userprofile
            profile.agency = profile_form.cleaned_data.get('agency')
            profile.directory = user_path
            profile.save()

        return _stage_otp_challenge(request, user, profile, 'register')

    flag_invalid_fields(user_form, profile_form)

    return render(request, 'friends/register.html',
                  {'user_form': user_form, 'profile_form': profile_form})


# user account page
@login_required
def friend_useraccount(request):
    context_dict = {}
    context_dict['bundle_count'] = Bundle.objects.filter(user=request.user).count()
    context_dict['passkeys'] = request.user.passkeys.all()
    return render(request, 'friends/useraccount.html', context_dict)


# profile_settings NOTE:  It is important to NOT rename this as simply settings.  Since we import django.conf import settings, when our user goes to register, settings.ARCHIVE_DIR does not pull from our settings.py file.  Rather, Django comes to this function (if named settings) and notices there is no ARCHIVE_DIR declared here.  Big boo boo that cost me (k) a couple days to figure out.
@login_required
def friend_settings(request, pk_user):

    updated = False # This is a flag to determine if the user has updated their profile.
    context_dict = {}
    context_dict['userprofile'] = UserProfile.objects.get(pk=pk_user)
    context_dict['user'] = User.objects.get(userprofile=context_dict['userprofile'])
    
    user = context_dict['user']
    userProfile = context_dict['userprofile']

    first_form = UpdateNameFirstForm(request.POST or None)
    last_form = UpdateNameLastForm(request.POST or None)
    agency_form = UpdateAgencyForm(request.POST or None)
    email_form = UpdateEmailForm(request.POST or None)
    password_form = UpdatePasswordForm(request.POST or None)
    
    if first_form.is_valid():
        nameF = first_form.save()
        user.first_name = nameF.first_name
        user.save()
        updated = True

    if last_form.is_valid():
        nameL = last_form.save()
        user.last_name = nameL.last_name
        user.save()
        updated = True

    if email_form.is_valid():
        email = email_form.save()
        user.email = email.email
        user.save()
        updated = True

    if password_form.is_valid():
        pwdForm = password_form.save()
        if user.check_password(pwdForm.current_password):
            print("Valid")
            if pwdForm.new_password == pwdForm.confirm_password:
                print("Valid")
                user.set_password(pwdForm.new_password)
                user.save()
                updated = True

            else:
                    return render(request, 'friends/settings/mismatched_password.html', context_dict)
        else:
            return render(request, 'friends/settings/wrong_password.html', context_dict)

    if updated == True:
        email_user = EmailMessage(
            subject = "ELSA User Profile Updated",
            body = 'Your ELSA user profile has been updated. If you did not make this change, please visit https://atmos.nmsu.edu/elsa/contact/ to report this incident. Thank you for using ELSA! \n\nRegards,\nTeam ELSA',
            from_email = 'atm-elsa@nmsu.edu',
            to=[user.email]
        )
        email_user.send()

    if request.user == context_dict['user']:
        return render(request, 'friends/settings.html', context_dict)

    else:
        return redirect('main:restricted_access')
    

@login_required
def bundle_hub(request):
    bundles = Bundle.objects.filter(user=request.user)
    return render(request, 'friends/bundle_hub.html', {
        'bundles': bundles,
        'total_count': bundles.count(),
        'archive_count': bundles.filter(bundle_type='Archive').count(),
        'external_count': bundles.filter(bundle_type='External').count(),
    })

@login_required
def delete_bundles(request):
    if request.method == "POST":
        bundle_ids = request.POST.getlist('bundle_ids')
        if not bundle_ids:
            messages.warning(request, "No bundles were selected.")
            return redirect('friends:bundle_hub')  # redirect back to hub

        bundles = Bundle.objects.filter(id__in=bundle_ids, user=request.user)
        if not bundles.exists():
            messages.error(request, "You cannot delete bundles that do not belong to you.")
            return redirect('friends:bundle_hub')

        # call remove_bundle() for each before deleting
        for bundle in bundles:
            bundle.remove_bundle()
        count = bundles.count()
        bundles.delete()

        messages.success(request, f"{count} bundle(s) deleted successfully.")
        return redirect('friends:bundle_hub')

    return redirect('friends:bundle_hub')
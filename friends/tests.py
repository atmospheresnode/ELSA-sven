# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from datetime import timedelta
import json

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import UserProfile, OTP_MAX_ATTEMPTS, OTP_TTL
from .views import _mask_email


class CaptchaPassing(object):
    """
    The sign-up form carries a reCAPTCHA and a test cannot solve one, so the
    verification call is stubbed the way django-recaptcha documents.  Classes
    using this must call super().setUp() from their own setUp.
    """

    def setUp(self):
        from unittest import mock
        from django_recaptcha.client import RecaptchaResponse

        patcher = mock.patch('django_recaptcha.fields.client.submit',
                             return_value=RecaptchaResponse(is_valid=True))
        patcher.start()
        self.addCleanup(patcher.stop)
        super(CaptchaPassing, self).setUp()


class OTPModelTests(TestCase):
    """Covers the one-time code policy on UserProfile."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')
        self.profile = self.user.userprofile

    def test_generated_code_is_six_digits_and_not_stored_in_clear(self):
        code = self.profile.generate_otp()
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertNotIn(code, self.profile.otp_hash)

    def test_correct_code_verifies_once_only(self):
        code = self.profile.generate_otp()
        self.assertEqual(self.profile.verify_otp(code), (True, None))
        # Replaying the same code must fail: the hash is retired on success.
        self.assertEqual(self.profile.verify_otp(code), (False, 'expired'))

    def test_expired_code_is_rejected(self):
        code = self.profile.generate_otp()
        self.profile.otp_created_at = timezone.now() - OTP_TTL - timedelta(seconds=1)
        self.profile.save()
        self.assertEqual(self.profile.verify_otp(code), (False, 'expired'))

    def test_attempts_are_capped_and_the_code_is_burned(self):
        code = self.profile.generate_otp()
        wrong = '000000' if code != '000000' else '111111'

        for _ in range(OTP_MAX_ATTEMPTS - 1):
            self.assertEqual(self.profile.verify_otp(wrong), (False, 'invalid'))

        self.assertEqual(self.profile.verify_otp(wrong), (False, 'locked'))
        self.assertTrue(self.profile.otp_lock_remaining() > 0)

        # Even the right code is worthless once the account is locked.
        self.assertEqual(self.profile.verify_otp(code), (False, 'locked'))

    def test_resend_cooldown_reports_a_wait(self):
        self.profile.generate_otp()
        self.assertTrue(self.profile.seconds_until_resend() > 0)

        self.profile.otp_created_at = timezone.now() - timedelta(minutes=2)
        self.profile.save()
        self.assertEqual(self.profile.seconds_until_resend(), 0)

    def test_blank_submission_does_not_pass(self):
        self.profile.generate_otp()
        self.assertEqual(self.profile.verify_otp(None), (False, 'invalid'))
        self.assertEqual(self.profile.verify_otp(''), (False, 'invalid'))


class OTPViewTests(TestCase):
    """Covers the login -> verify handshake."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def _login(self):
        return self.client.post(reverse('friends:login'),
                                {'username': 'vhartwick', 'password': 'correct-horse'})

    def test_password_alone_does_not_authenticate(self):
        self._login()
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_verify_page_without_a_staged_session_is_turned_away(self):
        response = self.client.get(reverse('friends:otp_verify'))
        self.assertRedirects(response, reverse('main:index'))

    def test_full_login_handshake(self):
        self._login()
        self.assertIn('pre_otp_user_id', self.client.session)

        code = User.objects.get(pk=self.user.pk).userprofile.generate_otp()
        self.client.post(reverse('friends:otp_verify'), {'otp': code})

        self.assertEqual(int(self.client.session['_auth_user_id']), self.user.pk)
        self.assertNotIn('pre_otp_user_id', self.client.session)

    def test_login_does_not_reactivate_a_disabled_account(self):
        self._login()
        code = User.objects.get(pk=self.user.pk).userprofile.generate_otp()

        self.user.is_active = False
        self.user.save()

        self.client.post(reverse('friends:otp_verify'), {'otp': code})
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertFalse(User.objects.get(pk=self.user.pk).is_active)

    def test_verify_page_is_branded_and_segmented(self):
        self._login()
        response = self.client.get(reverse('friends:otp_verify'))
        body = response.content.decode()

        # The mark now sits above the card, so it is the dark logo on a light
        # background rather than the white one on a teal band.
        self.assertContains(response, 'thumbnail_ELSA_Logo-black.png')
        self.assertEqual(body.count('class="otp-box"'), 6)
        # The masked address confirms delivery without exposing the address.
        self.assertIn('v**@example.org', body)
        self.assertNotIn('v@example.org', body)

    def test_stale_staged_session_expires(self):
        self._login()
        session = self.client.session
        session['pre_otp_started_at'] = (timezone.now() - timedelta(hours=1)).isoformat()
        session.save()

        code = User.objects.get(pk=self.user.pk).userprofile.generate_otp()
        response = self.client.post(reverse('friends:otp_verify'), {'otp': code})

        self.assertRedirects(response, reverse('main:index'))
        self.assertNotIn('_auth_user_id', self.client.session)


class OTPEmailTests(CaptchaPassing, TestCase):
    """Covers the branded multipart code email."""

    def setUp(self):
        super(OTPEmailTests, self).setUp()
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse',
            email='vhartwick@nmsu.edu', first_name='Victoria')

    def test_login_email_is_multipart_and_branded(self):
        self.client.post(reverse('friends:login'),
                         {'username': 'vhartwick', 'password': 'correct-horse'})

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.subject, 'ELSA: your sign-in code')
        self.assertEqual(message.from_email, 'atm-elsa@nmsu.edu')

        # Plain text body plus an HTML alternative, so no client is left out.
        self.assertEqual(len(message.alternatives), 1)
        html, mimetype = message.alternatives[0]
        self.assertEqual(mimetype, 'text/html')

        self.assertIn('Educational Labeling System @Atmospheres', html)
        self.assertIn('Victoria', html)

        # Teal palette, not the darkslategray from the staff-notification emails.
        self.assertIn('#008080', html)
        self.assertNotIn('2F4F4F', html)
        self.assertIn('expires in 5 minutes', html)

        # The same code has to appear in both parts, or one audience gets a
        # message with no code in it.
        import re
        code = re.search(r'\b(\d{6})\b', message.body).group(1)
        self.assertIn(code, html)

    def test_logo_travels_with_the_message(self):
        self.client.post(reverse('friends:login'),
                         {'username': 'vhartwick', 'password': 'correct-horse'})

        message = mail.outbox[0]
        html = message.alternatives[0][0]

        # Referenced by CID, not by URL, so nothing has to be fetched and no
        # client can decline to show it.
        self.assertIn('src="cid:elsa_logo"', html)
        self.assertNotIn('<img src="http', html)
        self.assertEqual(message.mixed_subtype, 'related')

        parts = [p for p in message.attachments if hasattr(p, 'get_content_type')]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0].get_content_type(), 'image/png')
        self.assertEqual(parts[0].get('Content-ID'), '<elsa_logo>')
        self.assertEqual(parts[0].get_content_disposition(), 'inline')

    def test_logo_asset_stays_small(self):
        # The whole point of the pre-built asset: every message carries this,
        # so a regression to the 70KB source would bloat every send.
        from friends.views import _elsa_logo_png
        self.assertLess(len(_elsa_logo_png()), 12 * 1024)

    def test_code_still_goes_out_if_the_logo_is_unreadable(self):
        # A decorative image must never be able to break sign-in.
        from unittest import mock
        with mock.patch('friends.views._elsa_logo_png', side_effect=IOError('gone')):
            self.client.post(reverse('friends:login'),
                             {'username': 'vhartwick', 'password': 'correct-horse'})

        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('<img', html)
        self.assertIn('Educational Labeling System @Atmospheres', html)
        self.assertRegex(mail.outbox[0].body, r'\b\d{6}\b')

    def test_registration_email_uses_the_signup_wording(self):
        self.client.post(reverse('friends:register'), {
            'username': 'newbie', 'password': 'correct-horse-battery',
            'email': 'newbie@nmsu.edu', 'first_name': 'New', 'last_name': 'Person',
            'agency': 'nasa:pds', 'g-recaptcha-response': 'PASSED',
        })

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].subject, 'ELSA: confirm your email address')
        self.assertIn('Welcome to ELSA', mail.outbox[0].body)


class MaskEmailTests(TestCase):

    def test_long_local_part_keeps_first_two_and_last(self):
        self.assertEqual(_mask_email('vhartwick@nmsu.edu'), 'vh******k@nmsu.edu')

    def test_short_local_parts_are_still_masked(self):
        # A short name must not survive intact just because there is little of it.
        self.assertEqual(_mask_email('abcd@nmsu.edu'), 'a***@nmsu.edu')
        self.assertEqual(_mask_email('abc@nmsu.edu'), 'a**@nmsu.edu')
        self.assertEqual(_mask_email('ab@nmsu.edu'), 'a**@nmsu.edu')
        self.assertEqual(_mask_email('a@nmsu.edu'), 'a**@nmsu.edu')

    def test_junk_input_yields_nothing(self):
        self.assertEqual(_mask_email(''), '')
        self.assertEqual(_mask_email(None), '')
        self.assertEqual(_mask_email('not-an-address'), '')


class PasskeyModelTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def test_transport_list_handles_blank(self):
        from .models import Passkey
        key = Passkey.objects.create(user=self.user, credential_id='abc', public_key=b'k')
        self.assertEqual(key.transport_list(), [])

        key.transports = 'internal,hybrid'
        self.assertEqual(key.transport_list(), ['internal', 'hybrid'])

    def test_mark_used_advances_counter_and_stamps_time(self):
        from .models import Passkey
        key = Passkey.objects.create(user=self.user, credential_id='abc', public_key=b'k')
        self.assertIsNone(key.last_used_at)

        key.mark_used(7)
        key.refresh_from_db()
        self.assertEqual(key.sign_count, 7)
        self.assertIsNotNone(key.last_used_at)


class PasskeyRoutingTests(TestCase):
    """The login fork, and who is allowed to reach the ceremony endpoints."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def _login(self):
        return self.client.post(reverse('friends:login'),
                                {'username': 'vhartwick', 'password': 'correct-horse'})

    def _give_passkey(self):
        from .models import Passkey
        return Passkey.objects.create(
            user=self.user, credential_id='cred-abc', public_key=b'key', name='Laptop')

    def test_without_a_passkey_login_falls_back_to_an_email_code(self):
        response = self._login()
        self.assertRedirects(response, reverse('friends:otp_verify'))
        self.assertEqual(len(mail.outbox), 1)

    def test_with_a_passkey_login_goes_to_the_passkey_page_and_sends_no_email(self):
        from friends import views
        from django.http import HttpResponse
        self._give_passkey()

        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

        response = self._login()

        self.assertRedirects(response, reverse('friends:passkey_verify'))
        self.assertEqual(mail.outbox, [])
        # Still not authenticated: the password alone proves nothing.
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_passkey_page_needs_a_staged_session(self):
        response = self.client.get(reverse('friends:passkey_verify'))
        self.assertRedirects(response, reverse('main:index'))

    def test_email_fallback_from_the_passkey_page_sends_a_code(self):
        self._give_passkey()
        self._login()

        response = self.client.post(reverse('friends:passkey_use_email'))
        self.assertRedirects(response, reverse('friends:otp_verify'))
        self.assertEqual(len(mail.outbox), 1)

    def test_registration_endpoints_require_login(self):
        for name in ('friends:passkey_register_options', 'friends:passkey_register_verify'):
            response = self.client.post(reverse(name))
            self.assertEqual(response.status_code, 302)
            self.assertIn('login', response['Location'])

    def test_ceremony_endpoints_reject_get(self):
        for name in ('friends:passkey_auth_options', 'friends:passkey_auth_verify'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)

    def test_auth_verify_rejects_a_malformed_body(self):
        response = self.client.post(reverse('friends:passkey_auth_verify'),
                                    data='not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_a_user_cannot_delete_someone_elses_passkey(self):
        key = self._give_passkey()
        from .models import Passkey

        intruder = User.objects.create_user(
            username='mallory', password='correct-horse', email='m@example.org')
        self.client.force_login(intruder)

        response = self.client.post(reverse('friends:passkey_delete', args=[key.pk]))
        self.assertRedirects(response, reverse('main:restricted_access'),
                             fetch_redirect_response=False)
        self.assertTrue(Passkey.objects.filter(pk=key.pk).exists())

    def test_owner_can_delete_their_passkey(self):
        key = self._give_passkey()
        from .models import Passkey

        self.client.force_login(self.user)
        self.client.post(reverse('friends:passkey_delete', args=[key.pk]))
        self.assertFalse(Passkey.objects.filter(pk=key.pk).exists())


class PasskeyCeremonyTests(TestCase):
    """Challenge handling and option shape, without a real authenticator."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def test_registration_options_require_a_discoverable_credential(self):
        # Discoverable is what makes passwordless sign-in possible, and it is
        # fixed at enrollment, so a regression here is expensive.
        self.client.force_login(self.user)
        options = self.client.post(reverse('friends:passkey_register_options')).json()

        self.assertEqual(options['authenticatorSelection']['residentKey'], 'required')
        self.assertTrue(options['authenticatorSelection']['requireResidentKey'])
        self.assertEqual(options['rp']['id'], 'testserver')
        self.assertEqual(options['user']['name'], 'vhartwick')

    def test_registration_options_exclude_existing_credentials(self):
        from .models import Passkey
        from webauthn.helpers import bytes_to_base64url
        Passkey.objects.create(user=self.user, public_key=b'k',
                               credential_id=bytes_to_base64url(b'existing'))

        self.client.force_login(self.user)
        options = self.client.post(reverse('friends:passkey_register_options')).json()
        self.assertEqual(len(options['excludeCredentials']), 1)

    def test_passwordless_options_list_no_credentials(self):
        # An empty allow list is what tells the browser to offer whatever
        # discoverable passkeys it holds.
        options = self.client.post(reverse('friends:passkey_auth_options')).json()
        self.assertFalse(options.get('allowCredentials'))
        self.assertEqual(options['userVerification'], 'required')

    def test_options_never_reveal_which_credentials_an_account_owns(self):
        # Options are handed out before anyone has proved who they are, so
        # naming the account's credentials would let a stranger learn which
        # usernames own passkeys and how many.  The account binding is enforced
        # at verification instead.
        from .models import Passkey
        from webauthn.helpers import bytes_to_base64url
        Passkey.objects.create(user=self.user, public_key=b'k',
                               credential_id=bytes_to_base64url(b'mine'))

        self.client.post(reverse('friends:login'),
                         {'username': 'vhartwick', 'password': 'correct-horse'})
        options = self.client.post(reverse('friends:passkey_auth_options')).json()

        self.assertFalse(options.get('allowCredentials'))
        self.assertNotIn('mine', json.dumps(options))

    def test_a_challenge_is_single_use(self):
        from . import passkeys
        request = self.client.request().wsgi_request
        request.session = self.client.session

        passkeys.stash_challenge(request, passkeys.AUTH_CHALLENGE_KEY, b'a-challenge')
        self.assertEqual(passkeys.take_challenge(request, passkeys.AUTH_CHALLENGE_KEY), b'a-challenge')
        # Replaying the same response must not verify twice.
        self.assertIsNone(passkeys.take_challenge(request, passkeys.AUTH_CHALLENGE_KEY))

    def test_verification_without_a_challenge_is_refused(self):
        from . import passkeys
        request = self.client.request().wsgi_request
        request.session = self.client.session

        passkey, error = passkeys.verify_authentication(request, {'rawId': 'whatever'})
        self.assertIsNone(passkey)
        self.assertIn('expired', error)

    def test_unknown_credential_is_refused(self):
        from . import passkeys
        request = self.client.request().wsgi_request
        request.session = self.client.session

        passkeys.stash_challenge(request, passkeys.AUTH_CHALLENGE_KEY, b'a-challenge')
        passkey, error = passkeys.verify_authentication(request, {'rawId': 'no-such-credential'})
        self.assertIsNone(passkey)
        self.assertIn('not registered', error)

    def test_a_passkey_cannot_satisfy_a_challenge_staged_for_another_account(self):
        from . import passkeys
        from .models import Passkey

        other = User.objects.create_user(
            username='mallory', password='correct-horse', email='m@example.org')
        key = Passkey.objects.create(user=other, credential_id='mallory-cred', public_key=b'k')

        request = self.client.request().wsgi_request
        request.session = self.client.session
        passkeys.stash_challenge(request, passkeys.AUTH_CHALLENGE_KEY, b'a-challenge')

        passkey, error = passkeys.verify_authentication(
            request, {'rawId': key.credential_id}, user=self.user)

        self.assertIsNone(passkey)
        self.assertIn('different account', error)


class PasskeyRenderTests(TestCase):
    """Every page touched by the passkey work must actually render."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def test_landing_page_asks_only_who_you_are(self):
        # FIDO's guidance is explicit that a passkey button and a password form
        # should not share a screen.  The landing page identifies, nothing more.
        response = self.client.get(reverse('main:index'))
        body = response.content.decode()

        self.assertContains(response, 'js/passkeys.js')
        self.assertContains(response, 'Continue')
        self.assertIn('name="username"', body)
        self.assertNotIn('name="password"', body)
        self.assertNotIn('Sign in with a passkey', body)

    def test_account_page_lists_passkeys(self):
        from .models import Passkey
        self.client.force_login(self.user)

        response = self.client.get(reverse('friends:useraccount'))
        self.assertContains(response, 'Add a passkey')
        self.assertContains(response, 'You have no passkeys yet')

        Passkey.objects.create(user=self.user, credential_id='c1',
                               public_key=b'k', name='Work laptop')
        response = self.client.get(reverse('friends:useraccount'))
        self.assertContains(response, 'Work laptop')
        self.assertContains(response, 'deletePasskeyModal')

    def test_passkey_verify_page_renders_with_an_email_escape_hatch(self):
        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='c1', public_key=b'k')
        self.client.post(reverse('friends:login'),
                         {'username': 'vhartwick', 'password': 'correct-horse'})

        response = self.client.get(reverse('friends:passkey_verify'))
        self.assertContains(response, 'Use my passkey')
        self.assertContains(response, 'Email me a code instead')
        self.assertContains(response, 'vhartwick')

    def test_username_field_invites_passkey_autofill(self):
        # Autofill is FIDO's top-ranked way to offer a passkey, and this token
        # is the whole mechanism.  Losing it silently removes the best path.
        body = self.client.get(reverse('main:index')).content.decode()
        self.assertIn('autocomplete="username webauthn"', body)


class IdentifierFirstFlowTests(TestCase):
    """
    The sign-in flow itself: name the account, then one method per screen.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def _identify(self, username='vhartwick'):
        return self.client.post(reverse('friends:login_identify'), {'username': username})

    def _give_passkey(self):
        from .models import Passkey
        return Passkey.objects.create(
            user=self.user, credential_id='cred-abc', public_key=b'key', name='Laptop')

    def test_a_password_only_account_is_sent_to_the_password_screen(self):
        response = self._identify()
        self.assertRedirects(response, reverse('friends:login_password'))

    def test_a_passkey_account_on_an_unknown_device_gets_the_password_screen(self):
        # Owning a passkey is not enough: it has to be a device that has used
        # one, or the prompt cannot be answered here.
        self._give_passkey()
        self.assertRedirects(self._identify(), reverse('friends:login_password'))

    def test_a_passkey_account_on_a_known_device_gets_the_passkey_screen(self):
        from friends import views
        from django.http import HttpResponse
        self._give_passkey()

        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

        self.assertRedirects(self._identify(), reverse('friends:login_passkey'))

    def test_an_unknown_username_looks_exactly_like_a_password_account(self):
        # Otherwise the first screen becomes an account-existence oracle.
        known = self._identify('vhartwick')
        unknown = self._identify('no-such-person')
        self.assertEqual(known['Location'], unknown['Location'])

    def test_an_inactive_account_does_not_get_the_passkey_route(self):
        self._give_passkey()
        self.user.is_active = False
        self.user.save()
        self.assertRedirects(self._identify(), reverse('friends:login_password'))

    def test_method_screens_need_an_identified_account(self):
        for name in ('friends:login_password', 'friends:login_passkey'):
            response = self.client.get(reverse(name))
            self.assertRedirects(response, reverse('main:index'))

    def test_identify_rejects_a_blank_username(self):
        response = self.client.post(reverse('friends:login_identify'), {'username': '   '})
        self.assertRedirects(response, reverse('main:index'))

    def test_password_screen_carries_the_username_for_password_managers(self):
        self._identify()
        body = self.client.get(reverse('friends:login_password')).content.decode()

        self.assertIn('vhartwick', body)
        self.assertIn('autocomplete="current-password"', body)
        # A hidden username field is what lets a manager match and save it.
        self.assertIn('autocomplete="username"', body)

    def test_password_screen_offers_the_way_back_only_when_there_is_one(self):
        self._identify()
        self.assertNotContains(self.client.get(reverse('friends:login_password')),
                               'Use your passkey instead')

        self._give_passkey()
        self._identify()
        self.client.post(reverse('friends:login_use_password'))
        self.assertContains(self.client.get(reverse('friends:login_password')),
                            'Use your passkey instead')

    def test_correct_password_still_lands_on_a_second_factor(self):
        self._identify()
        response = self.client.post(reverse('friends:login_password'),
                                    {'password': 'correct-horse'})

        self.assertRedirects(response, reverse('friends:otp_verify'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_wrong_password_returns_to_the_password_screen(self):
        self._identify()
        response = self.client.post(reverse('friends:login_password'), {'password': 'nope'})

        self.assertRedirects(response, reverse('friends:login_password'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_declining_the_passkey_falls_through_to_an_emailed_code(self):
        # Somebody without their authenticator must not be bounced straight
        # back to the passkey screen after their password.
        self._give_passkey()
        self._identify()
        self.client.post(reverse('friends:login_use_password'))

        response = self.client.post(reverse('friends:login_password'),
                                    {'password': 'correct-horse'})

        self.assertRedirects(response, reverse('friends:otp_verify'))
        self.assertEqual(len(mail.outbox), 1)

    def _mark_device_known(self):
        from friends import views
        from django.http import HttpResponse
        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

    def test_without_declining_a_passkey_holder_gets_the_passkey_second_factor(self):
        self._give_passkey()
        self._mark_device_known()
        response = self.client.post(reverse('friends:login'),
                                    {'username': 'vhartwick', 'password': 'correct-horse'})

        self.assertRedirects(response, reverse('friends:passkey_verify'))
        self.assertEqual(mail.outbox, [])

    def test_passkey_screen_names_the_account_and_offers_a_way_out(self):
        self._give_passkey()
        self._identify()
        response = self.client.get(reverse('friends:login_passkey'))

        self.assertContains(response, 'vhartwick')
        self.assertContains(response, 'Use your password instead')
        # "Change" says what the click does; "Not you?" only implied it.
        self.assertContains(response, 'Change')


class AuthStylingTests(TestCase):
    """
    The sign-in screens must stay one component.  These caught a card styled
    with plain Bootstrap next to cards styled with auth.css, and a CSS variable
    left dangling after a rename.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def _screens(self):
        screens = {'landing': self.client.get(reverse('main:index')).content.decode()}

        self.client.post(reverse('friends:login_identify'), {'username': 'vhartwick'})
        screens['password'] = self.client.get(
            reverse('friends:login_password')).content.decode()

        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='c1', public_key=b'k')
        self.client.post(reverse('friends:login_identify'), {'username': 'vhartwick'})
        screens['passkey'] = self.client.get(
            reverse('friends:login_passkey')).content.decode()

        return screens

    def test_every_screen_uses_the_shared_stylesheet(self):
        for name, body in self._screens().items():
            self.assertIn('css/auth.css', body, '%s screen is not on auth.css' % name)
            # Brand above the card, card holds only the task.
            self.assertIn('class="auth-brand"', body, '%s screen lost its mark' % name)
            self.assertIn('class="auth-heading"', body, '%s screen lost its heading' % name)

    def test_landing_card_matches_the_screens_that_follow_it(self):
        screens = self._screens()
        for element in ('auth-card', 'btn-elsa', 'form-control-elsa'):
            self.assertIn(element, screens['landing'])
            self.assertIn(element, screens['password'])

        # The old mismatched Bootstrap button is gone.
        self.assertNotIn('btn btn-light text-light', screens['landing'])

    def test_no_screen_references_an_undefined_css_variable(self):
        import re
        css = open('static/css/auth.css').read()
        defined = set(re.findall(r'(--elsa-[a-z-]+)\s*:', css))

        for name, body in self._screens().items():
            inline = set(re.findall(r'(--elsa-[a-z-]+)\s*:', body))
            used = set(re.findall(r'var\((--elsa-[a-z-]+)\)', body))
            self.assertEqual(used - defined - inline, set(),
                             '%s screen uses an undefined variable' % name)

    def test_password_field_reserves_room_for_the_reveal_button(self):
        screens = self._screens()
        self.assertIn('field-wrap', screens['password'])
        self.assertIn('padding-right: 3rem', open('static/css/auth.css').read())

    def test_username_field_can_be_cleared(self):
        body = self._screens()['landing']
        self.assertIn('id="clear-username"', body)
        self.assertIn('Clear username', body)

    def test_the_brand_sits_outside_the_card(self):
        # Amazon's lesson: the mark identifies the site, the card holds only
        # the task.  Branding inside the card competed with the input.
        for name, body in self._screens().items():
            brand_at = body.index('class="auth-brand"')
            card_at = body.index('card auth-card')
            self.assertLess(brand_at, card_at, '%s screen has the mark inside the card' % name)


class HiddenUsernameFieldTests(TestCase):
    """
    The password screen repeats the username in a hidden input so password
    managers can attach the credential to an account.  That field is posted
    with the form, so what matters is that the server never believes it.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')
        self.victim = User.objects.create_user(
            username='lyle', password='different-horse', email='l@example.org')

    def _identify(self, username='vhartwick'):
        return self.client.post(reverse('friends:login_identify'), {'username': username})

    def test_a_tampered_hidden_username_is_ignored(self):
        # The field is editable in the DOM, so a posted username must count for
        # nothing.  The view takes the account from the session instead.
        self._identify('vhartwick')

        self.client.post(reverse('friends:login_password'),
                         {'username': 'lyle', 'password': 'different-horse'})

        # Lyle's password against Lyle's name still fails, because the session
        # says this sign-in is Victoria's.
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertNotIn('pre_otp_user_id', self.client.session)

    def test_the_session_account_is_the_one_that_signs_in(self):
        self._identify('vhartwick')
        self.client.post(reverse('friends:login_password'),
                         {'username': 'lyle', 'password': 'correct-horse'})

        # Victoria's password succeeded, and it is Victoria who is staged.
        self.assertEqual(self.client.session['pre_otp_user_id'], self.user.pk)

    def test_the_field_is_a_real_text_input_hidden_with_css(self):
        # type="hidden" is ignored by password managers, and a visually-hidden
        # clip would leave the username in the accessibility tree twice.
        self._identify()
        body = self.client.get(reverse('friends:login_password')).content.decode()

        self.assertIn('autocomplete="username"', body)
        self.assertIn('class="pm-username"', body)
        self.assertNotIn('type="hidden" name="username"', body)
        self.assertNotIn('aria-hidden="true" readonly', body)
        self.assertIn('.pm-username { display: none; }', open('static/css/auth.css').read())


class SourceLeakTests(TestCase):
    """
    Django strips {# #} at render; <!-- --> ships to the browser.  Internal
    design and security reasoning belongs in the first kind.  These pages are
    unauthenticated, so anything left in them is world readable.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def _pages(self):
        pages = {'landing': self.client.get(reverse('main:index')).content.decode()}

        self.client.post(reverse('friends:login_identify'), {'username': 'vhartwick'})
        pages['password'] = self.client.get(
            reverse('friends:login_password')).content.decode()

        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='c1', public_key=b'k')
        self.client.post(reverse('friends:login_identify'), {'username': 'vhartwick'})
        pages['passkey'] = self.client.get(
            reverse('friends:login_passkey')).content.decode()

        self.client.post(reverse('friends:login'),
                         {'username': 'vhartwick', 'password': 'correct-horse'})
        self.client.post(reverse('friends:passkey_use_email'))
        pages['otp'] = self.client.get(reverse('friends:otp_verify')).content.decode()

        return pages

    def test_no_page_source_narrates_our_security_decisions(self):
        # Not a control in itself, but handing an attacker a description of the
        # controls is free help, and it reads as unfinished work.
        banned = ['attacker', 'stranger', 'enumerat', 'oracle', 'tamper',
                  'deliberately ignored', 'FIDO Alliance design guidelines',
                  'password managers can match']

        for name, body in self._pages().items():
            lowered = body.lower()
            for phrase in banned:
                self.assertNotIn(phrase.lower(), lowered,
                                 '%s page source leaks internal reasoning: %r'
                                 % (name, phrase))

    def test_the_hidden_username_rationale_never_reaches_the_browser(self):
        # This one is the sharpest: it explains both why the field is there and
        # that its value is ignored.
        password_page = self._pages()['password']
        self.assertIn('class="pm-username"', password_page)
        self.assertNotIn('<!--', password_page.split('</head>')[-1].split('pm-username')[0][-400:])
        self.assertNotIn('test_a_tampered_hidden_username_is_ignored', password_page)

    def test_shipped_comments_stay_short(self):
        # A long comment in page source is either a design debate or a leak.
        import re
        for name, body in self._pages().items():
            for comment in re.findall(r'<!--(.*?)-->', body, re.S):
                words = len(comment.split())
                self.assertLess(words, 30,
                                '%s page ships a %d-word comment: %r'
                                % (name, words, ' '.join(comment.split())[:80]))


class TemplateCommentTests(TestCase):
    """
    Django's {# #} is single-line only.  Put a newline inside one and it stops
    being a comment: the whole block is emitted verbatim and shows up as text
    on the page.  Multi-line notes must use {% comment %}.
    """

    def test_no_template_uses_a_multiline_hash_comment(self):
        import os
        import re

        offenders = []
        for root, _dirs, files in os.walk('templates'):
            for name in files:
                if not name.endswith('.html'):
                    continue
                path = os.path.join(root, name)
                with open(path, encoding='utf-8', errors='ignore') as handle:
                    body = handle.read()
                for match in re.finditer(r'\{#(.*?)#\}', body, re.S):
                    if '\n' in match.group(1):
                        line = body[:match.start()].count('\n') + 1
                        offenders.append('%s:%d' % (path, line))

        self.assertEqual(offenders, [],
                         'multi-line {# #} renders as visible text, use '
                         '{%% comment %%}: %s' % ', '.join(offenders))


class DeviceAwarePasskeyRoutingTests(TestCase):
    """
    A passkey lives on the device that made it.  Someone with one on their PC
    signing in from a new tablet must not be sent to a prompt that tablet
    cannot answer.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')
        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='pc-key',
                               public_key=b'k', name='Windows PC')

    def _identify(self):
        return self.client.post(reverse('friends:login_identify'),
                                {'username': 'vhartwick'})

    def _mark_device_known(self):
        # Stand in for a passkey having previously succeeded in this browser.
        from friends import views
        from django.http import HttpResponse
        response = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        cookie = response.cookies[views.PASSKEY_HINT_COOKIE]
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = cookie.value

    def test_a_new_device_goes_to_the_password_screen(self):
        # The tablet has never used a passkey, so it must not be shown one.
        self.assertRedirects(self._identify(), reverse('friends:login_password'))

    def test_a_device_that_has_used_a_passkey_gets_the_passkey_screen(self):
        self._mark_device_known()
        self.assertRedirects(self._identify(), reverse('friends:login_passkey'))

    def test_the_password_screen_still_offers_the_passkey_route(self):
        # Synced passkeys exist, so a new browser may still hold one.  There
        # has to be a manual way to reach it.
        self._identify()
        self.assertContains(self.client.get(reverse('friends:login_password')),
                            'Use your passkey instead')

    def test_the_second_factor_also_respects_the_device(self):
        # Caught by walking the flow: the identify step was fixed but the
        # password step still sent an unknown device to a passkey prompt.
        self._identify()
        response = self.client.post(reverse('friends:login_password'),
                                    {'password': 'correct-horse'})

        self.assertRedirects(response, reverse('friends:otp_verify'))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_known_device_still_gets_the_passkey_second_factor(self):
        self._mark_device_known()
        self.client.post(reverse('friends:login'),
                         {'username': 'vhartwick', 'password': 'correct-horse'})
        self.assertEqual(mail.outbox, [])

    def test_a_forged_hint_grants_nothing(self):
        # The cookie only picks a screen.  Claiming a device it does not have
        # just lands on the passkey screen, which still demands a real assertion.
        self.client.cookies['elsa_passkey_hint'] = 'not-a-valid-signed-value'
        self.assertRedirects(self._identify(), reverse('friends:login_password'))


class PasskeyOfferTests(TestCase):
    """The post-login invitation to enroll on a new device."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='vhartwick', password='correct-horse', email='v@example.org')

    def _sign_in_with_a_code(self):
        self.client.post(reverse('friends:login_identify'), {'username': 'vhartwick'})
        self.client.post(reverse('friends:login_password'), {'password': 'correct-horse'})
        code = User.objects.get(pk=self.user.pk).userprofile.generate_otp()
        return self.client.post(reverse('friends:otp_verify'), {'otp': code})

    def test_the_offer_comes_after_a_completed_sign_in(self):
        response = self._sign_in_with_a_code()

        self.assertRedirects(response, reverse('friends:passkey_offer'))
        self.assertIn('_auth_user_id', self.client.session)

    def test_the_offer_is_unreachable_without_signing_in(self):
        # Enrolling adds a permanent way in, so it must sit behind a completed
        # authentication rather than stand in for one.
        response = self.client.get(reverse('friends:passkey_offer'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_not_now_is_remembered(self):
        self._sign_in_with_a_code()
        self.client.post(reverse('friends:passkey_offer_dismiss'))

        self.client.get(reverse('friends:logout'))
        response = self._sign_in_with_a_code()

        # Second sign-in on the same browser goes straight in.
        self.assertRedirects(response, reverse('main:index'))

    def test_the_offer_explains_why_a_second_device_needs_its_own(self):
        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='pc-key', public_key=b'k')
        self._sign_in_with_a_code()

        self.assertContains(self.client.get(reverse('friends:passkey_offer')),
                            'needs its own')

    def test_a_device_that_knows_a_passkey_is_never_offered_one(self):
        from friends import views
        from django.http import HttpResponse
        from .models import Passkey

        # The account must actually hold one.  A hint claiming otherwise is the
        # stale state that DeletedPasskeyTests covers.
        Passkey.objects.create(user=self.user, credential_id='c', public_key=b'k')

        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

        self.assertRedirects(self._sign_in_with_a_code(), reverse('main:index'))


class MessageLeakTests(TestCase):
    """
    Messages default to cookie storage, which outlives the session flush that
    logout performs.  A message nobody rendered therefore follows the user out
    to the public landing page.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='rupak', password='correct-horse', email='r@nmsu.edu')

    def _delete_a_passkey(self):
        from .models import Passkey
        key = Passkey.objects.create(user=self.user, credential_id='c',
                                     public_key=b'k', name='My Win32')
        self.client.force_login(self.user)
        self.client.post(reverse('friends:passkey_delete', args=[key.pk]))

    def test_the_account_page_shows_its_own_feedback(self):
        # The root cause: nothing on this page consumed messages, so confirming
        # a delete was invisible here and escaped to the next page instead.
        self._delete_a_passkey()
        self.assertContains(self.client.get(reverse('friends:useraccount')),
                            'My Win32')

    def test_a_removed_passkey_is_not_announced_on_the_public_page(self):
        self._delete_a_passkey()
        self.client.get(reverse('friends:useraccount'))
        self.client.get(reverse('friends:logout'))

        landing = self.client.get(reverse('main:index')).content.decode()
        self.assertNotIn('My Win32', landing)

    def test_logout_drains_messages_nobody_read(self):
        # Belt and braces: even a message the page never rendered must not
        # survive the logout.  On a shared machine the next person is not the
        # person it was written for.
        self._delete_a_passkey()
        self.client.get(reverse('friends:logout'))

        landing = self.client.get(reverse('main:index')).content.decode()
        self.assertNotIn('My Win32', landing)
        self.assertNotIn('removed', landing)


class DeletedPasskeyTests(TestCase):
    """
    Removing a passkey from ELSA cannot reach into the authenticator that holds
    it.  The browser keeps offering it, and the per-browser hint keeps claiming
    this device knows one.  Both have to be handled.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='rupak', password='correct-horse', email='r@nmsu.edu')

    def _mark_device_known(self):
        from friends import views
        from django.http import HttpResponse
        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

    def _delete_last_passkey(self):
        from .models import Passkey
        key = Passkey.objects.create(user=self.user, credential_id='pc',
                                     public_key=b'k', name='My Win32')
        self._mark_device_known()
        self.client.force_login(self.user)
        self.client.post(reverse('friends:passkey_delete', args=[key.pk]))
        self.client.get(reverse('friends:logout'))

    def _full_login(self):
        self.client.post(reverse('friends:login_identify'), {'username': 'rupak'})
        self.client.post(reverse('friends:login_password'), {'password': 'correct-horse'})
        code = User.objects.get(pk=self.user.pk).userprofile.generate_otp()
        return self.client.post(reverse('friends:otp_verify'), {'otp': code})

    def test_deleting_the_last_passkey_brings_the_setup_offer_back(self):
        # The bug: the hint still said this browser knew a passkey, so signing
        # in with a password went straight through with no offer.
        self._delete_last_passkey()
        self.assertRedirects(self._full_login(), reverse('friends:passkey_offer'))

    def test_a_stale_hint_cannot_suppress_the_offer(self):
        # Same check from the other side, for browsers where the delete did not
        # happen and whose cookie was therefore never rewritten.
        self._delete_last_passkey()
        self._mark_device_known()
        self.assertRedirects(self._full_login(), reverse('friends:passkey_offer'))

    def test_an_orphaned_credential_is_flagged_so_the_browser_can_drop_it(self):
        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='live', public_key=b'k')

        # Options first, so a challenge is staged and we reach the lookup.
        self.client.post(reverse('friends:passkey_auth_options'))
        response = self.client.post(
            reverse('friends:passkey_auth_verify'),
            data=json.dumps({'credential': {'rawId': 'deleted-one'}}),
            content_type='application/json')

        body = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(body['reason'], 'unknown_credential')
        self.assertEqual(body['credential_id'], 'deleted-one')

    def test_a_credential_we_still_hold_is_not_flagged_as_unknown(self):
        from .models import Passkey
        Passkey.objects.create(user=self.user, credential_id='live', public_key=b'k')

        self.client.post(reverse('friends:passkey_auth_options'))
        response = self.client.post(
            reverse('friends:passkey_auth_verify'),
            data=json.dumps({'credential': {'rawId': 'live'}}),
            content_type='application/json')

        # It fails for other reasons, but must not tell the browser to bin it.
        self.assertNotIn('reason', response.json())


class SignInHintTests(TestCase):
    """
    The passkey note on the sign-in page must only appear where it is true.
    Right now no one has enrolled, so an unconditional note promises every
    single visitor something that will not happen.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='rupak', password='correct-horse', email='r@nmsu.edu')

    def test_a_fresh_browser_is_told_nothing_about_passkeys(self):
        body = self.client.get(reverse('main:index')).content.decode()
        self.assertNotIn('auth-hint', body)

    def test_a_browser_that_has_used_one_gets_the_note(self):
        from friends import views
        from django.http import HttpResponse
        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

        body = self.client.get(reverse('main:index')).content.decode()
        self.assertIn('auth-hint', body)
        self.assertIn('offer your passkey here', body)

    def test_the_note_does_not_instruct_a_tap(self):
        # The field is autofocused, so there is nothing to tap, and "tap" is
        # wrong on a desktop anyway.
        from friends import views
        from django.http import HttpResponse
        marked = views._write_passkey_hint(
            HttpResponse(), {'known': [self.user.pk], 'declined': {}})
        self.client.cookies[views.PASSKEY_HINT_COOKIE] = \
            marked.cookies[views.PASSKEY_HINT_COOKIE].value

        body = self.client.get(reverse('main:index')).content.decode()
        self.assertNotIn('Tap the username', body)
        self.assertNotIn('Set up a passkey?', body)


class RegistrationPageTests(CaptchaPassing, TestCase):
    """
    The sign-up page rendered no validation errors at all: a taken username or
    a malformed address just returned the form with nothing explaining why.
    """

    def _submit(self, **overrides):
        data = {'first_name': 'Rupak', 'last_name': 'Dey', 'username': 'newbie',
                'email': 'newbie@nmsu.edu', 'password': 'correct-horse',
                'agency': 'nasa:pds', 'g-recaptcha-response': 'PASSED'}
        data.update(overrides)
        return self.client.post(reverse('friends:register'), data)

    def test_a_taken_username_is_reported_on_the_page(self):
        User.objects.create_user('taken', 't@nmsu.edu', 'pw')
        response = self._submit(username='taken')

        self.assertContains(response, 'already exists')
        self.assertContains(response, 'Please check the highlighted fields')

    def test_a_bad_address_is_reported_on_the_page(self):
        self.assertContains(self._submit(email='not-an-email'),
                            'Enter a valid email address')

    def test_a_rejected_field_is_visibly_marked(self):
        # Error text with no styling on the field itself is half a fix.
        response = self._submit(email='not-an-email')
        self.assertContains(response, 'is-invalid')

    def test_the_page_uses_the_shared_auth_shell(self):
        response = self.client.get(reverse('friends:register'))
        for element in ('css/auth.css', 'auth-card', 'auth-brand', 'btn-elsa'):
            self.assertContains(response, element)
        # Wider than the sign-in screens, six fields rather than one.
        self.assertContains(response, 'auth-shell-wide')

    def test_fields_carry_labels_and_autocomplete_tokens(self):
        body = self.client.get(reverse('friends:register')).content.decode()

        for token in ('given-name', 'family-name', 'username', 'email', 'new-password'):
            self.assertIn('autocomplete="%s"' % token, body)
        for label in ('First name', 'Last name', 'Email address', 'Username', 'Agency'):
            self.assertIn('>%s</label>' % label, body)

    def test_a_valid_submission_still_registers(self):
        response = self._submit()

        self.assertRedirects(response, reverse('friends:otp_verify'))
        self.assertTrue(User.objects.filter(username='newbie').exists())
        self.assertFalse(User.objects.get(username='newbie').is_active)
        self.assertEqual(len(mail.outbox), 1)


class RegistrationHardeningTests(CaptchaPassing, TestCase):
    """
    Account creation, probed for the things that actually go wrong.  Every case
    here failed at least once against the original view.
    """

    def setUp(self):
        super(RegistrationHardeningTests, self).setUp()
        import tempfile
        self.archive = tempfile.mkdtemp()

    def _submit(self, **overrides):
        from django.test import override_settings
        data = {'first_name': 'Rupak', 'last_name': 'Dey', 'username': 'newbie',
                'email': 'newbie@nmsu.edu', 'password': 'correct-horse-battery',
                'agency': 'nasa:pds', 'g-recaptcha-response': 'PASSED'}
        data.update(overrides)
        with override_settings(ARCHIVE_DIR=self.archive):
            return self.client.post(reverse('friends:register'), data)

    # ---------------------------------------------------------------- passwords

    def test_password_validators_are_actually_enforced(self):
        # settings.AUTH_PASSWORD_VALIDATORS was configured but never called,
        # so a one-character password was accepted.
        for weak in ('a', '12345678', 'password'):
            response = self._submit(password=weak)
            self.assertEqual(response.status_code, 200, 'accepted password %r' % weak)
            self.assertFalse(User.objects.filter(username='newbie').exists())

    def test_a_password_too_like_the_username_is_refused(self):
        response = self._submit(username='rupakdey1', password='rupakdey1')
        self.assertContains(response, 'too similar')

    def test_a_strong_password_is_accepted(self):
        self.assertEqual(self._submit().status_code, 302)

    # ---------------------------------------------------------------- usernames

    def test_dot_usernames_cannot_escape_the_archive(self):
        # ".." is a legal Django username and a directory that resolves to the
        # parent of ARCHIVE_DIR.
        import os
        for bad in ('..', '.', '.hidden'):
            response = self._submit(username=bad)
            self.assertEqual(response.status_code, 200, 'accepted username %r' % bad)
            self.assertFalse(User.objects.filter(username=bad).exists())

    def test_every_accepted_username_stays_inside_the_archive(self):
        import os
        self._submit(username='newbie')
        directory = User.objects.get(username='newbie').userprofile.directory
        self.assertTrue(
            os.path.realpath(directory).startswith(os.path.realpath(self.archive)))

    def test_separators_and_overlong_names_are_refused(self):
        for bad in ('a/b', '../../etc', 'ru pak', 'u' * 200, 'nul\x00byte'):
            self.assertEqual(self._submit(username=bad).status_code, 200,
                             'accepted username %r' % bad)

    def test_usernames_collide_case_insensitively(self):
        # Otherwise Admin and admin coexist, which invites impersonation and
        # collides on a case-insensitive filesystem.
        self._submit(username='Admin')
        response = self._submit(username='admin', email='other@nmsu.edu')

        self.assertContains(response, 'already exists')
        self.assertEqual(User.objects.filter(username__iexact='admin').count(), 1)

    # ------------------------------------------------------------------- email

    def test_an_email_cannot_be_reused(self):
        # Email is the second factor and the recovery path, so two accounts on
        # one inbox is ambiguous to operate and to recover.
        self._submit()
        response = self._submit(username='other', email='NEWBIE@nmsu.edu')

        self.assertContains(response, 'already uses that email')
        self.assertEqual(User.objects.filter(email__iexact='newbie@nmsu.edu').count(), 1)

    def test_a_malformed_address_is_refused(self):
        self.assertContains(self._submit(email='not-an-email'), 'valid email')

    # ------------------------------------------------------------- other fields

    def test_required_fields_are_required(self):
        for field in ('first_name', 'last_name', 'username', 'email', 'password'):
            self.assertEqual(self._submit(**{field: ''}).status_code, 200,
                             'accepted a blank %s' % field)

    def test_agency_must_be_one_of_the_offered_choices(self):
        for bad in ('evil:corp', ''):
            self.assertEqual(self._submit(agency=bad).status_code, 200)

    # ------------------------------------------------------------- account state

    def test_a_new_account_is_inactive_and_not_signed_in(self):
        self._submit()
        user = User.objects.get(username='newbie')

        self.assertFalse(user.is_active)
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_password_is_hashed_not_stored(self):
        self._submit(password='correct-horse-battery')
        user = User.objects.get(username='newbie')

        self.assertNotEqual(user.password, 'correct-horse-battery')
        self.assertTrue(user.check_password('correct-horse-battery'))

    def test_an_unverified_account_cannot_sign_in(self):
        self._submit()
        response = self.client.post(reverse('friends:login'),
                                    {'username': 'newbie', 'password': 'correct-horse-battery'})

        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(response.status_code, 302)

    def test_the_emailed_code_activates_the_account(self):
        self._submit()
        code = User.objects.get(username='newbie').userprofile.generate_otp()
        self.client.post(reverse('friends:otp_verify'), {'otp': code})

        self.assertTrue(User.objects.get(username='newbie').is_active)
        self.assertIn('_auth_user_id', self.client.session)

    def test_a_wrong_code_leaves_the_account_inactive(self):
        self._submit()
        User.objects.get(username='newbie').userprofile.generate_otp()
        self.client.post(reverse('friends:otp_verify'), {'otp': '000000'})

        self.assertFalse(User.objects.get(username='newbie').is_active)
        self.assertNotIn('_auth_user_id', self.client.session)

    # ------------------------------------------------------------------- markup

    def test_submitted_values_are_escaped_when_echoed_back(self):
        response = self._submit(first_name='<script>alert(1)</script>', email='bad')
        body = response.content.decode()

        self.assertNotIn('<script>alert(1)</script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_registration_requires_a_csrf_token(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        response = strict.post(reverse('friends:register'),
                               {'username': 'x', 'password': 'y'})
        self.assertEqual(response.status_code, 403)


class ThrottleTests(CaptchaPassing, TestCase):
    """
    Password guessing had no ceiling at all before this: the emailed code had
    an attempt cap, but the password in front of it was unlimited.
    """

    def setUp(self):
        super(ThrottleTests, self).setUp()
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

        import tempfile
        self.archive = tempfile.mkdtemp()
        self.user = User.objects.create_user(
            username='rupak', password='correct-horse-battery', email='r@nmsu.edu')

    def _try_password(self, password):
        self.client.post(reverse('friends:login_identify'), {'username': 'rupak'})
        return self.client.post(reverse('friends:login_password'), {'password': password})

    def _signup(self, n):
        from django.test import override_settings
        data = {'first_name': 'A', 'last_name': 'B', 'username': 'user%d' % n,
                'email': 'user%d@nmsu.edu' % n, 'password': 'correct-horse-battery',
                'agency': 'nasa:pds', 'g-recaptcha-response': 'PASSED'}
        with override_settings(ARCHIVE_DIR=self.archive):
            return self.client.post(reverse('friends:register'), data)

    def test_password_guessing_is_eventually_refused(self):
        from friends.views import PASSWORD_FAILURES_PER_ACCOUNT

        for _ in range(PASSWORD_FAILURES_PER_ACCOUNT):
            self._try_password('wrong')

        self._try_password('wrong')
        self.assertContains(self.client.get(reverse('friends:login_password')),
                            'Too many sign-in attempts')

    def test_the_right_password_still_fails_once_the_cap_is_hit(self):
        # The lockout has to hold even for the correct password, or it is not
        # a lockout, it is a hint that the guess was wrong.
        from friends.views import PASSWORD_FAILURES_PER_ACCOUNT

        for _ in range(PASSWORD_FAILURES_PER_ACCOUNT):
            self._try_password('wrong')

        self._try_password('correct-horse-battery')
        self.assertNotIn('pre_otp_user_id', self.client.session)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_signing_in_normally_never_uses_up_the_allowance(self):
        from django.core.cache import cache
        from friends.views import _password_key

        self._try_password('wrong')
        self.assertEqual(cache.get(_password_key('rupak')), 1)

        self._try_password('correct-horse-battery')
        self.assertIsNone(cache.get(_password_key('rupak')))

    def test_the_counter_is_per_account(self):
        from django.core.cache import cache
        from friends.views import _password_key

        self._try_password('wrong')
        self.client.post(reverse('friends:login_identify'), {'username': 'someone-else'})
        self.client.post(reverse('friends:login_password'), {'password': 'wrong'})

        self.assertEqual(cache.get(_password_key('rupak')), 1)
        self.assertEqual(cache.get(_password_key('someone-else')), 1)

    def test_scripted_sign_ups_are_capped(self):
        from friends.views import SIGNUP_PER_HOUR

        for n in range(SIGNUP_PER_HOUR):
            self.assertEqual(self._signup(n).status_code, 302)

        response = self._signup(99)
        self.assertRedirects(response, reverse('main:index'))
        self.assertFalse(User.objects.filter(username='user99').exists())

    def test_browsing_the_sign_up_page_costs_nothing(self):
        from friends.views import SIGNUP_PER_HOUR

        for _ in range(SIGNUP_PER_HOUR * 3):
            self.assertEqual(self.client.get(reverse('friends:register')).status_code, 200)

        self.assertEqual(self._signup(1).status_code, 302)

    def test_a_forged_forwarded_header_does_not_reset_the_counter(self):
        # X-Forwarded-For is client-controlled unless a proxy is trusted, so it
        # must not be readable by default.
        from django.core.cache import cache
        from friends.views import _password_key

        self._try_password('wrong')
        self.client.post(reverse('friends:login_identify'), {'username': 'rupak'})
        self.client.post(reverse('friends:login_password'), {'password': 'wrong'},
                         HTTP_X_FORWARDED_FOR='1.2.3.4')

        self.assertEqual(cache.get(_password_key('rupak')), 2)


class SignupCaptchaTests(CaptchaPassing, TestCase):

    def test_the_form_carries_a_captcha(self):
        response = self.client.get(reverse('friends:register'))
        self.assertContains(response, 'g-recaptcha')

    def test_a_failed_captcha_blocks_the_account(self):
        from unittest import mock
        from django_recaptcha.client import RecaptchaResponse
        import tempfile
        from django.test import override_settings

        data = {'first_name': 'A', 'last_name': 'B', 'username': 'bot',
                'email': 'bot@nmsu.edu', 'password': 'correct-horse-battery',
                'agency': 'nasa:pds', 'g-recaptcha-response': 'FAILED'}

        with mock.patch('django_recaptcha.fields.client.submit',
                        return_value=RecaptchaResponse(is_valid=False)):
            with override_settings(ARCHIVE_DIR=tempfile.mkdtemp()):
                response = self.client.post(reverse('friends:register'), data)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='bot').exists())


class ExpectedOriginTests(TestCase):
    """
    The origin handed to the verifier has to match what the browser signed.
    Getting this wrong fails every ceremony and looks exactly like a forged
    credential, which is how it reached production unnoticed.
    """

    def _origin(self, secure=False, **settings_kwargs):
        from django.test import RequestFactory, override_settings
        from friends import passkeys

        request = RequestFactory(headers={'host': 'atmos.nmsu.edu'}).post('/')
        if secure:
            request.META['wsgi.url_scheme'] = 'https'

        with override_settings(ALLOWED_HOSTS=['atmos.nmsu.edu'], **settings_kwargs):
            return passkeys.expected_origin(request)

    def test_tls_terminated_upstream_still_yields_https(self):
        # Apache forwards over plain http, so request.scheme says http even
        # though the browser used https.  Pinning it is what fixes production.
        self.assertEqual(self._origin(secure=False, DEBUG=False),
                         'https://atmos.nmsu.edu')

    def test_debug_keeps_the_real_scheme_so_localhost_works(self):
        self.assertEqual(self._origin(secure=False, DEBUG=True),
                         'http://atmos.nmsu.edu')

    def test_an_explicit_setting_wins(self):
        self.assertEqual(
            self._origin(secure=False, DEBUG=False,
                         WEBAUTHN_ORIGIN='https://elsa.example.org'),
            'https://elsa.example.org')

    def test_a_genuinely_secure_request_is_unchanged(self):
        self.assertEqual(self._origin(secure=True, DEBUG=False),
                         'https://atmos.nmsu.edu')

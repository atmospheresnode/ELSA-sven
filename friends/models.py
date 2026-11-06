# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# from django.utils.encoding import python_2_unicode_compatible

from builtins import str
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import models
from django.db.models import F
from django.urls import reverse

from django.conf import settings
from django.utils import timezone
from datetime import timedelta
import os
import secrets
from django.db.models.signals import post_save
from django.dispatch import receiver


# One-time code settings for the email code flow.
OTP_LENGTH = 6
OTP_TTL = timedelta(minutes=5)          # How long an issued code stays valid.
OTP_MAX_ATTEMPTS = 5                    # Wrong guesses before the account locks.
OTP_LOCKOUT = timedelta(minutes=15)     # How long that lock lasts.
OTP_RESEND_COOLDOWN = timedelta(seconds=60)


# UserProfile is an extension of the User model.  So each User has a UserProfile.  And each UserProfile lists the associated user, their agency to be used in the urn, and their related user directory.
# @python_2_unicode_compatible
class UserProfile(models.Model):
    AGENCY_CHOICES = (
        ('nasa:pds','NASA'),
        ('esa:psa','ESA'),
        ('jaxa:darts','JAXA'),
        # We could be super cool and add more agencies.
    )
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    # 'nasa:pds', not 'NASA'. This has to be a choice value since it goes straight into the LID.
    agency = models.CharField(max_length=10, choices=AGENCY_CHOICES, default='nasa:pds')
    directory = models.CharField(max_length=1000)
    #picture = models.ImageField(upload_to='profile_images', blank=True)


    # Only the hash of the one-time code is stored, never the code itself.
    otp_hash = models.CharField(max_length=128, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_locked_until = models.DateTimeField(blank=True, null=True)

    # Set when the user confirms the emailed code. Inactive without it can resume, with it was switched off by an admin.
    email_verified_at = models.DateTimeField(blank=True, null=True)

    # Pending address change. Only the code's hash is kept.
    pending_email = models.EmailField(blank=True, null=True)
    pending_email_hash = models.CharField(max_length=128, blank=True, null=True)
    pending_email_created_at = models.DateTimeField(blank=True, null=True)
    pending_email_attempts = models.PositiveSmallIntegerField(default=0)

    # Null for accounts whose password has not changed since this was added.
    password_changed_at = models.DateTimeField(blank=True, null=True)

    # Set when the owner closes the account. See friends/closure.py.
    closed_at = models.DateTimeField(blank=True, null=True)

    # Researcher details, offered as "Add me as an author" in citations.
    orcid = models.CharField(max_length=19, blank=True, default='')
    affiliation = models.CharField(max_length=255, blank=True, default='')

    # An IANA name such as "Europe/Madrid"; blank means ELSA's own (TIME_ZONE).
    timezone = models.CharField(max_length=64, blank=True, default='')

    # Sessions get this at sign-in. "Sign out everywhere else" raises it, and the middleware ends older ones.
    session_generation = models.PositiveIntegerField(default=0)


    # Typical __str__ function returns the username.
    def __str__(self):
        return self.user.username


    # The Bundle Hub, which is what every "my profile" link in ELSA means.
    def get_absolute_url(self):
        return reverse('friends:bundle_hub')

    def generate_otp(self):
        """Issue a new one-time code. Returned once for emailing, only the hash is kept."""
        code = '{0:0{1}d}'.format(secrets.randbelow(10 ** OTP_LENGTH), OTP_LENGTH)
        self.otp_hash = make_password(code)
        self.otp_created_at = timezone.now()
        self.otp_attempts = 0
        self.save(update_fields=self.OTP_FIELDS)
        return code

    # update_fields so this doesn't overwrite the rest of the profile with a stale copy.
    OTP_FIELDS = ['otp_hash', 'otp_created_at', 'otp_attempts']

    def clear_otp(self):
        """Retires the current code so it cannot be replayed."""
        self.otp_hash = None
        self.otp_created_at = None
        self.otp_attempts = 0
        self.save(update_fields=self.OTP_FIELDS)

    def _lock_otp(self, now):
        # Clear the code too, so waiting out the lockout doesn't bring it back.
        self.otp_locked_until = now + OTP_LOCKOUT
        self.otp_hash = None
        self.otp_created_at = None
        self.otp_attempts = 0
        self.save(update_fields=self.OTP_FIELDS + ['otp_locked_until'])

    def _spend_code(self, code, hash_field, created_field, attempts_field, ttl):
        """Check a one-time code. Returns 'ok', 'invalid', 'expired' or 'exhausted'.

        Counts the attempt first, in one conditional UPDATE, so parallel guesses can't beat OTP_MAX_ATTEMPTS.
        """
        issued = getattr(self, hash_field)
        created = getattr(self, created_field)
        fields = [hash_field, created_field, attempts_field]
        cleared = {hash_field: None, created_field: None, attempts_field: 0}

        if not issued or not created:
            return 'expired'

        if created + ttl < timezone.now():
            for name, value in cleared.items():
                setattr(self, name, value)
            self.save(update_fields=fields)
            return 'expired'

        # Scoped to this code, so a guess can't count against a newer one.
        live = UserProfile.objects.filter(pk=self.pk, **{hash_field: issued})

        booked = live.filter(**{attempts_field + '__lt': OTP_MAX_ATTEMPTS}).update(
            **{attempts_field: F(attempts_field) + 1})
        self.refresh_from_db(fields=fields)
        if not booked:
            # Either retired by a concurrent request, or out of attempts.
            return 'expired' if getattr(self, hash_field) != issued else 'exhausted'

        # check_password compares in constant time.
        if check_password(str(code or ''), issued):
            # Clearing the code is the claim. Only one of two right requests gets in.
            if live.update(**cleared):
                for name, value in cleared.items():
                    setattr(self, name, value)
                return 'ok'
            return 'expired'

        if getattr(self, attempts_field) >= OTP_MAX_ATTEMPTS:
            return 'exhausted'
        return 'invalid'

    def verify_otp(self, code):
        """Check a sign-in code. Returns (ok, reason). Reason is None, 'locked', 'expired' or 'invalid'."""
        if self.otp_lock_remaining():
            return False, 'locked'

        result = self._spend_code(code, 'otp_hash', 'otp_created_at', 'otp_attempts', OTP_TTL)

        if result == 'ok':
            return True, None
        if result == 'exhausted':
            self._lock_otp(timezone.now())
            return False, 'locked'
        if result == 'expired':
            self.refresh_from_db(fields=['otp_locked_until'])
            if self.otp_lock_remaining():
                return False, 'locked'
        return False, result

    # --- Changing the address ----------------------------------------------

    PENDING_EMAIL_FIELDS = ['pending_email', 'pending_email_hash',
                            'pending_email_created_at', 'pending_email_attempts']

    def start_email_change(self, new_email):
        """Parks a new address and returns the code to mail to it."""
        code = '{0:0{1}d}'.format(secrets.randbelow(10 ** OTP_LENGTH), OTP_LENGTH)
        self.pending_email = new_email
        self.pending_email_hash = make_password(code)
        self.pending_email_created_at = timezone.now()
        self.pending_email_attempts = 0
        self.save(update_fields=self.PENDING_EMAIL_FIELDS)
        return code

    def reissue_email_code(self):
        """A fresh code for the address already pending."""
        return self.start_email_change(self.pending_email)

    def cancel_email_change(self):
        self.pending_email = None
        self.pending_email_hash = None
        self.pending_email_created_at = None
        self.pending_email_attempts = 0
        self.save(update_fields=self.PENDING_EMAIL_FIELDS)

    def verify_email_change(self, code):
        """Returns (new_email, None) if the code is right, else (None, reason).

        Running out of guesses drops the pending change instead of locking the account.
        """
        new_email = self.pending_email
        result = self._spend_code(code, 'pending_email_hash', 'pending_email_created_at',
                                  'pending_email_attempts', OTP_TTL)
        if result == 'ok':
            return new_email, None
        if result == 'exhausted':
            self.cancel_email_change()
        return None, result

    def email_change_pending(self):
        """Whether a change is waiting and its code can still be used."""
        return bool(self.pending_email and self.pending_email_hash and self.pending_email_created_at
                    and self.pending_email_created_at + OTP_TTL > timezone.now())

    def seconds_until_email_resend(self):
        if not self.pending_email_created_at:
            return 0
        remaining = (self.pending_email_created_at + OTP_RESEND_COOLDOWN - timezone.now()).total_seconds()
        return max(0, int(remaining))

    def email_attempts_remaining(self):
        return max(0, OTP_MAX_ATTEMPTS - self.pending_email_attempts)

    def otp_lock_remaining(self):
        """Seconds left on an attempt lockout, or 0 if the account is not locked."""
        if not self.otp_locked_until:
            return 0
        remaining = (self.otp_locked_until - timezone.now()).total_seconds()
        return max(0, int(remaining))

    def seconds_until_resend(self):
        """Seconds the user must wait before a fresh code can be sent."""
        if not self.otp_created_at:
            return 0
        remaining = (self.otp_created_at + OTP_RESEND_COOLDOWN - timezone.now()).total_seconds()
        return max(0, int(remaining))

    def seconds_until_expiry(self):
        """Seconds of life left in the current code, for the on-screen countdown."""
        if not self.otp_created_at or not self.otp_hash:
            return 0
        remaining = (self.otp_created_at + OTP_TTL - timezone.now()).total_seconds()
        return max(0, int(remaining))

    def attempts_remaining(self):
        """Guesses left before the account locks."""
        return max(0, OTP_MAX_ATTEMPTS - self.otp_attempts)


class Passkey(models.Model):
    """A WebAuthn credential. Only the public key is stored."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passkeys')

    # Base64url text so lookups are simple and it survives a MariaDB dump.
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.BinaryField()

    # A cloned authenticator eventually sends a counter that didn't advance. Some always send 0.
    sign_count = models.PositiveBigIntegerField(default=0)

    transports = models.CharField(max_length=255, blank=True, default='')
    name = models.CharField(max_length=100, default='Passkey')

    # Backed up means synced (e.g. iCloud Keychain), so losing a device doesn't lose the passkey.
    backed_up = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)

    class Meta(object):
        ordering = ('-last_used_at', '-created_at')

    def __str__(self):
        return '{0} ({1})'.format(self.name, self.user.username)

    def transport_list(self):
        return [t for t in self.transports.split(',') if t]

    def mark_used(self, new_sign_count):
        self.sign_count = new_sign_count
        self.last_used_at = timezone.now()
        self.save(update_fields=['sign_count', 'last_used_at'])


class UserSession(models.Model):
    """One signed-in browser, for the account page's device list."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tracked_sessions')
    session_key = models.CharField(max_length=40, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')

    class Meta(object):
        ordering = ('-last_seen_at',)

    def __str__(self):
        return '{0} on {1}'.format(self.user.username, self.user_agent[:40])


class AccountEvent(models.Model):
    """A security-relevant thing that happened to an account, shown to its owner."""
    SIGN_IN = 'sign_in'
    SIGN_OUT = 'sign_out'
    PASSWORD_CHANGED = 'password_changed'
    PASSWORD_RESET = 'password_reset'
    EMAIL_CHANGED = 'email_changed'
    USERNAME_CHANGED = 'username_changed'
    PASSKEY_ADDED = 'passkey_added'
    PASSKEY_REMOVED = 'passkey_removed'
    DEVICE_REMEMBERED = 'device_remembered'
    DEVICE_FORGOTTEN = 'device_forgotten'
    SESSION_ENDED = 'session_ended'
    OTHERS_SIGNED_OUT = 'others_signed_out'
    KIND_CHOICES = (
        (SIGN_IN, 'Signed in'),
        (SIGN_OUT, 'Signed out'),
        (PASSWORD_CHANGED, 'Password changed'),
        (PASSWORD_RESET, 'Password reset by email'),
        (EMAIL_CHANGED, 'Email address changed'),
        (USERNAME_CHANGED, 'Username changed'),
        (PASSKEY_ADDED, 'Passkey added'),
        (PASSKEY_REMOVED, 'Passkey removed'),
        (DEVICE_REMEMBERED, 'Device remembered'),
        (DEVICE_FORGOTTEN, 'Remembered device forgotten'),
        (SESSION_ENDED, 'Signed out a device'),
        (OTHERS_SIGNED_OUT, 'Signed out everywhere else'),
    )

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='account_events')
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    detail = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    ip = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')

    class Meta(object):
        ordering = ('-created_at', '-pk')

    def __str__(self):
        return '{0}: {1}'.format(self.user.username, self.get_kind_display())


class TrustedDevice(models.Model):
    """A browser that can skip the emailed code for a while. Only the token's SHA-256 is kept."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trusted_devices')
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(db_index=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    user_agent = models.CharField(max_length=300, blank=True, default='')

    class Meta(object):
        ordering = ('-created_at',)

    def __str__(self):
        return '{0} until {1:%Y-%m-%d}'.format(self.user.username, self.expires_at)


class AuthThrottle(models.Model):
    """One rate-limit counter. Every hit is a single UPDATE (see friends/throttle.py)."""
    # 191 characters keeps a unique utf8mb4 index inside MariaDB's limit.
    key = models.CharField(max_length=191, unique=True)
    count = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)

    def __str__(self):
        return '{0} = {1}'.format(self.key, self.count)


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Every user gets a profile. Only creates one, so it doesn't overwrite code fields on login."""
    if created or not UserProfile.objects.filter(user=instance).exists():
        UserProfile.objects.get_or_create(
            user=instance,
            defaults={'directory': os.path.join(settings.ARCHIVE_DIR, instance.username)})

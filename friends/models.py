# -*- coding: utf-8 -*-
from __future__ import unicode_literals
# from django.utils.encoding import python_2_unicode_compatible

from builtins import str
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse

from django.utils import timezone
from datetime import timedelta
import secrets
from django.db.models.signals import post_save
from django.dispatch import receiver


# One-time code policy.  These are the only knobs for the email code flow, so
# keep them here rather than scattering literals through the views.
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
    agency = models.CharField(max_length=10, choices=AGENCY_CHOICES, default='NASA')
    directory = models.CharField(max_length=1000)
    #picture = models.ImageField(upload_to='profile_images', blank=True)


    # Only the hash of the one-time code is stored, never the code itself.
    otp_hash = models.CharField(max_length=128, blank=True, null=True)
    otp_created_at = models.DateTimeField(blank=True, null=True)
    otp_attempts = models.PositiveSmallIntegerField(default=0)
    otp_locked_until = models.DateTimeField(blank=True, null=True)


    # Typical __str__ function returns the username.
    def __str__(self):
        return self.user.username


    # Returns a user's profile where the user is looked up by id.
    def get_absolute_url(self):
        return reverse('friends:profile', args=[str(self.id)])

    # Returns a user's archive on ELSA.
    def get_directory_url(self):
        return reverse('https://atmos.nmsu.edu/elsa/{0}'.format(str(self.id)))
    
    def generate_otp(self):
        """
        Issues a new one-time code.  Only the hash is kept, so this is the one
        and only chance to read the plain code -- it is returned for emailing
        and is not recoverable afterwards.
        """
        code = '{0:0{1}d}'.format(secrets.randbelow(10 ** OTP_LENGTH), OTP_LENGTH)
        self.otp_hash = make_password(code)
        self.otp_created_at = timezone.now()
        self.otp_attempts = 0
        self.save()
        return code

    def clear_otp(self):
        """Retires the current code so it cannot be replayed."""
        self.otp_hash = None
        self.otp_created_at = None
        self.otp_attempts = 0
        self.save()

    def verify_otp(self, code):
        """
        Checks a submitted code and books the attempt against the account.
        Returns (ok, reason), where reason is None on success or one of
        'locked', 'expired', 'invalid'.
        """
        now = timezone.now()

        if self.otp_lock_remaining():
            return False, 'locked'

        if not self.otp_hash or not self.otp_created_at:
            return False, 'expired'

        if self.otp_created_at + OTP_TTL < now:
            self.clear_otp()
            return False, 'expired'

        # check_password compares in constant time.
        if not check_password(str(code or ''), self.otp_hash):
            self.otp_attempts += 1
            if self.otp_attempts >= OTP_MAX_ATTEMPTS:
                # Burn the code as well as locking, so waiting out the lockout
                # does not hand back a still-live code.
                self.otp_locked_until = now + OTP_LOCKOUT
                self.clear_otp()
                return False, 'locked'
            self.save()
            return False, 'invalid'

        self.clear_otp()
        return True, None

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
    """
    A WebAuthn credential registered by a user.

    Only the public key is stored.  The private key never leaves the
    authenticator, which is what makes a passkey phishing resistant: the
    credential is bound to ELSA's origin, so a lookalike site cannot get the
    authenticator to sign for it.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='passkeys')

    # Base64url text rather than raw bytes, so lookups stay simple and the
    # value survives a MariaDB dump unchanged.
    credential_id = models.CharField(max_length=512, unique=True)
    public_key = models.BinaryField()

    # Replay defence: an authenticator that has been cloned will eventually
    # present a counter that failed to advance.  Some authenticators never
    # implement it and always report 0, which is legitimate.
    sign_count = models.PositiveBigIntegerField(default=0)

    transports = models.CharField(max_length=255, blank=True, default='')
    name = models.CharField(max_length=100, default='Passkey')

    # A backed-up credential is synced through something like iCloud Keychain,
    # so losing one device does not lose the passkey.
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


class UpdateNameFirst(models.Model):
    first_name = models.CharField(max_length=256)
class UpdateNameLast(models.Model):
    last_name = models.CharField(max_length=256)
class UpdateAgency(models.Model):
    AGENCY_CHOICES = (
        ('nasa:pds','NASA'),
        ('esa:psa','ESA'),
        ('jaxa:darts','JAXA'),
    )
    agency = models.CharField(max_length=10, choices=AGENCY_CHOICES, default='NASA')
class UpdateEmail(models.Model):
    email = models.CharField(max_length=256)
class UpdatePassword(models.Model):
    current_password = models.CharField(max_length=256)
    new_password = models.CharField(max_length=256)
    confirm_password = models.CharField(max_length=256)


@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        # Create default directory path if needed, or leave empty
        UserProfile.objects.create(user=instance, directory=f"/archive/{instance.username}/")
    else:
        # If user exists but has no profile, create one (safeguard)
        if hasattr(instance, 'userprofile'):
            instance.userprofile.save()
        else:
             UserProfile.objects.create(user=instance, directory=f"/archive/{instance.username}/")












# Login Stuff --- Don't touch until Login Stuff needs to be changed.
# For more info on where all of the Login Stuff, please see LoginStuff.txt in LearnElsa directory.

#def get_upload_path(instance):
#    return 'bundles/user_{}/'.format(instance.user.id)


#    def get_absolute_url(self):
#        """
#        Returns the url to access a particular book instance.
#        """
#        return reverse('profile', args=[smart_str(self.id)], current_app=friends)

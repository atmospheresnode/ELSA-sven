from builtins import object
import re

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox
from .models import *


# A username becomes a directory name under ARCHIVE_DIR, so it has to be safe
# as a single path segment.  Django's own validator allows "..", which is a
# perfectly legal username and a directory that escapes the archive root.
SAFE_USERNAME = re.compile(r'^[A-Za-z0-9._@+-]+$')










# Create Forms Here.

def flag_invalid_fields(*forms):
    """
    Puts is-invalid on the widgets Django rejected, so the field styling matches
    the error text beside it.  Django does not do this on its own, and there is
    no widget-tweaks dependency in this project.
    """
    for form in forms:
        for name in form.errors:
            field = form.fields.get(name)
            if field is None:
                continue
            classes = field.widget.attrs.get('class', '')
            if 'is-invalid' not in classes:
                field.widget.attrs['class'] = (classes + ' is-invalid').strip()


# Standard UserForm allows a user to be created given first name, last name, username, email, and password.  The PasswordInput widget is used to hide the typed in characters.
class UserForm(forms.ModelForm):
    # autocomplete tokens let password managers fill and, more importantly,
    # offer to save the new credential.  col-md-* classes were on the inputs
    # themselves, which is a grid class doing nothing useful on a form control.
    first_name = forms.CharField(
        max_length = 255,
        widget = forms.TextInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'First name',
            'autocomplete': 'given-name',
        })
    )
    last_name = forms.CharField(
        max_length = 255,
        widget = forms.TextInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'Last name',
            'autocomplete': 'family-name',
        })
    )
    username = forms.CharField(
        max_length = 255,
        widget = forms.TextInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'Pick a username',
            'autocomplete': 'username',
            'autocapitalize': 'none',
            'spellcheck': 'false',
        })
    )
    email = forms.EmailField(
        widget = forms.EmailInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'you@institution.edu',
            'autocomplete': 'email',
            'autocapitalize': 'none',
            'spellcheck': 'false',
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'Create a password',
            'autocomplete': 'new-password',
        })
    )
    # Same field the contact form uses.  The per-address throttle in the view
    # limits how fast one client can try; this is what stops it being scripted
    # from many addresses at once.
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')
    def clean_username(self):
        username = (self.cleaned_data.get('username') or '').strip()

        if not SAFE_USERNAME.match(username):
            raise ValidationError('Use letters, digits and . @ + - _ only.')

        # "." and ".." are valid Django usernames and disastrous directory
        # names; a leading dot would also make the archive folder hidden.
        if username.startswith('.'):
            raise ValidationError('Usernames cannot start with a dot.')

        if len(username) > 150:
            raise ValidationError('Usernames must be 150 characters or fewer.')

        # Case-insensitive, so "Admin" cannot be registered alongside "admin"
        # and so the two do not collide on a case-insensitive filesystem.
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError('A user with that username already exists.')

        return username

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()

        # Email is the second factor and the recovery path, so two accounts
        # sharing one inbox makes both ambiguous to operate and to recover.
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError('An account already uses that email address.')

        return email

    def clean_password(self):
        password = self.cleaned_data.get('password') or ''

        # settings.AUTH_PASSWORD_VALIDATORS was configured but nothing ever
        # ran it, so "a" was an acceptable password.  The half-built user lets
        # the similarity validator compare against the name and address.
        probe = User(
            username=self.cleaned_data.get('username') or '',
            email=self.cleaned_data.get('email') or '',
            first_name=self.cleaned_data.get('first_name') or '',
            last_name=self.cleaned_data.get('last_name') or '',
        )
        validate_password(password, probe)

        return password

    class Meta(object):
        model = User
        fields = ('first_name','last_name','username', 'email', 'password')


# UserProfileForm allows us to add additional information to the User model by assigning an associated UserProfile model.  The additional information we would like to store about the User is the directory.
AGENCY_CHOICES = (
    ('nasa:pds','NASA'),
    ('esa:psa','ESA'),
    ('jaxa:darts','JAXA'),
    # We could be super cool and add more agencies.
)
class UserProfileForm(forms.ModelForm):
    agency = forms.ChoiceField(required=True, choices=AGENCY_CHOICES, label='',
        widget=forms.Select(attrs={
            'class': 'form-control-elsa'
        })
    )
    class Meta(object):
        model = UserProfile
        exclude = ('user', 'directory', 'otp_hash', 'otp_created_at',
                   'otp_attempts', 'otp_locked_until')

#The following classes update various and sundry in the profile settings page. As things are added to the UserForm new classes will need to be added here and in friends/models.
class UpdateNameFirstForm(forms.ModelForm):
    class Meta(object):
        model = UpdateNameFirst
        fields = ('first_name',)

class UpdateNameLastForm(forms.ModelForm):
    class Meta(object):
        model = UpdateNameLast
        fields = ('last_name',)

class UpdateAgencyForm(forms.ModelForm):
    class Meta(object):
        model = UpdateAgency
        fields = ('agency',)

class UpdateEmailForm(forms.ModelForm):
    class Meta(object):
        model = UpdateEmail
        fields = ('email',)

class UpdatePasswordForm(forms.ModelForm):
    class Meta(object):
        model = UpdatePassword
        fields = ('current_password', 'new_password', 'confirm_password',)





from builtins import object
import re

from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox
from .models import UserProfile


# A username becomes a directory name under ARCHIVE_DIR, so it has to be safe
# as a single path segment.  Django's own validator allows "..", which is a
# perfectly legal username and a directory that escapes the archive root.
SAFE_USERNAME = re.compile(r'^[A-Za-z0-9._@+-]+$')

# Folder names ELSA itself keeps inside ARCHIVE_DIR, so no account may own one.
RESERVED_USERNAMES = {'_closed_accounts'}

# auth_user's name columns are 150 wide.  The form allowed 255, which MariaDB
# either truncates or refuses with a 500, depending on its SQL mode.
NAME_MAX = 150


def validate_new_username(username, exclude=None):
    """
    The rules for a username, shared by sign-up and renaming.  A username
    becomes ARCHIVE_DIR/<username>, so it must be one safe path segment.
    """
    username = (username or '').strip()

    if not SAFE_USERNAME.match(username):
        raise ValidationError('Use letters, digits and . @ + - _ only.')

    # "." and ".." are valid Django usernames and disastrous directory
    # names; a leading dot would also make the archive folder hidden.
    if username.startswith('.'):
        raise ValidationError('Usernames cannot start with a dot.')

    if username.lower() in RESERVED_USERNAMES:
        raise ValidationError('That username is not available.')

    # Case-insensitive, so "Admin" cannot be registered alongside "admin"
    # and so the two do not collide on a case-insensitive filesystem.
    taken = User.objects.filter(username__iexact=username)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    if taken.exists():
        raise ValidationError('A user with that username already exists.')

    return username


def email_in_use(email, exclude=None):
    """
    Whether another account already uses this address, ignoring case.  Email
    is the second factor and the recovery path, so two accounts sharing one
    inbox makes both ambiguous to operate and to recover.
    """
    taken = User.objects.filter(email__iexact=email)
    if exclude is not None:
        taken = taken.exclude(pk=exclude.pk)
    return taken.exists()










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
        max_length = NAME_MAX,
        widget = forms.TextInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'First name',
            'autocomplete': 'given-name',
        })
    )
    last_name = forms.CharField(
        max_length = NAME_MAX,
        widget = forms.TextInput(attrs={
            'class': 'form-control-elsa',
            'placeholder': 'Last name',
            'autocomplete': 'family-name',
        })
    )
    username = forms.CharField(
        max_length = NAME_MAX,
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
        return validate_new_username(self.cleaned_data.get('username'))

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip()

        if email_in_use(email):
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
class UserProfileForm(forms.ModelForm):
    agency = forms.ChoiceField(required=True, choices=UserProfile.AGENCY_CHOICES, label='',
        widget=forms.Select(attrs={
            'class': 'form-control-elsa'
        })
    )
    class Meta(object):
        model = UserProfile
        fields = ('agency',)


#----------------------------------------------------------------------------------------
# Profile Settings.  One form per card, each posted on its own.
#----------------------------------------------------------------------------------------

class NameForm(forms.ModelForm):
    first_name = forms.CharField(max_length=NAME_MAX, widget=forms.TextInput(attrs={
        'class': 'form-control', 'autocomplete': 'given-name'}))
    last_name = forms.CharField(max_length=NAME_MAX, widget=forms.TextInput(attrs={
        'class': 'form-control', 'autocomplete': 'family-name'}))

    def clean_first_name(self):
        return self.cleaned_data['first_name'].strip()

    def clean_last_name(self):
        return self.cleaned_data['last_name'].strip()

    class Meta(object):
        model = User
        fields = ('first_name', 'last_name')


def _password_widget(autocomplete):
    return forms.PasswordInput(attrs={'class': 'form-control', 'autocomplete': autocomplete})


class EmailChangeForm(forms.Form):
    """
    Asks for the new address and the current password.  The address is not
    changed here: the view mails a code to it, and only that code switches the
    account over.
    """
    new_email = forms.EmailField(label='New email address', widget=forms.EmailInput(attrs={
        'class': 'form-control', 'autocomplete': 'email',
        'autocapitalize': 'none', 'spellcheck': 'false'}))
    current_password = forms.CharField(label='Current password', strip=False,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control', 'autocomplete': 'current-password'}))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(EmailChangeForm, self).__init__(*args, **kwargs)

    def clean_new_email(self):
        email = self.cleaned_data['new_email'].strip()
        if email.lower() == (self.user.email or '').lower():
            raise ValidationError('That is already your email address.')
        if email_in_use(email, exclude=self.user):
            raise ValidationError('An account already uses that email address.')
        return email

    def clean_current_password(self):
        password = self.cleaned_data['current_password']
        if not self.user.check_password(password):
            raise ValidationError('Your current password was entered incorrectly.')
        return password


class ElsaPasswordChangeForm(PasswordChangeForm):
    """
    Django's own form: checks the old password, runs AUTH_PASSWORD_VALIDATORS
    and compares the two new entries.  Only the widgets are restyled.
    """
    def __init__(self, *args, **kwargs):
        super(ElsaPasswordChangeForm, self).__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'


class EmailCodeForm(forms.Form):
    code = forms.RegexField(regex=r'^\s*\d{6}\s*$', label='Code',
                            error_messages={'invalid': 'Enter the 6-digit code from the email.'})

    def clean_code(self):
        return self.cleaned_data['code'].strip()


class PasskeyNameForm(forms.Form):
    name = forms.CharField(max_length=100, label='Passkey name')

    def clean_name(self):
        name = ' '.join(self.cleaned_data['name'].split())
        if not name:
            raise ValidationError('Give the passkey a name.')
        return name


class CloseAccountForm(forms.Form):
    """Confirmed with the account's own username, as GitHub and Heroku do."""
    current_password = forms.CharField(label='Current password', strip=False,
                                       widget=_password_widget('current-password'))
    confirm = forms.CharField(label='Your username')

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(CloseAccountForm, self).__init__(*args, **kwargs)

    def clean_current_password(self):
        password = self.cleaned_data['current_password']
        if not self.user.check_password(password):
            raise ValidationError('Your current password was entered incorrectly.')
        return password

    def clean_confirm(self):
        if self.cleaned_data['confirm'].strip() != self.user.username:
            raise ValidationError('Type your username exactly as shown to confirm.')
        return self.cleaned_data['confirm']


class UsernameForm(forms.Form):
    new_username = forms.CharField(label='New username', max_length=NAME_MAX)
    current_password = forms.CharField(label='Current password', strip=False,
                                       widget=_password_widget('current-password'))

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(UsernameForm, self).__init__(*args, **kwargs)

    def clean_new_username(self):
        username = (self.cleaned_data['new_username'] or '').strip()
        if username == self.user.username:
            raise ValidationError('That is already your username.')
        return validate_new_username(username, exclude=self.user)

    def clean_current_password(self):
        password = self.cleaned_data['current_password']
        if not self.user.check_password(password):
            raise ValidationError('Your current password was entered incorrectly.')
        return password


class AgencyRequestForm(forms.Form):
    """Step one of asking the team for a different agency."""
    agency = forms.ChoiceField(label='Agency you need', choices=UserProfile.AGENCY_CHOICES)
    note = forms.CharField(label='Anything the team should know?', required=False, max_length=500)

    def __init__(self, profile, *args, **kwargs):
        self.profile = profile
        super(AgencyRequestForm, self).__init__(*args, **kwargs)

    def clean_agency(self):
        agency = self.cleaned_data['agency']
        if agency == self.profile.agency:
            raise ValidationError('Your account already uses that agency.')
        return agency


class AgencySendForm(AgencyRequestForm):
    """Step two: the reviewed email itself."""
    subject = forms.CharField(max_length=150)
    body = forms.CharField(max_length=4000)

    def clean_subject(self):
        # One line: a newline in a subject is a header injection.
        return ' '.join(self.cleaned_data['subject'].split())


ORCID_PATTERN = re.compile(r'^(?:https?://(?:www\.)?orcid\.org/)?(\d{4})-?(\d{4})-?(\d{4})-?(\d{3}[\dX])$', re.I)


def orcid_checksum_ok(digits):
    """ISO 7064 MOD 11-2, the check character every ORCID iD ends with."""
    total = 0
    for char in digits[:15]:
        total = (total + int(char)) * 2
    result = (12 - total % 11) % 11
    return digits[15].upper() == ('X' if result == 10 else str(result))


def normalize_orcid(value):
    """'0000-0002-1825-0097' from any common way of writing it, or ValidationError."""
    value = (value or '').strip()
    if not value:
        return ''
    match = ORCID_PATTERN.match(value)
    if not match:
        raise ValidationError('Enter an ORCID iD like 0000-0002-1825-0097.')
    digits = ''.join(match.groups()).upper()
    if not orcid_checksum_ok(digits):
        raise ValidationError('That ORCID iD has a typo: its last character does not match.')
    return '-'.join(digits[i:i + 4] for i in range(0, 16, 4))


class ResearchProfileForm(forms.Form):
    orcid = forms.CharField(label='ORCID iD', required=False, max_length=60)
    affiliation = forms.CharField(label='Affiliation', required=False, max_length=255)

    def clean_orcid(self):
        return normalize_orcid(self.cleaned_data['orcid'])

    def clean_affiliation(self):
        return ' '.join(self.cleaned_data['affiliation'].split())

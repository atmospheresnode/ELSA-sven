from builtins import object
from django import forms
from django.contrib.auth.models import User
from .models import *










# Create Forms Here.

# Standard UserForm allows a user to be created given first name, last name, username, email, and password.  The PasswordInput widget is used to hide the typed in characters.
class UserForm(forms.ModelForm):
    first_name = forms.CharField(
        max_length = 255,
        widget = forms.TextInput(attrs={
            'class': 'form-control form-outline',
            'placeholder': 'First Name'
        })
    )
    last_name = forms.CharField(
        max_length = 255,
        widget = forms.TextInput(attrs={
            'class': 'col-md-6 mb-4 form-control form-outline',
            'placeholder': 'Last Name'
        })
    )
    username = forms.CharField(
        max_length = 255,
        widget = forms.TextInput(attrs={
            'class': 'col-md-6 mb-4 form-control form-outline',
            'placeholder': 'Username'
        })
    )
    email = forms.EmailField(
        widget = forms.TextInput(attrs={
            'class': 'col-md-6 mb-4 form-control form-outline',
            'placeholder': 'Email'
        })
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={
            'class': 'col-md-6 mb-4 form-control form-outline',
            'placeholder': 'Password'
        })
    )
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
            'class': 'form-control form-outline'
        })
    )
    class Meta(object):
        model = UserProfile
        exclude = ('user', 'directory',)

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





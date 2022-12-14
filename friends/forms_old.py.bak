from django import forms
from django.contrib.auth.models import User
from .models import UserProfile










# Create Forms Here.

# Standard UserForm allows a user to be created given first name, last name, username, email, and password.  The PasswordInput widget is used to hide the typed in characters.
class UserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput())

    class Meta:
        model = User
        fields = ('first_name','last_name','username', 'email', 'password')


# UserProfileForm allows us to add additional information to the User model by assigning an associated UserProfile model.  The additional information we would like to store about the User is the directory.
class UserProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        exclude = ('user', 'directory',)

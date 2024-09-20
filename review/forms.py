from django import forms
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox


# A normal form with questions and answers for the user.  These questions and answers were pulled from Danae's version of ELSA.

class ReviewForm(forms.Form):
    user_name = forms.CharField(
        label='Full Name',
        label_suffix = '',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter your full name'})
    )
    
    user_email = forms.EmailField(
        label='Email Address',
        label_suffix = '',
        widget=forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Enter your email address'})
    )

    derived_data = forms.CharField(
        required=True,
        label='Reviewed PDS Data Set',
        label_suffix = '',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter the reviewed data set name'})
    )
    
    CHOICES = (
        ('PDS3', 'PDS3'),
        ('PDS4', 'PDS4'),
    )
    
    archive_standard = forms.ChoiceField(
        choices=CHOICES,
        label_suffix = '',
        widget=forms.RadioSelect(attrs={'class': 'form-check-input'})
    )
    
    question1 = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Provide your comments...'}),
        label='Does the data provide clear and concise documentation adequate for its usage?'
    )
    
    question2 = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Provide your comments...'}),
        label='Are you able to manipulate and plot the data, interpret columns into tables, and understand the context and relationships of the data products?'
    )
    
    question3 = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Provide your comments...'}),
        label='Are there any concerns about the creation/generation, calibration, or general usability of the data?'
    )
    
    question4 = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 4, 'placeholder': 'Provide your comments...'}),
        label='Any further comments to PDS Atmospheres Node about the data?'
    )

    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')



class UserInfoForm(forms.Form):
    first_name = forms.CharField(required=True)
    last_name = forms.CharField(required=True)
    contact_email = forms.CharField(required=True)
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')

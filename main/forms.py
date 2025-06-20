from builtins import object
from django import forms
from .models import UploadedDocument
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox

class ContactForm(forms.Form):
    name = forms.CharField(
        label='Name',
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your full name',
            'aria-label': 'Name',
        })
    )
    email = forms.EmailField(
        label='Email',
        label_suffix = '',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address',
            'aria-label': 'Email',
        })
    )
    # agency = forms.CharField(
    #     label='Agency/Institution',
    #     label_suffix = '',
    #     widget=forms.TextInput(attrs={
    #         'class': 'form-control',
    #         'placeholder': 'Enter your agency or institution',
    #         'aria-label': 'Agency/Institution',
    #     })
    # )
    message = forms.CharField(
        label='Describe your issue',
        label_suffix = '',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'placeholder': 'Type your message here...',
            'rows': 4,
            'aria-label': 'Message',
        })
    )

    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')

class ContextProductsContactForm(forms.Form):
    name = forms.CharField(
        label='Name',
        label_suffix = '',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your full name',
            'aria-label': 'Name',
        })
    )
    email = forms.EmailField(
        label='Email',
        label_suffix = '',
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email address',
            'aria-label': 'Email',
        })
    )
    message = forms.CharField(
        label='Describe the Context Product you need (e.g., investigation, instrument, target, etc.)',
        label_suffix = '',
        widget=forms.Textarea(attrs={
            'class': 'form-control',
            'placeholder': 'Type your message here...',
            'rows': 4,
            'aria-label': 'Message',
        })
    )

    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')


class UserContactForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea)
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')



class UploadedDocumentForm(forms.ModelForm):
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox(), label='')
    class Meta(object):
        model = UploadedDocument
        fields = ('description', 'document',)


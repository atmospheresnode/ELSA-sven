from builtins import object
from django import forms
from .models import UploadedDocument
from captcha.fields import CaptchaField

class ContactForm(forms.Form):

    name = forms.CharField()
    email = forms.CharField()
    agency = forms.CharField()
    message = forms.CharField(widget=forms.Textarea)
    captcha = CaptchaField()


class UserContactForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea)
    captcha = CaptchaField()


class UploadedDocumentForm(forms.ModelForm):
    class Meta(object):
        model = UploadedDocument
        fields = ('description', 'document',)


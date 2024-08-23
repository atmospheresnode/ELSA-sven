from builtins import object
from django import forms
from .models import UploadedDocument
from django_recaptcha.fields import ReCaptchaField
from django_recaptcha.widgets import ReCaptchaV2Checkbox

class ContactForm(forms.Form):

    name = forms.CharField()
    email = forms.CharField()
    agency = forms.CharField(label='Agency/Institution')
    message = forms.CharField(widget=forms.Textarea)
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox())



class UserContactForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea)
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox())



class UploadedDocumentForm(forms.ModelForm):
    captcha = ReCaptchaField(widget=ReCaptchaV2Checkbox())
    class Meta(object):
        model = UploadedDocument
        fields = ('description', 'document',)


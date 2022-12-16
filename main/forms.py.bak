from django import forms
from .models import UploadedDocument

class ContactForm(forms.Form):

    name = forms.CharField()
    email = forms.CharField()
    agency = forms.CharField()
    message = forms.CharField(widget=forms.Textarea)


class UserContactForm(forms.Form):
    message = forms.CharField(widget=forms.Textarea)


class UploadedDocumentForm(forms.ModelForm):
    class Meta:
        model = UploadedDocument
        fields = ('description', 'document',)


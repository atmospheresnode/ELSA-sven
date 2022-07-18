# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models
from django.utils.encoding import python_2_unicode_compatible
from build.models import Bundle

def get_user_document_directory(instance, filename):
    
    document_collection_directory = 'archive/{0}/{1}/documents/'.format(instance.bundle.user.username, instance.bundle.name)
    return document_collection_directory

# Create your models here.
@python_2_unicode_compatible
class Joke(models.Model):
    question = models.CharField(max_length=500, unique=True, default="")
    answer = models.CharField(max_length=500, default="")    
    likes = models.IntegerField(default=0)

    class Meta:
        verbose_name = 'Joke'
        verbose_name_plural = 'Jokes'

    def __str__(self):
        return self.question

@python_2_unicode_compatible
class UploadedDocument(models.Model):
    description = models.CharField(max_length=255, blank=True)
    document = models.FileField(upload_to=get_user_document_directory)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.description

    



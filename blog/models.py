# Stdlib imports
# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from builtins import str
from builtins import object
import datetime

# Core Django imports
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
from django.utils import timezone
# from django.utils.encoding import python_2_unicode_compatible

# Third-party app imports

# Imports from apps

# Create your models here.



# @python_2_unicode_compatible
class Category(models.Model):
    name = models.CharField(max_length=128, unique=True)
    overview = models.CharField(max_length=40)
    views = models.IntegerField(default=0)
    #likes = models.IntegerField(default=0)
#    slug = models.SlugField(unique=True)

#    def save(self, *args, **kwargs):
#        self.slug = slugify(self.name)
#        super(Category, self).save(*args, **kwargs)

    class Meta(object):
        verbose_name_plural = 'Categories'

    def __str__(self):
        return self.name


# @python_2_unicode_compatible
class Post(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=22)
    overview = models.CharField(max_length=40)
    content = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    date = models.DateTimeField(default=timezone.now)


    def __str__(self):
        return self.title

    def was_published_recently(self):
        return self.date >= timezone.now() - datetime.timedelta(days=1)

    def get_absolute_url(self):
        return reverse('blog:detail', args=[smart_str(self.id)])

# @python_2_unicode_compatible
class Comment(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField(max_length=5000)
    date = models.DateTimeField(default=timezone.now)
    post = models.ForeignKey(Post, on_delete=models.CASCADE)

    def __str__(self):
        shortened_content = self.content[:10]
        return '{0}: {1}'.format(self.author, shortened_content)

    def was_published_recently(self):
        return self.date >= timezone.now() - datetime.timedelta(days=1)


# To begin making a model, we need to dissect the structure of a Django Model Object.  Below is the skeleton for the Task model object.
#
#@python_2_unicode_compatible
#class Task(models.Model):	
#
#    # Attributes of your model are listed here.  The attributes do as normal attributes do and describe the object at hand.
#    # Each attribute is related to a model field reference.  The model field references are used in a number of places (forms, database, ..).
#
#
#

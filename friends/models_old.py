# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from builtins import str
from django.contrib.auth.models import User
from django.db import models
from django.urls import reverse
# from django.utils.encoding import python_2_unicode_compatible










# Create your models here.

# UserProfile is an extension of the User model.  So each User has a UserProfile.  And each UserProfile lists the associated user, their agency to be used in the urn, and their related user directory.
# @python_2_unicode_compatible
class UserProfile(models.Model):
    AGENCY_CHOICES = (
        ('nasa:pds','NASA'),
        ('esa:psa','ESA'),
        ('jaxa:darts','JAXA'),
        # We could be super cool and add more agencies.
    )
    user = models.OneToOneField(User)
    agency = models.CharField(max_length=10, choices=AGENCY_CHOICES, default='NASA')
    directory = models.CharField(max_length=1000)
    #picture = models.ImageField(upload_to='profile_images', blank=True)


    # Typical __str__ function returns the username.
    def __str__(self):
        return self.user.username


    # Returns a user's profile where the user is looked up by id.
    def get_absolute_url(self):
        return reverse('friends:profile', args=[smart_str(self.id)])

    # Returns a user's archive on ELSA.
    def get_directory_url(self):
        return reverse('https://atmos.nmsu.edu/elsa/{0}'.format(smart_str(self.id)))



















# Login Stuff --- Don't touch until Login Stuff needs to be changed.
# For more info on where all of the Login Stuff, please see LoginStuff.txt in LearnElsa directory.

#def get_upload_path(instance):
#    return 'bundles/user_{}/'.format(instance.user.id)


#    def get_absolute_url(self):
#        """
#        Returns the url to access a particular book instance.
#        """
#        return reverse('profile', args=[smart_str(self.id)], current_app=friends)

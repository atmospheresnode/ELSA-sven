"""
K. Sweebe

elsa.friends.admin is used to display elsa's backend on a GUI to see and manipulate data (given the proper permissions).

"""




# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib import admin
from .models import UserProfile







# Register your models here.
admin.site.register(UserProfile)

# Stdlib imports
# -*- coding: utf-8 -*-
from __future__ import unicode_literals

# Core Django imports
from django.contrib import admin

# Third-party app imports

# Imports from apps
from .models import Category, Comment, Post

# Register your models here.
admin.site.register(Category)
admin.site.register(Comment)
admin.site.register(Post)

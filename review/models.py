# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models
from django.contrib.auth.models import User
import uuid


class ReviewDraft(models.Model):
    draft_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE)
    content = models.TextField()  # JSON serialized form data
    updated_at = models.DateTimeField(auto_now=True)
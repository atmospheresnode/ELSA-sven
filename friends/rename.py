# -*- coding: utf-8 -*-
"""Renaming an account.

The username is also the archive folder, and bundle paths come from it. A rename moves the
folder and changes the two columns. The os.rename is inside the transaction and gets undone
if the transaction fails.
"""
from __future__ import unicode_literals

import logging
import os

from django.contrib.auth.models import User
from django.db import transaction

from build.models import ValidationRun
from .models import UserProfile


logger = logging.getLogger(__name__)


class RenameRefused(Exception):
    """A reason the rename cannot happen now, worded for the user."""


def rename_user(user, new_username):
    from .views import archive_dir_for

    old_username = user.username
    old_dir = archive_dir_for(old_username)
    new_dir = archive_dir_for(new_username)
    case_only = old_username.lower() == new_username.lower()

    # validate reads the bundle folder from a worker for minutes at a time.
    if ValidationRun.objects.filter(
            bundle__user=user,
            status__in=(ValidationRun.STATUS_QUEUED, ValidationRun.STATUS_RUNNING)).exists():
        raise RenameRefused('A validation run is in progress on one of your bundles. '
                            'Try again when it has finished.')

    # Never adopt a deleted account's folder. A case-only rename can see its own folder here.
    if os.path.lexists(new_dir) and not case_only:
        logger.warning('rename %s -> %s refused: %s already exists', old_username, new_username, new_dir)
        raise RenameRefused('That username is not available.')

    moved = False
    try:
        with transaction.atomic():
            locked = User.objects.select_for_update().get(pk=user.pk)
            if User.objects.filter(username__iexact=new_username).exclude(pk=user.pk).exists():
                raise RenameRefused('A user with that username already exists.')

            if os.path.isdir(old_dir):
                os.rename(old_dir, new_dir)
                moved = True

            locked.username = new_username
            locked.save(update_fields=['username'])
            UserProfile.objects.filter(user=user).update(directory=new_dir)
    except Exception:
        if moved:
            try:
                os.rename(new_dir, old_dir)
            except OSError:
                logger.exception('rename %s -> %s failed AND the folder could not be moved back; '
                                 'it is at %s', old_username, new_username, new_dir)
        raise

    user.username = new_username
    logger.info('renamed user %s -> %s (folder moved: %s)', old_username, new_username, moved)
    return old_username

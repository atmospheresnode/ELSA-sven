# -*- coding: utf-8 -*-
"""Archiving projects with providers: bundles, people and milestones. Changes are audited."""
from __future__ import unicode_literals

from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone

from build.models import Bundle

from . import audit, roles
from .actions import ActionError, _require, _storable
from .models import Project, ProjectMilestone


def save(request, project, data):
    """Creates or updates a project from the form's fields."""
    _require(request, roles.REVIEW_BUNDLES)
    name = ' '.join((data.get('name') or '').split())[:150]
    if not name:
        raise ActionError('A project needs a name.')
    status = data.get('status') or Project.ACTIVE
    if status not in dict(Project.STATUS_CHOICES):
        raise ActionError('Unknown status.')
    _storable(name, data.get('mission'), data.get('description'), data.get('external_contacts'))
    with transaction.atomic():
        creating = project is None
        project = project or Project(created_by=request.user)
        project.name = name
        project.mission = ' '.join((data.get('mission') or '').split())[:150]
        project.description = (data.get('description') or '').strip()[:10000]
        project.external_contacts = (data.get('external_contacts') or '').strip()[:2000]
        project.status = status
        project.remind_providers = data.get('remind_providers') == '1'
        project.save()
        staff_ids = [int(i) for i in data.getlist('staff') if str(i).isdecimal()]
        project.staff.set([u for u in User.objects.filter(pk__in=staff_ids, is_active=True) if roles.is_member(u)])
        audit.record(request, 'project.create' if creating else 'project.update',
                     changes={'project': project.pk, 'name': name})
    return project


def add_bundle(request, project, name):
    _require(request, roles.REVIEW_BUNDLES)
    # Bundle.name is unique across all users (build migrations 0045, 0060).
    bundle = Bundle.objects.filter(name=(name or '').strip()).first()
    if bundle is None:
        raise ActionError('No bundle with that exact name.')
    project.bundles.add(bundle)
    # Add the bundle's owner as a provider on the project.
    project.providers.add(bundle.user)
    audit.record(request, 'project.bundle', bundle, changes={'project': project.pk, 'added': True})


def remove_bundle(request, project, bundle):
    _require(request, roles.REVIEW_BUNDLES)
    project.bundles.remove(bundle)
    audit.record(request, 'project.bundle', bundle, changes={'project': project.pk, 'added': False})


def add_provider(request, project, username):
    _require(request, roles.REVIEW_BUNDLES)
    user = User.objects.filter(username=(username or '').strip(), is_active=True).first()
    if user is None:
        raise ActionError('No active account with that username.')
    project.providers.add(user)
    audit.record(request, 'project.provider', user, changes={'project': project.pk, 'added': True})


def remove_provider(request, project, user):
    _require(request, roles.REVIEW_BUNDLES)
    project.providers.remove(user)
    audit.record(request, 'project.provider', user, changes={'project': project.pk, 'added': False})


def add_milestone(request, project, title, due_date):
    _require(request, roles.REVIEW_BUNDLES)
    title = ' '.join((title or '').split())[:200]
    if not title or due_date is None:
        raise ActionError('A milestone needs a name and a real date.')
    _storable(title)
    milestone = ProjectMilestone.objects.create(project=project, title=title, due_date=due_date)
    audit.record(request, 'project.milestone', changes={'project': project.pk, 'milestone': title,
                                                        'due': due_date.isoformat()})
    return milestone


def set_milestone_done(request, milestone, done):
    _require(request, roles.REVIEW_BUNDLES)
    ProjectMilestone.objects.filter(pk=milestone.pk).update(
        done_at=timezone.now() if done else None, done_by=request.user if done else None)
    audit.record(request, 'project.milestone', changes={'project': milestone.project_id,
                                                        'milestone': milestone.title, 'done': bool(done)})


def delete_milestone(request, milestone):
    _require(request, roles.REVIEW_BUNDLES)
    audit.record(request, 'project.milestone', changes={'project': milestone.project_id,
                                                        'milestone': milestone.title, 'deleted': True})
    milestone.delete()

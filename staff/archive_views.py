# -*- coding: utf-8 -*-
"""Admin Console pages for the archive: pipeline board, provider projects, releases."""
from __future__ import unicode_literals

import os

from django.contrib import messages
from django.contrib.auth.models import User
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from build.models import Bundle

from . import audit, pipeline, projects, releases, roles
from .actions import ActionError
from .decorators import console_view, limited
from .models import Case, Project, ProjectMilestone, Release
from .views import _console_context, _date, _pk, _saved_views, _text, _workers


def _act(request, call, success, *args, **kwargs):
    try:
        result = call(request, *args, **kwargs)
    except ActionError as error:
        messages.error(request, str(error))
        return None
    if success:
        messages.success(request, success)
    return result if result is not None else True


# --- Pipeline ---

@console_view()
def pipeline_board(request):
    project = Project.objects.filter(pk=_pk(request.GET.get('project'))).first()
    bundle_type = request.GET.get('type', '')
    reviewer = None
    if request.GET.get('reviewer') == 'me':
        reviewer = request.user
    elif _pk(request.GET.get('reviewer')):
        reviewer = User.objects.filter(pk=_pk(request.GET['reviewer'])).first()
    columns, stuck_days = pipeline.board(project=project, bundle_type=bundle_type, reviewer=reviewer)
    return render(request, 'staff/pipeline.html', _console_context(
        request, section='pipeline', columns=columns, stuck_days=stuck_days, project=project,
        projects=Project.objects.exclude(status=Project.DONE), bundle_type=bundle_type,
        reviewer=reviewer, reviewer_value=request.GET.get('reviewer', ''), workers=_workers(),
        saved_views=_saved_views(request, 'staff:pipeline'), view_page='staff:pipeline',
        stuck_total=sum(1 for _k, _l, cards in columns for card in cards if card['stuck'])))


# --- Projects ---

@console_view()
def project_list(request):
    if request.method == 'POST':
        project = _act(request, projects.save, 'Project created.', None, request.POST)
        if project and project is not True:
            return redirect('staff:project', pk=project.pk)
        return redirect('staff:projects')
    items = Project.objects.prefetch_related('staff', 'milestones', 'bundles')
    rows = []
    for item in items:
        upcoming = [m for m in item.milestones.all() if not m.done_at]
        rows.append({'project': item, 'next': upcoming[0] if upcoming else None,
                     'overdue': sum(1 for m in upcoming if m.due_date < _today())})
    return render(request, 'staff/projects.html', _console_context(
        request, section='projects', rows=rows, workers=_workers(), statuses=Project.STATUS_CHOICES))


def _today():
    from django.utils import timezone
    return timezone.localdate()


@console_view()
def project_detail(request, pk):
    project = get_object_or_404(Project, pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save':
            _act(request, projects.save, 'Saved.', project, request.POST)
        elif action == 'add_bundle':
            _act(request, projects.add_bundle, 'Bundle added; its owner is now a provider on the project.',
                 project, request.POST.get('bundle'))
        elif action == 'remove_bundle':
            bundle = get_object_or_404(Bundle, pk=_pk(request.POST.get('bundle')))
            _act(request, projects.remove_bundle, 'Bundle removed from the project.', project, bundle)
        elif action == 'add_provider':
            _act(request, projects.add_provider, 'Provider added.', project, request.POST.get('username'))
        elif action == 'remove_provider':
            person = get_object_or_404(User, pk=_pk(request.POST.get('user')))
            _act(request, projects.remove_provider, 'Provider removed.', project, person)
        elif action == 'add_milestone':
            _act(request, projects.add_milestone, 'Milestone added.', project, _text(request, 'title', 200),
                 _date(request.POST.get('due_date')))
        elif action in ('milestone_done', 'milestone_open', 'milestone_delete'):
            milestone = get_object_or_404(ProjectMilestone, pk=_pk(request.POST.get('milestone')), project=project)
            if action == 'milestone_delete':
                _act(request, projects.delete_milestone, 'Milestone removed.', milestone)
            else:
                _act(request, projects.set_milestone_done, 'Milestone updated.', milestone, action == 'milestone_done')
        else:
            messages.error(request, 'Unknown action.')
        return redirect('staff:project', pk=project.pk)
    bundles = project.bundles.select_related('user')
    reviews = {c.bundle_id: c for c in Case.objects.filter(kind=Case.SUBMISSION, bundle__in=bundles)
               .order_by('created_at')}
    return render(request, 'staff/project.html', _console_context(
        request, section='projects', page_title=project.name, project=project, workers=_workers(),
        bundles=[(b, reviews.get(b.pk)) for b in bundles], providers=project.providers.all(),
        milestones=project.milestones.all(), today=_today(), statuses=Project.STATUS_CHOICES))


# --- Releases ---

@console_view()
@require_POST
def release_start(request, pk):
    case = get_object_or_404(Case, pk=pk)
    release = _act(request, releases.start, None, case)
    if release and release is not True:
        return redirect('staff:release', pk=release.pk)
    return redirect('staff:case', pk=case.pk)


@console_view()
def release_detail(request, pk):
    release = get_object_or_404(Release.objects.select_related('bundle', 'bundle__user', 'case', 'final_run'), pk=pk)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'validate':
            run = _act(request, releases.run_final_validate, None, release)
            if run and run is not True:
                messages.success(request, 'Final full validate started (run #{0}). Refresh in a few minutes.'.format(run.pk))
        elif action == 'package':
            _act(request, releases.package_from_web, 'Delivery package built.', release)
        elif action == 'doi':
            _act(request, releases.set_doi, 'DOI saved.', release, request.POST.get('doi'), request.POST.get('doi_status'))
        elif action == 'delivered':
            _act(request, releases.record_delivery, 'Delivery recorded.', release, _text(request, 'note', 2000))
        elif action == 'registry':
            _act(request, releases.set_registry, 'Registry status saved.', release, request.POST.get('registry'))
        elif action == 'released':
            _act(request, releases.mark_released, 'Released. The owner has been told.', release,
                 _text(request, 'note', 4000), announce=request.POST.get('announce') == '1')
        else:
            messages.error(request, 'Unknown action.')
        return redirect('staff:release', pk=release.pk)
    own = release.bundle.user_id == request.user.pk
    return render(request, 'staff/release.html', _console_context(
        request, section='pipeline', help_topic='release', page_title='{0}: {1}'.format(release.bundle.name, release.label),
        release=release, steps=release.steps(), own=own,
        can_act=roles.has(request.user, roles.REVIEW_BUNDLES) and not own and not release.released_at,
        package_available=releases.package_file(release) is not None,
        registry_choices=[c for c in Release.REGISTRY_CHOICES if c[0]],
        web_limit_gb=releases.WEB_PACKAGE_LIMIT // 1024 ** 3))


@console_view()
@limited('download')
def release_package(request, pk):
    release = get_object_or_404(Release, pk=pk)
    path = releases.package_file(release)
    if path is None:
        raise Http404('The package is not available.')
    if not releases.package_intact(release, path):
        audit.record(request, 'security.alert', release.bundle, reason='Release package changed on disk',
                     changes={'release': release.pk})
        messages.error(request, 'The package on the server is not the one ELSA built (its checksum changed). '
                                'Build it again before delivering it, and tell the ELSA developers.')
        return redirect('staff:release', pk=release.pk)
    audit.record(request, 'bundle.download', release.bundle, changes={'release_package': release.pk})
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=os.path.basename(path))

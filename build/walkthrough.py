"""The guided setup.

The setup page (templates/build/setup/setup.html) shows one step at a time. Step order, what's
required and what counts as done all live here.

Old walkthrough views are still used by the bundle page. A request from there says so
(``source=bundle`` or ``?next=bundle``) and goes back. Anything else goes to the setup page
(moved_to_setup, after_save).
"""

import functools

from django.shortcuts import redirect
from django.urls import reverse


# Done means the bundle has it, since steps can be skipped or done in any order.
def _has_alias(bundle):
    return bundle.alias_set.exists()


def _has_modification_history(bundle):
    return bundle.modification_history_set.exists()


def _has_citation(bundle):
    return bundle.citation_information_set.exists()


def _has_author(bundle):
    # Imported here to avoid a circular import.
    from build import preflight
    return preflight._has_author(bundle)


def _has_complete_citation(bundle):
    # An External citation needs an author (build.preflight), so it isn't done without one.
    if not _has_citation(bundle):
        return False
    return bundle.bundle_type != 'External' or _has_author(bundle)


def _has_investigation(bundle):
    return bundle.investigations.exists()


def _has_target(bundle):
    return bundle.targets.exists()


def _has_document(bundle):
    return bundle.product_document_set.exists()


def _has_processed_netcdf(bundle):
    from build import preflight
    return preflight._has_processed_netcdf(bundle)


class Step(object):
    """One setup step.

    ``key`` is its address on the page (``#citation``). ``legacy`` is the name the old views pass
    to after_save() and progress().
    """

    def __init__(self, key, label, required, done, legacy=None):
        self.key = key
        self.label = label
        self.required = required
        self.done = done
        self.legacy = legacy


STEPS = {
    'Archive': [
        # No modification history step, ELSA writes the first entry at creation.
        Step('alias', 'Alias', False, _has_alias, legacy='alias'),
        Step('citation', 'Citation', True, _has_complete_citation, legacy='citation_information'),
        Step('investigation', 'Investigation', False, _has_investigation, legacy='context_search'),
        # Archive bundles need a target too (build.preflight).
        Step('targets', 'Targets', True, _has_target),
    ],
    'External': [
        # No modification history step here either; see Archive.
        Step('citation', 'Citation', True, _has_complete_citation, legacy='citation_information'),
        Step('targets', 'Targets', True, _has_target, legacy='context_search'),
        Step('documents', 'Documents', True, _has_document, legacy='annex_collection_document'),
        Step('data', 'Data', True, _has_processed_netcdf, legacy='collection_additional'),
    ],
}

# The last stop on the setup page: what is still needed, and the way on to the bundle.
FINISH = 'finish'


def steps_for(bundle):
    return STEPS.get(bundle.bundle_type, [])


def _find(bundle, name):
    """The step a legacy view name or a setup key refers to, and its position."""
    for index, step in enumerate(steps_for(bundle)):
        if name in (step.key, step.legacy):
            return index, step
    return None, None


def returning_to_bundle(request):
    """True when the post or link was started on the bundle page."""
    return (request.POST.get('source') == 'bundle'
            or request.GET.get('next') == 'bundle')


def bundle_url(bundle, anchor=''):
    url = reverse('build:bundle', args=[bundle.pk])
    return url + ('#' + anchor if anchor else '')


def setup_url(bundle, step=''):
    url = reverse('build:setup', args=[bundle.pk])
    return url + ('#' + step if step else '')


def next_url(bundle, step):
    """The step after ``step``, finish after the last, or the bundle page if this type doesn't have it."""
    index, _ = _find(bundle, step)
    if index is None:
        return bundle_url(bundle)
    steps = steps_for(bundle)
    following = steps[index + 1].key if index + 1 < len(steps) else FINISH
    return setup_url(bundle, following)


def after_save(request, bundle, step, anchor=''):
    """Where a view goes once it has saved: back to the bundle, or on to the next step."""
    if returning_to_bundle(request):
        return bundle_url(bundle, anchor)
    return next_url(bundle, step)


def moved_to_setup(step):
    """Send a GET of an old step page to that step on the setup page. Posts and bundle page requests stay."""
    def decorator(view):
        @functools.wraps(view)
        def wrapper(request, *args, **kwargs):
            if request.method == 'GET' and not returning_to_bundle(request):
                from build.models import Bundle
                pk = kwargs.get('pk_bundle', args[0] if args else None)
                bundle = Bundle.objects.filter(pk=pk).first()
                if bundle is not None and bundle.user == request.user:
                    _, found = _find(bundle, step)
                    if found is not None:
                        return redirect(setup_url(bundle, found.key))
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


def chip(bundle, step, done=None):
    """Status beside a step: Added, Missing or Optional, same as Bundle Components."""
    done = step.done(bundle) if done is None else done
    if done:
        counts = {
            'targets': lambda: bundle.targets.count(),
            'documents': lambda: bundle.product_document_set.count(),
            'investigation': lambda: bundle.investigations.count(),
            'data': lambda: bundle.netcdf_files.filter(processed=True).count(),
        }
        nouns = {'targets': 'target', 'documents': 'document',
                 'investigation': 'investigation', 'data': 'NetCDF file'}
        if step.key in counts:
            n = counts[step.key]()
            return {'text': '{} {}{}'.format(n, nouns[step.key], '' if n == 1 else 's'),
                    'tone': 'added'}
        return {'text': 'Added', 'tone': 'added'}
    if step.key == 'citation' and _has_citation(bundle):
        return {'text': 'Needs an author', 'tone': 'missing'}
    if step.required:
        return {'text': 'Missing', 'tone': 'missing'}
    return {'text': 'Optional', 'tone': 'optional'}


def is_untouched(bundle):
    """True if nothing was added since the bundle was created, so it can be discarded."""
    if bundle.submitted_at:
        return False
    if bundle.netcdf_files.exists() or bundle.product_document_set.exists():
        return False
    return not any(step.done(bundle) for step in steps_for(bundle))


def required_left(bundle):
    """How many required setup steps this bundle still needs (0 when set up)."""
    return sum(1 for step in steps_for(bundle) if step.required and not step.done(bundle))


def summary(bundle):
    """Each step with its state, and how many of the required ones are done."""
    steps = steps_for(bundle)
    items = []
    for number, step in enumerate(steps, start=1):
        done = step.done(bundle)
        items.append({'key': step.key, 'label': step.label, 'number': number,
                      'required': step.required, 'done': done,
                      'chip': chip(bundle, step, done),
                      'prev': steps[number - 2].key if number > 1 else '',
                      'next': steps[number].key if number < len(steps) else FINISH})
    required = [item for item in items if item['required']]
    return {
        'steps': items,
        'last': items[-1]['key'] if items else '',
        'skipped': [item['label'] for item in items if not item['required'] and not item['done']],
        'required_total': len(required),
        'required_done': sum(1 for item in required if item['done']),
        'complete': all(item['done'] for item in required),
    }


def progress(bundle, step):
    """Stepper for an old walkthrough page, or None. Only used when a post there is refused."""
    index, _ = _find(bundle, step)
    if index is None:
        return None
    steps = steps_for(bundle)
    items = []
    for position, item in enumerate(steps):
        done = item.done(bundle)
        items.append({
            'label': item.label,
            'number': position + 1,
            'state': 'current' if position == index else 'done' if done else 'todo',
            'url': '' if position == index else setup_url(bundle, item.key),
        })
    return {
        'steps': items,
        'number': index + 1,
        'total': len(steps),
        'label': steps[index].label,
        'next_url': next_url(bundle, step),
        'is_last': index + 1 == len(steps),
        'exit_url': bundle_url(bundle),
    }

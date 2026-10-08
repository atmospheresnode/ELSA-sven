"""The guided walkthrough, in one place.

Every walkthrough page used to carry its own idea of what comes next: a hard-coded
redirect on success, another in the template's Skip link, and a third in the template
it re-rendered on error. The same views also serve edits started on the bundle page,
so an edit that failed validation, or that simply forgot to say where it came from,
fell through into the walkthrough and left the user several steps from their bundle
with no way back.

So the order of the steps lives here and nowhere else, and every view that finishes a
step asks after_save() where to go. A request that came from the bundle page says so
(``source=bundle`` on the post, or ``?next=bundle`` on the URL) and goes back there;
anything else is the walkthrough and moves on to the next step.

The steps are the ones the walkthrough already took. Archive ends at context products
because its investigation form posts to the bundle page, which is where an Archive
walkthrough has always finished.
"""

from django.urls import reverse


# (step, URL name, label). The URL name is None for a step whose page is reached from
# the step before it rather than by a link of its own.
STEPS = {
    'Archive': [
        ('alias', 'build:alias', 'Alias'),
        ('modification_history', 'build:modification_history', 'Modification history'),
        ('citation_information', 'build:citation_information', 'Citation'),
        ('context_search', 'build:context_search', 'Context products'),
    ],
    'External': [
        ('citation_information', 'build:citation_information', 'Citation'),
        ('context_search', 'build:context_search', 'Targets'),
        ('annex_collection_document', 'build:annex_collection_document', 'Documents'),
        ('collection_additional', 'build:collection_additional', 'Data collection'),
    ],
}


def returning_to_bundle(request):
    """True when the post or link was started on the bundle page."""
    return (request.POST.get('source') == 'bundle'
            or request.GET.get('next') == 'bundle')


def bundle_url(bundle, anchor=''):
    url = reverse('build:bundle', args=[bundle.pk])
    return url + ('#' + anchor if anchor else '')


def _steps(bundle):
    return STEPS.get(bundle.bundle_type, [])


def _step_url(bundle, url_name):
    return reverse(url_name, args=[bundle.pk])


def next_url(bundle, step):
    """The page after ``step``, or the bundle page once the walkthrough is over."""
    steps = _steps(bundle)
    names = [name for name, _, _ in steps]
    if step in names:
        index = names.index(step)
        if index + 1 < len(steps):
            return _step_url(bundle, steps[index + 1][1])
    return bundle_url(bundle)


def after_save(request, bundle, step, anchor=''):
    """Where a view goes once it has saved: back to the bundle, or on to the next step."""
    if returning_to_bundle(request):
        return bundle_url(bundle, anchor)
    return next_url(bundle, step)


def progress(bundle, step):
    """What the stepper on a walkthrough page shows, or None off the walkthrough."""
    steps = _steps(bundle)
    names = [name for name, _, _ in steps]
    if step not in names:
        return None
    index = names.index(step)
    items = []
    for position, (name, url_name, label) in enumerate(steps):
        items.append({
            'label': label,
            'number': position + 1,
            'state': 'done' if position < index else 'current' if position == index else 'todo',
            # Only steps already passed are links: a later one may depend on this one.
            'url': _step_url(bundle, url_name) if position < index else '',
        })
    return {
        'steps': items,
        'number': index + 1,
        'total': len(steps),
        'label': steps[index][2],
        'next_url': next_url(bundle, step),
        'is_last': index + 1 == len(steps),
        'exit_url': bundle_url(bundle),
    }

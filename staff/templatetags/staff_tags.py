from django import template
from django.utils import timezone
from django.utils.timesince import timesince

from staff.audit import action_label

register = template.Library()


@register.filter
def age(value):
    """Largest unit only, like '3 days'. Never '0 minutes'."""
    if not value:
        return ''
    if (timezone.now() - value).total_seconds() < 60:
        return 'just now'
    return timesince(value, depth=1)


@register.filter
def age_class(value):
    """Amber after 2 days waiting, red after 5."""
    if not value:
        return ''
    days = (timezone.now() - value).total_seconds() / 86400.0
    if days > 5:
        return 'staff-age-danger'
    if days > 2:
        return 'staff-age-warning'
    return ''


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def audit_action(value):
    return action_label(value)


@register.filter
def filesize(value):
    """Bytes as GB with one decimal, for disk figures."""
    try:
        return '{0:.1f} GB'.format(float(value) / (1024 ** 3))
    except (TypeError, ValueError):
        return ''


def _label(choices, value):
    return dict(choices).get(value, value) or 'nothing'


@register.filter
def audit_details(changes):
    """An audit entry's recorded facts as short sentences a person can read."""
    from friends.models import UserProfile
    from staff.models import Case
    if not isinstance(changes, dict):
        return ''
    out = []
    for key, value in changes.items():
        pair = isinstance(value, (list, tuple)) and len(value) == 2
        if key == 'assignee' and pair:
            out.append('Assignee from {0} to {1}'.format(value[0] or 'nobody', value[1] or 'nobody'))
        elif key == 'status' and pair:
            out.append('Status from {0} to {1}'.format(_label(Case.STATUS_CHOICES, value[0]),
                                                       _label(Case.STATUS_CHOICES, value[1])))
        elif key == 'stage' and pair:
            out.append('Review stage from {0} to {1}'.format(_label(Case.STAGE_CHOICES, value[0]),
                                                             _label(Case.STAGE_CHOICES, value[1])))
        elif key == 'agency' and pair:
            out.append('Agency from {0} to {1}'.format(_label(UserProfile.AGENCY_CHOICES, value[0]),
                                                       _label(UserProfile.AGENCY_CHOICES, value[1])))
        elif key == 'is_active' and pair:
            out.append('Account {0}'.format('enabled' if value[1] else 'disabled'))
        elif key == 'roles' and pair:
            out.append('Role from {0} to {1}'.format(', '.join(value[0]) or 'none', ', '.join(value[1]) or 'none'))
        elif key == 'checklist' and pair and isinstance(value[1], dict):
            out.append('Checklist: {0} of {1} ticked'.format(sum(1 for v in value[1].values() if v), len(value[1])))
        elif key == 'accept_anyway':
            if value:
                out.append('Accepted with checklist items unticked')
        elif key == 'via':
            out.append('Confirmed with a passkey' if value == 'passkey' else 'Using the passkey from sign-in')
        elif key == 'passkey':
            out.append('Passkey "{0}"'.format(value))
        elif key == 'to':
            out.append('Sent to {0}'.format(value))
        elif key == 'path':
            out.append('File {0}'.format(value))
        elif value not in ('', None, [], {}):
            out.append('{0}: {1}'.format(key.replace('_', ' ').capitalize(), value))
    return '; '.join(out)


@register.filter
def mentions(text):
    """Escape a note, then mark @username so mentions stand out."""
    import re
    from django.utils.html import conditional_escape
    from django.utils.safestring import mark_safe
    escaped = str(conditional_escape(text or ''))
    # Safe since the text is escaped above and [\w.+-] can't contain HTML. See XssTests.
    marked = re.sub(r'(?<![\w@])@([\w.+-]{2,150})', r'<strong class="staff-mention">@\1</strong>', escaped)
    return mark_safe(marked)  # nosec B308 B703


@register.filter
def reply_state(case):
    """'late' / 'soon' / 'ok' / None against the first-reply target."""
    from staff import targets
    return targets.state(case)


@register.filter
def away_until(user):
    """The date someone is away until, or None."""
    from django.utils import timezone
    from staff.models import StaffPreference
    value = StaffPreference.objects.filter(user=user).values_list('away_until', flat=True).first()
    return value if value and value >= timezone.localdate() else None

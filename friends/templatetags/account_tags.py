from django import template
from django.utils import timezone
from django.utils.timesince import timesince

register = template.Library()


@register.filter
def ago(value):
    """'just now' under a minute, else Django's '3 hours ago'."""
    if not value:
        return ''
    if (timezone.now() - value).total_seconds() < 60:
        return 'just now'
    return '{0} ago'.format(timesince(value))


@register.simple_tag(takes_context=True)
def console_link(context):
    """The Admin Console address for staff, '' for everyone else.

    In here so base-derk.html still loads on a server without the staff app.
    """
    from staff.hooks import console_link as link
    request = context.get('request')
    return link(getattr(request, 'user', None))


@register.inclusion_tag('friends/_site_banners.html', takes_context=True)
def site_banners(context):
    """Announcements and the "viewing as" bar, if the staff app is installed."""
    from staff.hooks import announcements_for
    request = context.get('request')
    return {
        'request': request,
        'csrf_token': context.get('csrf_token'),
        'announcements': announcements_for(request) if request is not None else [],
        'viewing_as': getattr(request, 'staff_viewing_as', None),
    }


@register.simple_tag(takes_context=True)
def my_requests_link(context):
    """(url, unread count) for the user menu, or ('', 0) without the staff app."""
    from django.urls import NoReverseMatch, reverse
    from staff.hooks import routed, unread_replies
    request = context.get('request')
    if not routed() or request is None:
        return ('', 0)
    try:
        return (reverse('staff_public:requests'), unread_replies(getattr(request, 'user', None)))
    except NoReverseMatch:
        return ('', 0)


@register.simple_tag(takes_context=True)
def notification_bell(context):
    """Bell count and latest notifications, or None without the staff app."""
    from staff.hooks import bell
    request = context.get('request')
    return bell(getattr(request, 'user', None)) if request is not None else None


@register.simple_tag(takes_context=True)
def admin_alerts(context):
    """The Admin Console counts for staff (numbers only), or None."""
    from staff.hooks import admin_alerts as counts
    request = context.get('request')
    return counts(getattr(request, 'user', None)) if request is not None else None

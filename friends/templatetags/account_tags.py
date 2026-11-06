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

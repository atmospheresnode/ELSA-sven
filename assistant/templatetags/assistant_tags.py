from django import template
from django.conf import settings

register = template.Library()


@register.simple_tag
def assistant_enabled():
    """Kill switch: set ASSISTANT_ENABLED = False in settings to hide the widget."""
    return getattr(settings, 'ASSISTANT_ENABLED', True)

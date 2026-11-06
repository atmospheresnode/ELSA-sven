from django import template

register = template.Library()


@register.simple_tag
def assistant_enabled():
    """Kill switch: ASSISTANT_ENABLED=False or the `manage.py assistant_toggle off` cache flag."""
    from assistant.views import _assistant_enabled
    return _assistant_enabled()

from django import template

register = template.Library()


@register.simple_tag
def assistant_enabled():
    """Kill switch: ASSISTANT_ENABLED=False in settings, or the instant cache
    flag set by `manage.py assistant_toggle off`. Hides the widget entirely."""
    from assistant.views import _assistant_enabled
    return _assistant_enabled()

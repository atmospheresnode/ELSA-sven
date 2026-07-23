"""Instant kill switch for the assistant, no config edit or restart needed.

    python3 manage.py assistant_toggle off   # hide widget, 503 all endpoints
    python3 manage.py assistant_toggle on    # re-enable
    python3 manage.py assistant_toggle       # show current state

Uses the shared DB-backed cache, so it applies to every worker process at
once and survives restarts until toggled back on.
"""
from django.core.cache import cache
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Enable or disable the ELSA Assistant instantly via the shared cache.'

    def add_arguments(self, parser):
        parser.add_argument('state', nargs='?', choices=['on', 'off'],
                            help='Omit to show the current state.')

    def handle(self, *args, **options):
        state = options['state']
        if state == 'off':
            cache.set('assistant-disabled', 1, None)  # no expiry
            self.stdout.write(self.style.WARNING('Assistant DISABLED (cache flag set).'))
        elif state == 'on':
            cache.delete('assistant-disabled')
            self.stdout.write(self.style.SUCCESS('Assistant enabled (cache flag cleared).'))
        else:
            disabled = bool(cache.get('assistant-disabled'))
            self.stdout.write('Assistant is currently: {}'.format(
                'DISABLED (cache flag)' if disabled else 'enabled'))

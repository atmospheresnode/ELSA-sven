"""Email each staff member a morning summary of what's open.

    python3 manage.py staff_digest

Can be turned off under Preferences. Nothing is sent if nothing is open.
Cron, weekdays at 08:00:
    0 8 * * 1-5 cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py staff_digest
"""
from datetime import timedelta

from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from staff import comms
from staff.models import Case, StaffPreference

OVERDUE = timedelta(days=5)


class Command(BaseCommand):
    help = 'Email staff a summary of open requests.'

    def handle(self, *args, **options):
        active = list(Case.objects.filter(status__in=Case.ACTIVE, is_spam=False)
                      .select_related('assignee').order_by('created_at'))
        if not active:
            self.stdout.write('Nothing open; no digest sent.')
            return
        now = timezone.now()
        sent = 0
        for person in comms.members():
            pref = StaffPreference.for_user(person)
            if not person.email or not pref.daily_digest:
                continue
            if pref.away_until and pref.away_until >= timezone.localdate():
                continue
            mine = [c for c in active if c.assignee_id == person.pk]
            unassigned = [c for c in active if c.assignee_id is None]
            overdue = [c for c in active if now - c.created_at > OVERDUE]
            answered = [c for c in active if comms.staff_has_unread(c, person)
                        and (c.assignee_id in (None, person.pk))]
            lines = ['Good morning {0}. {1} request{2} open in the Admin Console.'.format(
                person.first_name or person.username, len(active), '' if len(active) == 1 else 's'), '']
            for title, cases in (('Users answered and are waiting for you', answered),
                                 ('Assigned to you', mine),
                                 ('Nobody has taken these yet', unassigned),
                                 ('Open for more than 5 days', overdue)):
                if cases:
                    lines.append('{0} ({1}):'.format(title, len(cases)))
                    lines += ['  #{0} {1} ({2}, {3} days)'.format(c.pk, c.title, c.get_kind_display(),
                                                                  (now - c.created_at).days)
                              for c in cases[:15]]
                    lines.append('')
            lines.append('Open the Admin Console: {0}'.format(comms.absolute(reverse('staff:today'))))
            lines.append('Switch this email off under Preferences in the Admin Console.')
            try:
                EmailMessage(subject='Admin Console: {0} open request{1}'.format(
                                 len(active), '' if len(active) == 1 else 's'),
                             body='\n'.join(lines), from_email='atm-elsa@nmsu.edu',
                             to=[person.email]).send()
                sent += 1
            except Exception as error:
                self.stderr.write('digest to {0} failed: {1}'.format(person.username, error))
        self.stdout.write('Digest sent to {0} staff member(s).'.format(sent))

"""Remind staff, and providers if the project allows it, about project milestones.

    python3 manage.py staff_reminders

Sends at 14 days out, 3 days out, on the due date, then weekly while overdue.
Cron, daily at 07:30:
    30 7 * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py staff_reminders
"""
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from staff import comms
from staff.models import Notification, ProjectMilestone


def due_today(days_left):
    return days_left in (14, 3, 0) or (days_left < 0 and (-days_left) % 7 == 0)


class Command(BaseCommand):
    help = 'Email reminders about project milestones.'

    def handle(self, *args, **options):
        today = timezone.localdate()
        sent = 0
        for milestone in (ProjectMilestone.objects.filter(done_at__isnull=True)
                          .exclude(reminded_on=today).select_related('project')):
            project = milestone.project
            if project.status == project.DONE:
                continue
            days_left = (milestone.due_date - today).days
            if not due_today(days_left):
                continue
            when = ('is due today' if days_left == 0 else
                    'is due in {0} days'.format(days_left) if days_left > 0 else
                    'was due {0} days ago'.format(-days_left))
            subject = '{0}: "{1}" {2}'.format(project.name, milestone.title, when)
            staff = sorted({u.email for u in project.staff.all() if u.is_active and u.email})
            if staff:
                try:
                    EmailMessage(subject='Admin Console: ' + subject,
                                 body='{0} ({1:%B %d, %Y}).\n\nProject:\n{2}\n'.format(
                                     subject, milestone.due_date,
                                     comms.absolute(reverse('staff:project', args=[project.pk]))),
                                 from_email='atm-elsa@nmsu.edu', to=staff).send()
                    sent += 1
                except Exception as error:
                    self.stderr.write('staff reminder failed: {0}'.format(error))
            if project.remind_providers:
                for provider in project.providers.filter(is_active=True):
                    try:
                        comms.tell_user(provider, Notification.MESSAGE, 'Reminder: {0}'.format(subject),
                                        'This is a reminder from the PDS Atmospheres Node about your archiving '
                                        'project "{0}": "{1}" {2} ({3:%B %d, %Y}). If anything is in the way, '
                                        'message the ELSA team from your Account page.'.format(
                                            project.name, milestone.title, when, milestone.due_date))
                        sent += 1
                    except Exception as error:
                        self.stderr.write('provider reminder failed: {0}'.format(error))
            ProjectMilestone.objects.filter(pk=milestone.pk).update(reminded_on=today)
        self.stdout.write('Sent {0} reminder(s).'.format(sent))

"""Monthly access review, emailed to the developers.

    python3 manage.py staff_access_review

Lists every account with a role or superuser status, their passkeys, when they last opened
admin mode, and the audit log's current seal. Remove anyone who shouldn't be there with
revoke_staff and keep the email. Cron, the 1st of each month at 07:00:
    0 7 1 * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py staff_access_review
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.core.management.base import BaseCommand
from django.db.models import Max, Q
from django.utils import timezone

from staff import audit, roles, security_alerts, selfcheck
from staff.models import AuditChain, StaffSession

from ._staffing import server_actor

QUIET = timedelta(days=90)


class Command(BaseCommand):
    help = 'Email the developers the list of people who can open the Admin Console.'

    def handle(self, *args, **options):
        people = (User.objects.filter(Q(is_superuser=True) | Q(groups__name__in=roles.GROUP_NAMES))
                  .distinct().order_by('username'))
        last_seen = dict(StaffSession.objects.values('user').annotate(last=Max('last_seen_at'))
                         .values_list('user', 'last'))
        lines = ['People who can open the Admin Console on {0:%B %d, %Y}:'.format(timezone.localdate()), '']
        quiet = []
        for person in people:
            seen = last_seen.get(person.pk)
            lines.append('- {0} ({1}){2}: {3}; {4} passkey{5}; last in admin mode {6}'.format(
                person.username, person.email or 'no email', '' if person.is_active else ' [disabled]',
                roles.role_label(person), person.passkeys.count(), '' if person.passkeys.count() == 1 else 's',
                '{0:%B %d, %Y}'.format(timezone.localtime(seen)) if seen else 'never'))
            if person.is_active and (seen is None or seen < timezone.now() - QUIET):
                quiet.append(person.username)
        if quiet:
            lines += ['', 'Not in admin mode for 90 days or more: {0}. If they no longer need it, '
                          'run "manage.py revoke_staff <username>".'.format(', '.join(quiet))]
        problems = [row for row in selfcheck.run() if row['level'] != selfcheck.OK]
        if problems:
            lines += ['', 'Security check items to look at:']
            lines += ['- {0}: {1}'.format(row['name'], row['found']) for row in problems]
        chain = AuditChain.objects.filter(pk=1).first()
        if chain is not None and chain.head_hash:
            lines += ['', 'Audit log seal today ({0} entries): {1}'.format(chain.count, chain.head_hash),
                      'Keep this email: if the log is ever rewritten, this seal will no longer be in it.']
        to = security_alerts.recipients()
        if not to:
            self.stderr.write('Nobody to send the review to.')
            return
        EmailMessage(subject='[ELSA security] Monthly Admin Console access review',
                     body='\n'.join(lines) + '\n', from_email=security_alerts.FROM, to=to).send()
        audit.record(None, 'security.access_review', actor_name=server_actor(),
                     changes={'people': people.count(), 'quiet': quiet})
        self.stdout.write(self.style.SUCCESS('Access review sent to {0}.'.format(', '.join(to))))

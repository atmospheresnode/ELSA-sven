"""Check that the Admin Console audit log hasn't been altered.

    python3 manage.py verify_audit

If an entry was changed, removed or reordered it says which, emails the developers and
exits with status 1. Cron, nightly at 02:30:
    30 2 * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py verify_audit
"""
import sys

from django.core.management.base import BaseCommand

from staff import audit, security_alerts


class Command(BaseCommand):
    help = 'Check the Admin Console audit log for tampering.'

    def handle(self, *args, **options):
        ok, problem = audit.verify()
        if ok:
            self.stdout.write(self.style.SUCCESS('The audit log is intact.'))
            return
        security_alerts.audit_broken(problem)
        self.stderr.write(self.style.ERROR('The audit log failed its check: ' + problem))
        sys.exit(1)

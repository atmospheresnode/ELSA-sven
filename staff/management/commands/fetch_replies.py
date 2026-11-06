"""Read emailed replies from the inbound mailbox and save them on their cases.

    python3 manage.py fetch_replies

Configured in settings.py:

    ELSA_INBOUND = {
        'address': 'elsa-replies@nmsu.edu',
        'imap_host': 'outlook.office365.com',
        'imap_port': 993,
        'imap_user': 'elsa-replies@nmsu.edu',
        'imap_password': '...',                  # from secrets.py
        'mailbox': 'INBOX',
        'done_folder': 'ELSA-processed',         # optional
    }

Cron, every five minutes:
    */5 * * * * cd /export/atmos1/htdocs/elsa && /usr/local/venvs/ELSA_3.11/bin/python manage.py fetch_replies
"""
import imaplib

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from staff import inbound


class Command(BaseCommand):
    help = 'Fetch emailed replies from the inbound mailbox.'

    def handle(self, *args, **options):
        config = getattr(settings, 'ELSA_INBOUND', None) or {}
        if not config.get('imap_host'):
            raise CommandError('ELSA_INBOUND is not configured on this server; replies are read in ELSA only.')

        mail = imaplib.IMAP4_SSL(config['imap_host'], int(config.get('imap_port', 993)))
        try:
            mail.login(config['imap_user'], config['imap_password'])
            mail.select(config.get('mailbox', 'INBOX'))
            _, found = mail.search(None, 'UNSEEN')
            counts = {}
            for number in (found[0] or b'').split():
                try:
                    _, data = mail.fetch(number, '(RFC822)')
                    raw = next((part[1] for part in data if isinstance(part, tuple)), b'')
                except Exception as error:
                    self.stderr.write('could not fetch message {0}: {1}'.format(number, error))
                    continue
                outcome, _case = inbound.ingest(raw)
                counts[outcome] = counts.get(outcome, 0) + 1
                done = config.get('done_folder')
                if done and mail.copy(number, done)[0] == 'OK':
                    mail.store(number, '+FLAGS', '\\Deleted')
                else:
                    mail.store(number, '+FLAGS', '\\Seen')
            mail.expunge()
        finally:
            try:
                mail.logout()
            except Exception:
                pass
        self.stdout.write(', '.join('{0}: {1}'.format(k, v) for k, v in sorted(counts.items())) or 'nothing new')

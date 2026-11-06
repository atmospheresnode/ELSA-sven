# -*- coding: utf-8 -*-
"""Email backend that logs what ELSA sent, then hands it to the real backend.

Turn it on in settings.py:

    EMAIL_BACKEND = 'staff.mail_log.LoggingBackend'
    ELSA_MAIL_INNER_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

If the log can't be written the mail still goes.
"""
from __future__ import unicode_literals

import logging

from django.apps import apps
from django.conf import settings
from django.core.mail import get_connection
from django.core.mail.backends.base import BaseEmailBackend


logger = logging.getLogger(__name__)

DEFAULT_INNER = 'django.core.mail.backends.smtp.EmailBackend'


class LoggingBackend(BaseEmailBackend):

    def __init__(self, fail_silently=False, **kwargs):
        super(LoggingBackend, self).__init__(fail_silently=fail_silently)
        self.inner = get_connection(getattr(settings, 'ELSA_MAIL_INNER_BACKEND', DEFAULT_INNER),
                                    fail_silently=fail_silently, **kwargs)

    def open(self):
        return self.inner.open()

    def close(self):
        return self.inner.close()

    def send_messages(self, email_messages):
        error = ''
        try:
            sent = self.inner.send_messages(email_messages)
        except Exception as failure:
            error = '{0}: {1}'.format(type(failure).__name__, failure)
            self._log(email_messages, error)
            raise
        self._log(email_messages, error)
        return sent

    def _log(self, email_messages, error):
        if not apps.is_installed('staff'):
            return
        try:
            from .models import MailLog
            MailLog.objects.bulk_create([MailLog(
                subject=str(m.subject)[:255],
                to=', '.join(list(m.to) + list(m.cc) + list(m.bcc))[:5000],
                from_email=str(m.from_email or '')[:255],
                sent=not error, error=error[:2000]) for m in email_messages])
        except Exception:
            logger.exception('could not log %d email(s)', len(email_messages))

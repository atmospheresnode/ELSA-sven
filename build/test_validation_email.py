"""When a validation result is emailed, and when it is deliberately not.

The policy exists because mailing every run trains people to filter the sender. A
structure check takes a few seconds and its result is on screen before an email
could arrive; only a run long enough to have been walked away from is worth a
message.
"""
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from build.models import Bundle, ValidationRun
from build.validate_runner import notify_if_slow


class NotificationPolicyTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            'mailer', password='pw', email='mailer@example.com')
        self.bundle = Bundle.objects.create(
            name='mail bundle', user=self.user, version='1O00')

    def run_lasting(self, seconds, status=ValidationRun.STATUS_DONE, findings=None):
        started = timezone.now() - timezone.timedelta(seconds=seconds)
        run = ValidationRun.objects.create(
            bundle=self.bundle, status=status,
            findings=findings if findings is not None else [])
        ValidationRun.objects.filter(pk=run.pk).update(
            started_at=started, finished_at=timezone.now())
        run.refresh_from_db()
        return run

    def test_a_quick_check_is_not_emailed(self):
        """Its result is on the screen before a message could arrive."""
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            self.assertFalse(notify_if_slow(self.run_lasting(4)))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_long_check_is_emailed(self):
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            self.assertTrue(notify_if_slow(self.run_lasting(300)))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('mail bundle', mail.outbox[0].subject)

    def test_five_quick_checks_send_nothing(self):
        """A ten-minute fixing session must not produce five emails."""
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            for _ in range(5):
                notify_if_slow(self.run_lasting(5))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_failed_run_says_so(self):
        run = self.run_lasting(300, status=ValidationRun.STATUS_FAILED)
        run.failure_reason = 'validate is not installed'
        run.save()
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            notify_if_slow(run)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('could not finish', mail.outbox[0].subject.lower())
        self.assertIn('not installed', mail.outbox[0].body)

    def test_a_clean_result_says_so(self):
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            notify_if_slow(self.run_lasting(300, findings=[]))
        self.assertIn('passed', mail.outbox[0].subject.lower())

    def test_the_count_comes_from_the_translation_not_the_raw_total(self):
        """Telling someone they have 43 errors when 41 are ELSA's own is not useful."""
        findings = [
            {'severity': 'ERROR', 'type': 'error.label.schematron',
             'message': 'In Product_Bundle both Citation_Information and its '
                        'description are required.',
             'label': 'b.xml', 'label_path': '/b.xml', 'line': 1,
             'element_path': 'Product_Bundle/Identification_Area'},
        ] + [
            {'severity': 'ERROR', 'type': 'error.label.schema',
             'message': "cvc-minLength-valid: Value '' with length = '0' is not valid.",
             'label': 'b.xml', 'label_path': '/b.xml', 'line': n,
             'element_path': 'Product_Bundle/Context_Area/Time_Coordinates/start_date_time'}
            for n in range(40)
        ]
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            notify_if_slow(self.run_lasting(300, findings=findings))
        body = mail.outbox[0].body
        self.assertIn('1 item', body)
        self.assertNotIn('41', body)
        self.assertNotIn('40', body)

    def test_a_user_with_no_address_is_skipped(self):
        self.user.email = ''
        self.user.save()
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            self.assertFalse(notify_if_slow(self.run_lasting(300)))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_mail_failure_does_not_fail_the_run(self):
        """A mail server having a bad afternoon must not undo a finished validation."""
        from unittest import mock
        run = self.run_lasting(300)
        with override_settings(VALIDATE_EMAIL_AFTER_SECONDS=30):
            with mock.patch('build.validate_runner.EmailMessage') as message:
                message.return_value.send.side_effect = OSError('smtp is down')
                self.assertFalse(notify_if_slow(run))
        run.refresh_from_db()
        self.assertEqual(run.status, ValidationRun.STATUS_DONE)

    def test_an_unfinished_run_is_not_emailed(self):
        run = ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_RUNNING)
        self.assertFalse(notify_if_slow(run))
        self.assertEqual(len(mail.outbox), 0)

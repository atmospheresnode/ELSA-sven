"""The storage dialog in a real browser, end to end.

Reuses the AMA browser fixture (a bundle with two collections, signed in through a session cookie)
and skips the same way when Playwright is not installed. The live server runs in this process, so
patching shutil.disk_usage here is what the server sees, and the locmem email outbox collects what
it sends. Failures the server cannot easily be made to produce (a check that errors, a draft that
hangs) are staged by intercepting the page's own requests with page.route.

Every test also fails on any uncaught script error on the page.

Run with:
    python manage.py test build.test_storage_browser --settings=test_settings
"""
from __future__ import unicode_literals

import json
from smtplib import SMTPException
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.test import override_settings
from django.urls import reverse

from build import storage_report, test_ama_browser
from build.models import NetCDFFile
from build.test_storage_report import GB, free_space, gemini_reply

NC_HEADER = b'CDF\x01' + b'\0' * 64


def _real_netcdf():
    """A small valid NetCDF file, from the audit's helper, for the upload that goes through."""
    from build.test_ama_audit import real_netcdf_bytes
    return real_netcdf_bytes()


@override_settings(GEMINI_API_KEY='')
class StorageDialogBrowserTests(test_ama_browser.AMABrowserTestCase):

    def setUp(self):
        super().setUp()
        self.script_errors = []
        self.page.on('pageerror', lambda exc: self.script_errors.append(str(exc)))
        self.addCleanup(lambda: self.assertEqual(self.script_errors, [], 'script errors on the page'))
        self.user.first_name, self.user.last_name = 'Alex', 'Rivera'
        self.user.save()

    # ---- helpers ------------------------------------------------------------------------------

    def url(self, name):
        return reverse('build:' + name, kwargs={'pk_bundle': self.bundle.pk})

    def dialog(self):
        return self.page.locator('#netcdfStorageModal')

    def pick(self, files, collection=None):
        """Put files in a collection's upload well. files: [(name, bytes)]."""
        pane = self.pane(collection or self.alpha)
        pane.locator('input[name="netcdf_files"]').set_input_files(
            [{'name': n, 'mimeType': 'application/x-netcdf', 'buffer': b} for n, b in files])
        return pane

    def upload(self, files=(('big.nc', NC_HEADER),), collection=None):
        self.pick(files, collection).locator('#uploadBtn').click()

    def wait_for_draft(self):
        self.dialog().wait_for(state='visible', timeout=10000)
        self.page.locator('#netcdfStorageModal .ess-drafting').wait_for(state='hidden', timeout=10000)

    def wait_closed(self):
        self.dialog().wait_for(state='hidden', timeout=10000)
        self.page.wait_for_function('!document.querySelector(".modal-backdrop")', timeout=10000)

    def requests_to(self, url, method='POST'):
        seen = []
        self.page.on('request', lambda r: seen.append(r) if (
            r.method == method and r.url.split('?')[0].endswith(url)) else None)
        return seen

    def send(self):
        self.dialog().locator('.ess-send').click()

    def wait_sent(self):
        self.dialog().locator('.ess-sent').wait_for(state='visible', timeout=10000)

    def error_text(self):
        box = self.dialog().locator('.ess-error')
        box.wait_for(state='visible', timeout=10000)
        return box.inner_text()

    # ---- the main path ------------------------------------------------------------------------

    def test_a_full_disk_stops_the_upload_before_it_is_sent_and_the_report_goes_out(self):
        uploads = self.requests_to(self.url('bundle'))
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            dialog = self.dialog()

            self.assertIn('stopped before anything was sent', dialog.locator('.ess-lede').inner_text())
            self.assertEqual(dialog.locator('.ess-needed').inner_text(), '68 bytes')
            self.assertEqual(dialog.locator('.ess-count').inner_text(), '1 file')
            self.assertEqual(dialog.locator('.ess-available').inner_text(), '0 bytes')
            self.assertIn('no room for new files', dialog.locator('.ess-tip').inner_text())
            self.assertIn('Prefilled for you', dialog.locator('.ess-source').inner_text())
            self.assertEqual(dialog.locator('.ess-replyto').inner_text(), 'Replies go to b@example.com')

            message = dialog.locator('#essMessage').input_value()
            for expected in ('Hello Team ELSA,', 'browser_bundle', 'External', '"alpha"',
                             'big.nc (68 bytes)', 'Alex Rivera (ama_browser)'):
                self.assertIn(expected, message)

            # The draft opens at the greeting, with the caret there, ready to edit.
            # Focus follows the fade-in, which can finish after a template draft has arrived.
            self.page.wait_for_function('document.activeElement.id === "essMessage"', timeout=5000)
            self.assertEqual(self.page.evaluate('document.getElementById("essMessage").selectionStart'), 0)

            dialog.locator('#essMessage').fill(message + '\nThis is urgent for my thesis.')
            self.send()
            self.wait_sent()

        self.assertIn('will reply to b@example.com', dialog.locator('.ess-sent-text').inner_text())
        self.assertEqual(self.page.evaluate('document.activeElement.textContent.trim()'), 'Done')
        self.assertFalse(dialog.locator('.ess-send').is_visible())
        self.assertEqual(dialog.locator('.ess-close').inner_text().strip(), 'Done')
        self.assertEqual(uploads, [], 'the upload was sent despite the refusal')
        self.assertFalse(NetCDFFile.objects.filter(title='big.nc').exists())

        self.assertEqual(len(mail.outbox), 2)
        staff, confirmation = mail.outbox
        self.assertEqual(staff.from_email, 'atm-elsa@nmsu.edu')
        self.assertEqual(staff.to, ['lneakras@nmsu.edu', 'rupakdey@nmsu.edu'])
        self.assertEqual(staff.reply_to, ['b@example.com'])
        self.assertEqual(staff.subject, '[ELSA Storage] Upload refused for ama_browser (68 bytes)')
        self.assertEqual(staff.content_subtype, 'html')
        self.assertIn('urgent for my thesis', staff.body)
        self.assertIn('alpha', staff.body)
        self.assertIn('big.nc (68 bytes)', staff.body)
        self.assertEqual(confirmation.to, ['b@example.com'])
        self.assertEqual(confirmation.from_email, 'atm-elsa@nmsu.edu')

        # Closing hands the page back with the upload button usable for a later try.
        dialog.locator('.ess-close').click()
        self.wait_closed()
        button = self.pane(self.alpha).locator('#uploadBtn')
        self.assertTrue(button.is_enabled())
        self.assertEqual(button.inner_text().strip(), 'Upload')

    def test_an_upload_that_fits_goes_ahead_and_the_file_arrives(self):
        payload = _real_netcdf()
        with free_space(100 * GB):
            self.open_bundle(self.alpha)
            with self.page.expect_response(
                    lambda r: r.request.method == 'POST' and r.url.endswith(self.url('bundle')),
                    timeout=30000) as response:
                self.upload((('fresh.nc', payload),))
            self.assertEqual(response.value.status, 200)
        self.assertTrue(NetCDFFile.objects.filter(title='fresh.nc', collection=self.alpha).exists())
        self.assertFalse(self.dialog().is_visible())
        self.assertEqual(len(mail.outbox), 0)

    def test_a_gemini_draft_is_labelled_as_drafted_by_the_assistant(self):
        text = ('Hello Team ELSA,\n\nMy upload of "big.nc" to "browser_bundle", collection "alpha", '
                'was refused for lack of space.\n\nThank you,\nAlex Rivera (ama_browser)')
        with override_settings(GEMINI_API_KEY='key'), free_space(1 * GB), \
                mock.patch('build.storage_report.requests.post', return_value=gemini_reply(text)):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
        self.assertEqual(self.dialog().locator('#essMessage').input_value(), text)
        self.assertIn('Drafted by ELSA Assistant', self.dialog().locator('.ess-source').inner_text())

    def test_the_report_names_the_collection_it_was_uploaded_into(self):
        with free_space(1 * GB):
            self.open_bundle(self.beta)
            self.upload(collection=self.beta)
            self.wait_for_draft()
            self.assertIn('"beta"', self.dialog().locator('#essMessage').input_value())
            self.send()
            self.wait_sent()
        self.assertIn('beta', mail.outbox[0].body)
        self.assertNotIn('>alpha<', mail.outbox[0].body)

    def test_several_files_suggest_a_smaller_batch_and_a_long_list_is_summarised(self):
        files = [('run_{:02d}.nc'.format(i), NC_HEADER) for i in range(25)]
        # 100 bytes of usable room: less than the 25 files together, more than any one of them.
        with free_space(storage_report.STORAGE_MARGIN + 100):
            self.open_bundle(self.alpha)
            self.upload(files)
            self.wait_for_draft()
            dialog = self.dialog()
            self.assertEqual(dialog.locator('.ess-count').inner_text(), '25 files')
            self.assertIn('Uploading fewer files at a time may work: anything up to 100 bytes',
                          dialog.locator('.ess-tip').inner_text())
            self.assertIn('and 5 more', dialog.locator('#essMessage').input_value())
            self.send()
            self.wait_sent()
        body = mail.outbox[0].body
        self.assertIn('run_19.nc', body)
        self.assertNotIn('run_20.nc', body)
        self.assertIn('and 5 more', body)
        self.assertIn('1.7 KB across 25 file(s)', body)

    # ---- the backstop and the fail-open preflight ---------------------------------------------

    def test_when_the_disk_fills_after_the_preflight_the_view_refuses_and_the_same_dialog_opens(self):
        # The preflight is told there is room; the view, checking again, finds none.
        self.page.route('**' + self.url('netcdf_storage_check'), lambda route: route.fulfill(
            status=200, content_type='application/json',
            body=json.dumps({'ok': True, 'needed': '68 bytes', 'available': '9 GB',
                             'available_bytes': 9 * GB})))
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
        self.assertIn('reached the server but could not be stored',
                      self.dialog().locator('.ess-lede').inner_text())
        self.assertFalse(NetCDFFile.objects.filter(title='big.nc').exists())
        self.assertEqual(self.page.locator('.netcdf-upload-error').count(), 0)

    def test_a_preflight_that_errors_does_not_block_the_upload(self):
        self.page.route('**' + self.url('netcdf_storage_check'),
                        lambda route: route.fulfill(status=500, body='boom'))
        with free_space(100 * GB):
            self.open_bundle(self.alpha)
            # The response, not just the request: a test that ends while the upload is still
            # being handled tears down its temporary MEDIA_ROOT under the server thread, and
            # the file then lands in the real uploads/ folder, two per run.
            with self.page.expect_response(
                    lambda r: r.request.method == 'POST' and r.url.endswith(self.url('bundle')),
                    timeout=30000):
                self.upload()
        self.assertFalse(self.dialog().is_visible())

    def test_a_preflight_that_cannot_connect_does_not_block_the_upload(self):
        self.page.route('**' + self.url('netcdf_storage_check'), lambda route: route.abort())
        with free_space(100 * GB):
            self.open_bundle(self.alpha)
            # The response, not just the request: a test that ends while the upload is still
            # being handled tears down its temporary MEDIA_ROOT under the server thread, and
            # the file then lands in the real uploads/ folder, two per run.
            with self.page.expect_response(
                    lambda r: r.request.method == 'POST' and r.url.endswith(self.url('bundle')),
                    timeout=30000):
                self.upload()
        self.assertFalse(self.dialog().is_visible())

    # ---- things going wrong inside the dialog -------------------------------------------------

    def test_a_draft_that_fails_leaves_an_empty_box_the_user_can_still_send(self):
        self.page.route('**' + self.url('netcdf_storage_draft'),
                        lambda route: route.fulfill(status=500, body='boom'))
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            dialog = self.dialog()
            self.assertEqual(dialog.locator('#essMessage').input_value(), '')
            self.assertIn('could not prepare a draft', dialog.locator('.ess-source').inner_text())
            dialog.locator('#essMessage').fill('My upload failed, please help.')
            self.send()
            self.wait_sent()
        self.assertIn('My upload failed, please help.', mail.outbox[0].body)

    def test_an_email_failure_is_reported_and_sending_again_works(self):
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            with mock.patch.object(EmailMessage, 'send', side_effect=SMTPException('down')):
                self.send()
                self.assertIn('atm-elsa@nmsu.edu', self.error_text())
            dialog = self.dialog()
            self.assertTrue(dialog.locator('.ess-send').is_enabled())
            self.assertTrue(dialog.locator('.ess-send .spinner-border').is_hidden())
            self.assertTrue(dialog.locator('#essMessage').is_editable())
            self.send()
            self.wait_sent()
        self.assertEqual(len(mail.outbox), 2)

    def test_the_hourly_cap_is_explained_in_the_dialog(self):
        cache.set('storage-report-send-{}'.format(self.user.pk), storage_report.SEND_LIMIT_PER_HOUR, 3600)
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            self.send()
            self.assertIn('already sent a few reports this hour', self.error_text())
            # Focus is back inside the dialog, so the keyboard still works.
            self.assertEqual(self.page.evaluate('document.activeElement.id'), 'essMessage')
            self.page.keyboard.press('Escape')
            self.wait_closed()
        self.assertEqual(len(mail.outbox), 0)

    def test_an_empty_message_is_caught_before_anything_is_sent(self):
        reports = self.requests_to(self.url('netcdf_storage_report'))
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            self.dialog().locator('#essMessage').fill('   ')
            self.send()
            self.assertIn('write a short message', self.error_text())
        self.assertEqual(reports, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_an_expired_session_is_never_reported_as_sent(self):
        from django.conf import settings as django_settings
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            # Sign out behind the page's back, keeping the CSRF cookie, which is what an expired
            # session looks like: the POST passes CSRF and login_required redirects to the login page.
            kept = [c for c in self.context.cookies()
                    if c['name'] != django_settings.SESSION_COOKIE_NAME]
            self.context.clear_cookies()
            self.context.add_cookies(kept)
            self.send()
            self.assertIn('sign in', self.error_text())
        self.assertFalse(self.dialog().locator('.ess-sent').is_visible())
        self.assertEqual(len(mail.outbox), 0)

    # ---- hostile input and races --------------------------------------------------------------

    def test_a_file_name_carrying_markup_stays_text_everywhere(self):
        name = '<img src=x onerror="window.__xss=1">.nc'
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload(((name, NC_HEADER),))
            self.wait_for_draft()
            self.assertIn(name, self.dialog().locator('#essMessage').input_value())
            self.send()
            self.wait_sent()
        self.assertIsNone(self.page.evaluate('window.__xss'))
        body = mail.outbox[0].body
        self.assertNotIn('<img src=x', body)
        self.assertIn('&lt;img src=x', body)

    def test_a_late_draft_from_an_earlier_opening_does_not_overwrite_the_current_one(self):
        held = []

        def draft(route):
            if not held:
                held.append(route)   # the first draft never answers until the test says so
            else:
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'message': 'CURRENT DRAFT', 'source': 'template'}))

        self.page.route('**' + self.url('netcdf_storage_draft'), draft)
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.dialog().wait_for(state='visible', timeout=10000)
            # Bootstrap listens for Escape on the dialog, which has focus once it has faded in.
            self.page.wait_for_function(
                'document.activeElement && !!document.activeElement.closest("#netcdfStorageModal")')
            self.page.keyboard.press('Escape')
            self.wait_closed()

            self.upload((('second.nc', NC_HEADER),))
            self.wait_for_draft()
            self.assertEqual(self.dialog().locator('#essMessage').input_value(), 'CURRENT DRAFT')

            held[0].fulfill(status=200, content_type='application/json',
                            body=json.dumps({'message': 'STALE DRAFT', 'source': 'ai'}))
            self.page.wait_for_timeout(500)
        self.assertEqual(self.dialog().locator('#essMessage').input_value(), 'CURRENT DRAFT')
        self.assertIn('Prefilled', self.dialog().locator('.ess-source').inner_text())

    def test_reopening_after_a_send_starts_a_fresh_report(self):
        with free_space(1 * GB):
            self.open_bundle(self.alpha)
            self.upload()
            self.wait_for_draft()
            self.send()
            self.wait_sent()
            self.dialog().locator('.ess-close').click()
            self.wait_closed()

            self.upload((('again.nc', NC_HEADER),))
            self.wait_for_draft()
        dialog = self.dialog()
        self.assertTrue(dialog.locator('.ess-form').is_visible())
        self.assertFalse(dialog.locator('.ess-sent').is_visible())
        self.assertTrue(dialog.locator('.ess-send').is_visible())
        self.assertEqual(dialog.locator('.ess-close').inner_text().strip(), 'Not now')
        self.assertIn('again.nc', dialog.locator('#essMessage').input_value())
        self.assertFalse(dialog.locator('.ess-error').is_visible())


# The AMA editor's own tests come along with its fixture, and already run in their own module.
# Blanking them here keeps the loader from collecting them a second time under this one.
for _name in dir(test_ama_browser.AMABrowserTestCase):
    if _name.startswith('test_'):
        setattr(StorageDialogBrowserTests, _name, None)

# -*- coding: utf-8 -*-
"""Django messages render as toasts, not as a banner at the top of the document.

The AMA save is what prompted this. Saving Model Metadata posts from inside a modal,
and the handler deliberately ends in window.location.reload() because the file tree's
readings and the Collections badges go stale once the labels are rewritten. The
confirmation therefore arrived after a full page load, as a full-width dismissible
alert inserted at the very top of the body: a long way from the dialog the user had
been working in, shifting the whole page down as it appeared, and staying there until
clicked.

What is asserted here is behaviour rather than styling. A success takes itself away;
a warning does not, because a warning from this code path means some labels could not
be refreshed, and a notice about something that did not happen is the exact case where
auto-dismissal leaves a user believing it did.
"""
from __future__ import unicode_literals

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from build.models import (AdditionalCollections, Bundle, Investigation,
                          NetCDFFile)

TEMPLATE = 'includes/_toasts.html'


def render(*messages):
    return render_to_string(TEMPLATE, {'messages': list(messages)})


class ToastComponentTests(SimpleTestCase):
    """The component on its own, one message at a time."""

    def test_the_message_is_shown(self):
        body = render(Message(constants.SUCCESS,
                              'Saved. Model Metadata now applies to every file in new.'))
        self.assertIn('Saved. Model Metadata now applies to every file in new.', body)

    def test_it_is_a_toast_and_not_a_banner(self):
        body = render(Message(constants.SUCCESS, 'Saved.'))
        self.assertIn('id="elsaToasts"', body)
        self.assertIn('class="toast elsa-toast', body)
        self.assertNotIn('alert-dismissible', body.split('<noscript>')[0])

    def test_nothing_is_rendered_when_there_is_nothing_to_say(self):
        self.assertEqual(render_to_string(TEMPLATE, {'messages': []}).strip(), '')

    # -- severity decides whether it leaves on its own ---------------------------

    def test_a_success_takes_itself_away(self):
        self.assertIn('data-autohide="true"', render(Message(constants.SUCCESS, 'Saved.')))

    def test_a_warning_stays_until_dismissed(self):
        # The real one: 'Saved, but some labels could not be refreshed: ...'
        body = render(Message(constants.WARNING,
                              'Saved, but some labels could not be refreshed: x'))
        self.assertIn('data-autohide="false"', body)
        self.assertNotIn('data-autohide="true"', body)

    def test_an_error_stays_until_dismissed(self):
        self.assertIn('data-autohide="false"',
                      render(Message(constants.ERROR, 'That did not work.')))

    def test_each_severity_gets_its_own_icon(self):
        pairs = [
            (constants.SUCCESS, 'bi-check-circle-fill'),
            (constants.WARNING, 'bi-exclamation-triangle-fill'),
            (constants.ERROR, 'bi-x-circle-fill'),
            (constants.INFO, 'bi-info-circle-fill'),
        ]
        for level, icon in pairs:
            self.assertIn(icon, render(Message(level, 'x')), icon)

    # -- readable and reachable ---------------------------------------------------

    def test_a_success_is_announced_politely_and_an_error_assertively(self):
        self.assertIn('aria-live="polite"', render(Message(constants.SUCCESS, 'Saved.')))
        self.assertIn('aria-live="assertive"', render(Message(constants.ERROR, 'Nope.')))

    def test_the_message_survives_without_javascript(self):
        # A .toast is display:none until a script shows it, so a page whose script did
        # not run would otherwise drop the notice entirely.
        body = render(Message(constants.SUCCESS, 'Saved.'))
        self.assertIn('<noscript>', body)
        noscript = body.split('<noscript>')[1].split('</noscript>')[0]
        self.assertIn('Saved.', noscript)

    def test_it_sits_above_a_modal_rather_than_behind_one(self):
        self.assertIn('z-index: 1090', render(Message(constants.SUCCESS, 'Saved.')))

    def test_html_in_a_message_is_escaped(self):
        body = render(Message(constants.SUCCESS, 'Saved <script>alert(1)</script>'))
        self.assertNotIn('<script>alert(1)</script>', body)
        self.assertIn('&lt;script&gt;', body)

    def test_several_messages_stack_rather_than_overwrite(self):
        body = render(Message(constants.SUCCESS, 'First thing.'),
                      Message(constants.WARNING, 'Second thing.'))
        self.assertIn('First thing.', body)
        self.assertIn('Second thing.', body)
        self.assertEqual(body.count('class="toast elsa-toast'), 2)


class AmaSaveShowsAToastTests(TestCase):
    """The real path: save AMA metadata, then load the page the save reloads into.

    The bundle is built through the real build view rather than as a bare row,
    because the bundle page redirects away from a Bundle with no Product_Bundle
    behind it, and the bundle page is exactly where the AMA save lands.
    """

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-toast-ama-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-toast-ama-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('toaster', password='pw')
        self.client.login(username='toaster', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        response = self.client.post(reverse('build:build'), {
            'name': 'toast bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.assertIn(response.status_code, (200, 302))
        self.bundle = Bundle.objects.get(name='toast bundle')

        self.collection = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name='alpha', collection_type='External')
        os.makedirs(self.collection.directory(), exist_ok=True)
        self.netcdf = NetCDFFile.objects.create(
            title='00000.atmos_average.nc', file='00000.atmos_average.nc',
            bundle=self.bundle, collection=self.collection, processed=True)

    def ama_url(self):
        return reverse('build:netcdf_ama', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': self.netcdf.pk})

    def save(self, **extra):
        payload = {'model-name': 'MarsWRF'}
        payload.update(extra)
        return self.client.post(self.ama_url(), payload,
                                HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def bundle_page(self):
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_save_message_arrives_as_a_toast_on_the_bundle_page(self):
        self.save(**{'model-apply_scope': 'collection'})

        # The save handler closes the dialog and reloads the page; this is that reload.
        body = self.bundle_page()

        message = 'Model Metadata now applies to every file in alpha'
        self.assertIn('id="elsaToasts"', body)
        self.assertIn(message, body.split('id="elsaToasts"')[1])
        # The banner this replaced. Not a blanket ban on dismissible alerts: the
        # NetCDF upload builds its own inline ones beside the upload control, and
        # those are in the right place already.
        self.assertNotIn('alert alert-success alert-dismissible', body)

    def test_a_page_with_nothing_to_say_renders_no_toast_markup(self):
        self.assertNotIn('id="elsaToasts"', self.bundle_page())

    def test_the_notice_is_shown_once_and_not_again_on_the_next_load(self):
        # Messages are consumed on render. A toast that reappeared on every reload
        # would be worse than the banner it replaced.
        self.save()
        self.assertIn('id="elsaToasts"', self.bundle_page())
        self.assertNotIn('id="elsaToasts"', self.bundle_page())

    def test_the_standalone_ama_page_uses_the_same_component(self):
        self.client.post(self.ama_url(), {'model-name': 'MarsWRF'})
        body = self.client.get(self.ama_url()).content.decode()
        self.assertIn('id="elsaToasts"', body)
        self.assertNotIn('alert alert-success alert-dismissible', body)

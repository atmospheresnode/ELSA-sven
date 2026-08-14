# -*- coding: utf-8 -*-
"""Real browser tests for the AMA inline editor, driven with Playwright.

These cover the parts no server-side test can reach: whether the panel actually loads on click,
whether the unsaved-changes guard fires, and whether Select All stays inside one collection.

Playwright is a development-only dependency and is deliberately not in requirements.txt: nothing
in the application imports it. If it is not installed, or the browser cannot start, every test
here skips rather than failing the suite.

Run with:
    python manage.py test build.test_ama_browser --settings=test_settings
"""

from __future__ import unicode_literals

import os

# Playwright's synchronous API drives a greenlet-based event loop, which Django's ORM mistakes for
# an async context and refuses to run queries in. This flag is the documented escape hatch and is
# set here, inside a test-only module, so it never applies to the running site.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', '1')

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from build import views
from build.models import (AdditionalCollections, Bundle, ModelMetadata, NetCDFFile,
                          Product_Bundle, SimulationConfiguration)
from build.tests import MINIMAL_LDD

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - dev-only dependency
    sync_playwright = None


@override_settings(ALLOWED_HOSTS=['*'])
class AMABrowserTestCase(StaticLiveServerTestCase):
    """A bundle with two collections and three files, driven through a real browser."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = None
        cls.browser = None
        if sync_playwright is None:
            return
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment dependent
            cls.browser = None
            cls.launch_error = exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser is not None:
            cls.browser.close()
        if cls.playwright is not None:
            cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        if self.browser is None:
            self.skipTest('Playwright browser unavailable: {}'.format(
                getattr(self, 'launch_error', 'playwright not installed')))

        import shutil
        import tempfile
        from django.contrib.auth.models import User

        self.archive_dir = tempfile.mkdtemp(prefix='elsa-ama-browser-')
        self.addCleanup(shutil.rmtree, self.archive_dir, True)
        self.media_root = tempfile.mkdtemp(prefix='elsa-ama-browser-media-')
        self.addCleanup(shutil.rmtree, self.media_root, True)
        self.settings_patcher = override_settings(
            ARCHIVE_DIR=self.archive_dir, MEDIA_ROOT=self.media_root)
        self.settings_patcher.enable()
        self.addCleanup(self.settings_patcher.disable)

        # Label regeneration would reach out to PDS on every save; prime the cache instead.
        cache.clear()
        cache.set('ldd_schema:' + views.AMA_LDD_URL, MINIMAL_LDD.encode('utf-8'), 300)
        self.addCleanup(cache.clear)

        self.user = User.objects.create_user(
            username='ama_browser', password='pw-for-tests', email='b@example.com')
        self.bundle = Bundle.objects.create(
            name='browser_bundle', user=self.user, version='1800', bundle_type='External')
        os.makedirs(self.bundle.directory(), exist_ok=True)
        Product_Bundle.objects.create(bundle=self.bundle)

        self.alpha = self.make_collection('alpha')
        self.beta = self.make_collection('beta')
        self.nc_one = self.make_netcdf('00000.atmos_average.nc', self.alpha)
        self.nc_two = self.make_netcdf('00001.atmos_average.nc', self.alpha)
        self.nc_beta = self.make_netcdf('00002.atmos_average.nc', self.beta)

        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.login()

    def make_collection(self, name):
        collection = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name=name, collection_type='External')
        os.makedirs(collection.directory(), exist_ok=True)
        return collection

    def make_netcdf(self, title, collection):
        return NetCDFFile.objects.create(
            title=title, file=title, bundle=self.bundle, collection=collection, processed=True)

    def login(self):
        """Sign in by installing the session cookie directly; the login flow is not under test."""
        from django.conf import settings as django_settings
        from django.contrib.auth import login
        from django.contrib.sessions.backends.db import SessionStore
        from django.http import HttpRequest

        request = HttpRequest()
        request.session = SessionStore()
        self.user.backend = 'django.contrib.auth.backends.ModelBackend'
        login(request, self.user)
        request.session.save()

        self.page.goto(self.live_server_url + '/static/css/styles.css')
        self.context.add_cookies([{
            'name': django_settings.SESSION_COOKIE_NAME,
            'value': request.session.session_key,
            'url': self.live_server_url,
        }])

    def open_bundle(self, collection=None):
        """Load the bundle page and open a collection's tab.

        Each collection's NetCDF card lives inside its own Bootstrap tab pane, and only the
        document collection is active on load, so nothing inside a collection is clickable until
        its tab is selected.
        """
        self.page.goto(
            self.live_server_url + reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}))
        self.page.wait_for_load_state('domcontentloaded')
        self.open_collection_tab(collection or self.alpha)

    def open_collection_tab(self, collection):
        selector = 'button[data-bs-target="#additional_collection_{}"]'.format(collection.pk)
        tab = self.page.locator(selector).locator('visible=true').first
        tab.click()
        self.pane(collection).wait_for(state='visible', timeout=10000)

    def pane(self, collection):
        return self.page.locator('#additional_collection_{}'.format(collection.pk))

    def ama_button(self, netcdf_file):
        return self.pane(netcdf_file.collection).locator(
            '.ama-select[data-ama-key="file-{}"]'.format(netcdf_file.pk))

    def defaults_button(self, collection):
        return self.pane(collection).locator(
            '.ama-select[data-ama-key="defaults-{}"]'.format(collection.pk))

    def panel(self, collection=None):
        collection = collection or self.alpha
        return self.pane(collection).locator('#amaPanelInner')

    # -- panel loading -------------------------------------------------------------------------

    def test_clicking_a_file_loads_its_panel(self):
        self.open_bundle()
        self.ama_button(self.nc_one).click()

        panel = self.panel()
        panel.wait_for(state='visible', timeout=10000)

        self.assertEqual(panel.get_attribute('data-scope'), 'file')
        self.assertEqual(panel.get_attribute('data-netcdf-id'), str(self.nc_one.pk))
        self.assertIn(self.nc_one.title, panel.inner_text())

    def test_clicking_collection_defaults_loads_the_default_scope(self):
        self.open_bundle()
        self.defaults_button(self.alpha).click()

        panel = self.panel()
        panel.wait_for(state='visible', timeout=10000)

        self.assertEqual(panel.get_attribute('data-scope'), 'default')
        self.assertIn('Collection Defaults', panel.inner_text())

    def test_all_three_sections_are_present_and_expandable(self):
        self.open_bundle()
        self.ama_button(self.nc_one).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.assertEqual(self.pane(self.alpha).locator('.accordion-item').count(), 3)
        # The first section is open, the others collapsed until clicked.
        self.assertTrue(self.pane(self.alpha).locator('#amaSectionmodel').is_visible())
        self.assertFalse(self.pane(self.alpha).locator('#amaSectionsim').is_visible())

        self.pane(self.alpha).locator('button[data-bs-target="#amaSectionsim"]').click()
        self.pane(self.alpha).locator('#amaSectionsim').wait_for(state='visible', timeout=5000)
        self.assertTrue(self.pane(self.alpha).locator('#amaSectionsim').is_visible())

    # -- the guard that only a browser can exercise ---------------------------------------------

    def test_switching_files_with_unsaved_edits_warns_first(self):
        self.open_bundle()
        self.ama_button(self.nc_one).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.pane(self.alpha).locator('#id_model-name').fill('MarsWRF')
        self.ama_button(self.nc_two).click()

        modal = self.page.locator('#amaUnsavedModal')
        modal.wait_for(state='visible', timeout=5000)
        self.assertIn('Discard unsaved changes', modal.inner_text())

        # Keeping the edit leaves the original file's panel in place.
        self.page.locator('#amaUnsavedModal button[data-bs-dismiss="modal"]').last.click()
        modal.wait_for(state='hidden', timeout=5000)
        self.assertEqual(
            self.panel().get_attribute('data-netcdf-id'),
            str(self.nc_one.pk))

    def test_discarding_unsaved_edits_switches_to_the_other_file(self):
        self.open_bundle()
        self.ama_button(self.nc_one).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.pane(self.alpha).locator('#id_model-name').fill('MarsWRF')
        self.ama_button(self.nc_two).click()
        self.page.locator('#amaUnsavedModal').wait_for(state='visible', timeout=5000)
        self.page.click('#amaDiscardConfirm')

        self.page.wait_for_function(
            'document.querySelector("#additional_collection_{} #amaPanelInner") && '
            'document.querySelector("#additional_collection_{} #amaPanelInner")'
            '.dataset.netcdfId === "{}"'.format(
                self.alpha.pk, self.alpha.pk, self.nc_two.pk), timeout=10000)
        self.assertEqual(ModelMetadata.objects.count(), 0, 'a discarded edit was still saved')

    def test_switching_without_edits_does_not_warn(self):
        self.open_bundle()
        self.ama_button(self.nc_one).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.ama_button(self.nc_two).click()
        self.page.wait_for_function(
            'document.querySelector("#additional_collection_{} #amaPanelInner") && '
            'document.querySelector("#additional_collection_{} #amaPanelInner")'
            '.dataset.netcdfId === "{}"'.format(
                self.alpha.pk, self.alpha.pk, self.nc_two.pk), timeout=10000)
        self.assertFalse(self.page.locator('#amaUnsavedModal').is_visible())

    # -- saving --------------------------------------------------------------------------------

    def test_saving_a_file_panel_stores_the_values_and_refreshes_the_page(self):
        self.open_bundle()
        self.ama_button(self.nc_one).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.pane(self.alpha).locator('#id_model-name').fill('MarsWRF')
        self.pane(self.alpha).locator('#amaPanelForm button[type="submit"]').click()

        # The save reloads the page. innerHTML rather than innerText because the badge lives in a
        # tab pane, and innerText would miss it while that pane is hidden.
        self.page.wait_for_function(
            "document.body.innerHTML.includes('Custom AMA')", timeout=20000)

        override = ModelMetadata.objects.get(netcdf_file=self.nc_one)
        self.assertEqual(override.name, 'MarsWRF')

    def test_saving_keeps_the_user_on_the_collection_they_were_editing(self):
        """The reload after a save used to drop the user back on the default tab."""
        self.open_bundle(self.beta)
        self.ama_button(self.nc_beta).click()
        self.panel(self.beta).wait_for(state='visible', timeout=10000)

        self.pane(self.beta).locator('#id_model-name').fill('LMD')
        self.pane(self.beta).locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            "document.body.innerHTML.includes('Custom AMA')", timeout=20000)
        self.page.wait_for_selector(
            '#additional_collection_{}.active'.format(self.beta.pk), timeout=10000)

        self.assertTrue(self.pane(self.beta).is_visible(),
                        'the save reload lost the collection the user was working in')

    def test_a_rejected_value_is_reported_in_the_panel_without_saving(self):
        self.open_bundle()
        self.defaults_button(self.alpha).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.pane(self.alpha).locator('button[data-bs-target="#amaSectionsim"]').click()
        self.pane(self.alpha).locator('#amaSectionsim').wait_for(state='visible', timeout=5000)
        self.pane(self.alpha).locator('#id_sim-northern_boundary').fill('120')
        self.pane(self.alpha).locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            'document.querySelector("#additional_collection_{} #amaPanelInner") && '
            'document.querySelector("#additional_collection_{} #amaPanelInner")'
            '.dataset.saved === "false"'.format(self.alpha.pk, self.alpha.pk), timeout=10000)
        self.assertEqual(SimulationConfiguration.objects.count(), 0)

    def test_a_non_ascii_value_is_refused_with_an_explanation(self):
        self.open_bundle()
        self.defaults_button(self.alpha).click()
        self.panel().wait_for(state='visible', timeout=10000)

        self.pane(self.alpha).locator('#id_model-institution').fill('Université de Paris')
        self.pane(self.alpha).locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            "document.body.innerText.includes('basic Latin')", timeout=10000)
        self.assertEqual(ModelMetadata.objects.count(), 0)

    # -- the data-loss regression ---------------------------------------------------------------

    def test_select_all_stays_inside_its_own_collection(self):
        """The bug: a document-wide selector ticked every collection's files, and the delete that
        followed removed files the user could not see from that tab."""
        self.open_bundle(self.alpha)

        select_all = self.pane(self.alpha).locator('#selectAllWrapper')
        select_all.evaluate('el => el.style.display = "inline-block"')
        select_all.click()

        self.assertTrue(
            self.pane(self.alpha).locator(
                'input.netcdf-checkbox[value="{}"]'.format(self.nc_one.pk)).is_checked(),
            'Select All did not tick its own collection')
        self.assertFalse(
            self.pane(self.beta).locator(
                'input.netcdf-checkbox[value="{}"]'.format(self.nc_beta.pk)).is_checked(),
            'Select All ticked a file belonging to a different collection')

    def test_the_delete_modal_only_collects_its_own_collections_files(self):
        self.open_bundle(self.alpha)

        # Tick a file in each collection, then open the delete modal from the alpha card.
        self.pane(self.alpha).locator(
            'input.netcdf-checkbox[value="{}"]'.format(self.nc_one.pk)).check()
        self.pane(self.beta).locator(
            'input.netcdf-checkbox[value="{}"]'.format(self.nc_beta.pk)).evaluate(
                'el => el.checked = true')

        self.pane(self.alpha).locator('#bulkDeleteBtn').click()
        self.page.locator('#bulkDeleteNetCDFModal').wait_for(state='visible', timeout=10000)

        values = self.page.eval_on_selector_all(
            '#ncDeleteHiddenIds input', 'nodes => nodes.map(n => n.value)')

        self.assertIn(str(self.nc_one.pk), values)
        self.assertNotIn(str(self.nc_beta.pk), values,
                         'the delete modal collected a file from another collection')

    # -- upload cancel --------------------------------------------------------------------------

    def test_the_upload_cancel_button_is_hidden_until_an_upload_starts(self):
        self.open_bundle()
        self.assertFalse(self.pane(self.alpha).locator('#uploadCancelBtn').is_visible())

    def test_each_collection_tab_can_be_opened(self):
        """Collection tab targets used to embed the collection name, which broke on odd names."""
        self.open_bundle()
        for collection in (self.alpha, self.beta):
            with self.subTest(collection=collection.collection_name):
                tab = self.page.locator(
                    'button[data-bs-target="#additional_collection_{}"]'.format(collection.pk))
                self.assertTrue(tab.count() >= 1)

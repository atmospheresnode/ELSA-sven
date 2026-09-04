# -*- coding: utf-8 -*-
"""Real browser tests for the AMA metadata editor, driven with Playwright.

These cover the parts no server-side test can reach: whether the editor actually opens from a
label, whether the unsaved-changes guard fires on close, and whether Select All stays inside one
collection.

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
from build.tests import NS, MINIMAL_LDD

# Taken from views rather than imported directly, the way test_ama_audit does: views binds ET
# twice and the tests have to read labels with whatever it ended up with.
ET = views.ET

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
        for netcdf_file in (self.nc_one, self.nc_two, self.nc_beta):
            self.write_label(netcdf_file)
        # A structural label too, so the tree holds something with no AMA behind it.
        self.collection_label = 'collection_1_alpha.xml'
        with open(os.path.join(self.alpha.directory(), self.collection_label), 'w') as handle:
            handle.write('<?xml version="1.0"?><Product_Collection/>')

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

    def write_label(self, netcdf_file):
        """Put a stub label beside the data file so the Files card has a row for it."""
        stem = os.path.splitext(os.path.basename(netcdf_file.file.name))[0]
        with open(os.path.join(netcdf_file.directory(), stem + '.xml'), 'w') as handle:
            handle.write('<?xml version="1.0"?><Product_Observational/>')

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

    def tree_row(self, netcdf_file):
        """The label's row in the Files card, which is what metadata is opened from now."""
        return self.page.locator(
            '.eft-item[data-netcdf-id="{}"]'.format(netcdf_file.pk))

    def panel(self):
        """The one editor, wherever it was opened from."""
        return self.page.locator('#amaMetadataPanel #amaPanelInner')

    def close_editor(self):
        """Shut the editor and wait for it to be gone.

        Its backdrop covers the file tree, so nothing else on the page is clickable until it is.
        """
        self.page.locator('#amaMetadataModal .btn-close').click()
        self.page.locator('#amaMetadataModal').wait_for(state='hidden', timeout=10000)
        self.page.wait_for_function(
            '!document.querySelector(".modal-backdrop")', timeout=10000)

    def open_metadata(self, netcdf_file):
        """Select a label, then open its metadata: the two steps a user actually takes.

        Shuts an already-open editor first. A save reopens it on the file just edited, and its
        backdrop covers the tree, so reaching another label always means closing this one.
        """
        # A save reloads the page, so wait for the document to settle before deciding whether
        # anything needs closing.
        self.page.wait_for_function(
            'document.readyState === "complete" && ('
            '!document.querySelector(".modal-backdrop") || '
            'document.querySelector("#amaMetadataModal.show"))', timeout=15000)
        if self.page.locator('#amaMetadataModal.show').count():
            self.close_editor()
        self.tree_row(netcdf_file).click()
        button = self.page.locator('#editMetadataBtn')
        self.page.wait_for_selector('#editMetadataBtn:not(.d-none)', timeout=10000)
        button.click()
        self.panel().wait_for(state='visible', timeout=10000)
        # The modal fades and slides its dialog into place over ~300ms. Bootstrap animates the
        # .modal-dialog, not the .modal, so that is what has to settle before a click will land.
        # Waiting on the transform rather than on a fixed delay keeps this from being a flaky sleep.
        self.page.wait_for_function(
            'getComputedStyle(document.querySelector("#amaMetadataModal .modal-dialog"))'
            '.transform === "none"', timeout=10000)

    # -- panel loading -------------------------------------------------------------------------

    def test_clicking_a_label_opens_the_metadata_editor_for_its_file(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        panel = self.panel()
        self.assertEqual(panel.get_attribute('data-scope'), 'file')
        self.assertEqual(panel.get_attribute('data-netcdf-id'), str(self.nc_one.pk))
        self.assertTrue(self.page.locator('#amaMetadataModal').is_visible())

    def test_the_button_is_hidden_for_a_label_with_no_metadata_behind_it(self):
        """Collection, bundle and document labels have no AMA. Editing is not temporarily
        unavailable for them, it is meaningless, so the button goes away rather than sitting there
        greyed out with no way to explain itself."""
        self.open_bundle()

        self.tree_row(self.nc_one).click()
        self.page.wait_for_selector('#editMetadataBtn:not(.d-none)', timeout=10000)
        self.assertTrue(self.page.locator('#editMetadataBtn').is_visible())

        self.page.locator(
            '.eft-item[data-filename="{}"]'.format(self.collection_label)).click()
        # state='attached': the default is 'visible', which a .d-none element can never satisfy.
        self.page.wait_for_selector('#editMetadataBtn.d-none', state='attached', timeout=10000)
        self.assertFalse(self.page.locator('#editMetadataBtn').is_visible())
        self.assertTrue(self.page.locator('#editMetadataBtn').is_disabled())

    def test_all_three_sections_are_present_as_tabs(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        panel = self.panel()
        self.assertEqual(panel.locator('.nav-tabs .nav-link').count(), 3)
        # The first tab is showing, the others are not, until clicked.
        self.assertTrue(panel.locator('#amaTabmodel').is_visible())
        self.assertFalse(panel.locator('#amaTabsim').is_visible())

        panel.locator('button[data-bs-target="#amaTabsim"]').click()
        panel.locator('#amaTabsim').wait_for(state='visible', timeout=5000)

    def test_each_section_says_which_scope_its_values_are_held_at(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        panel = self.panel()
        # Nothing stored yet, so every section is following the collection.
        for prefix in ('model', 'sim', 'desc'):
            with self.subTest(section=prefix):
                self.assertTrue(panel.locator(
                    'input[name="{}-apply_scope"][value="collection"]'.format(prefix)).is_checked())

    # -- the guard that only a browser can exercise ---------------------------------------------

    def test_closing_with_unsaved_edits_warns_first(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.page.locator('#amaMetadataModal .btn-close').click()

        modal = self.page.locator('#amaUnsavedModal')
        modal.wait_for(state='visible', timeout=5000)
        self.assertIn('Discard unsaved changes', modal.inner_text())

        # Keeping the edit leaves the editor open on the same file.
        self.page.locator('#amaUnsavedModal button[data-bs-dismiss="modal"]').last.click()
        modal.wait_for(state='hidden', timeout=5000)
        self.assertTrue(self.page.locator('#amaMetadataModal').is_visible())
        self.assertEqual(self.panel().get_attribute('data-netcdf-id'), str(self.nc_one.pk))

    def test_discarding_unsaved_edits_closes_the_editor_without_saving(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.page.locator('#amaMetadataModal .btn-close').click()
        self.page.locator('#amaUnsavedModal').wait_for(state='visible', timeout=5000)
        self.page.click('#amaDiscardConfirm')

        self.page.locator('#amaMetadataModal').wait_for(state='hidden', timeout=10000)
        self.assertEqual(ModelMetadata.objects.count(), 0, 'a discarded edit was still saved')

    def test_closing_without_edits_does_not_warn(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        self.page.locator('#amaMetadataModal .btn-close').click()
        self.page.locator('#amaMetadataModal').wait_for(state='hidden', timeout=10000)
        self.assertFalse(self.page.locator('#amaUnsavedModal').is_visible())

    # -- saving --------------------------------------------------------------------------------

    def test_saving_closes_the_editor_and_keeps_the_label_selected(self):
        """Save is finished business. Reopening the editor after the reload made pressing Save
        look like the dialog had merely reloaded itself, with no sign the save had worked and an
        extra click needed to get out. The reload still has to resync the tree's readings, so the
        label stays selected and the user lands in front of the updated count."""
        self.open_bundle()
        self.open_metadata(self.nc_one)

        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.panel().locator('input[name="model-apply_scope"][value="file"]').check()

        # The editor is already open on this file, so "the editor is open on this file" cannot tell
        # a finished save from a save that has not started. This marker survives only until the
        # reload, which makes the wait unambiguous.
        self.page.evaluate('window.__preSave = true')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()

        # The save reloads the page. Wait for the new document, then for the tree to have
        # reselected the label.
        self.page.wait_for_function(
            'window.__preSave === undefined && '
            'document.querySelector(\'.eft-item[data-netcdf-id="{}"].active\')'.format(
                self.nc_one.pk), timeout=20000)

        self.assertFalse(self.page.locator('#amaMetadataModal').is_visible(),
                         'the editor was still open after a successful save')
        self.assertEqual(self.page.locator('.modal-backdrop').count(), 0,
                         'a backdrop was left over the page after a save')

        override = ModelMetadata.objects.get(netcdf_file=self.nc_one)
        self.assertEqual(override.name, 'MarsWRF')

    def test_the_reading_in_the_files_card_updates_after_a_save(self):
        self.open_bundle()
        row = self.tree_row(self.nc_one)
        # Nothing described yet, so the row nudges rather than reporting a count of zero.
        self.assertIn('No AMA metadata yet', row.inner_text())

        self.open_metadata(self.nc_one)
        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            'document.querySelector(\'.eft-item[data-netcdf-id="{}"]\') && '
            'document.querySelector(\'.eft-item[data-netcdf-id="{}"]\')'
            '.innerText.includes("1 of 28")'.format(self.nc_one.pk, self.nc_one.pk),
            timeout=20000)

    def test_a_rejected_value_is_reported_in_the_panel_without_saving(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        panel = self.panel()
        panel.locator('button[data-bs-target="#amaTabsim"]').click()
        panel.locator('#amaTabsim').wait_for(state='visible', timeout=5000)
        panel.locator('#id_sim-northern_boundary').fill('120')
        panel.locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            'document.querySelector("#amaMetadataPanel #amaPanelInner") && '
            'document.querySelector("#amaMetadataPanel #amaPanelInner")'
            '.dataset.saved === "false"', timeout=10000)
        self.assertEqual(SimulationConfiguration.objects.count(), 0)

    def test_a_non_ascii_value_is_refused_with_an_explanation(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)

        self.panel().locator('#id_model-institution').fill('Universit\u00e9 de Paris')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            "document.body.innerText.includes('basic Latin')", timeout=10000)
        self.assertEqual(ModelMetadata.objects.count(), 0)

    # -- copy from another file ------------------------------------------------------------------

    def test_copying_a_section_from_a_sibling_file(self):
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, name='LMD')
        self.open_bundle()
        self.open_metadata(self.nc_one)

        form = self.panel().locator('#amaCopyForm')
        form.locator('select[name="section"]').select_option('model')
        form.locator('select[name="source_netcdf"]').select_option(str(self.nc_two.pk))

        # The tree auto-selects the first label on load, so "this row is active" is already true
        # before the copy and cannot tell a finished copy from one that never started. This marker
        # survives only until the reload.
        self.page.evaluate('window.__preCopy = true')
        form.locator('button[type="submit"]').click()

        # A copy writes to the database and rebuilds the label, so it finishes the same way a
        # save does: the editor closes and the page comes back with the reading updated.
        self.page.wait_for_function('window.__preCopy === undefined', timeout=20000)
        self.page.wait_for_function(
            'document.querySelector(\'.eft-item[data-netcdf-id="{}"].active\')'.format(
                self.nc_one.pk), timeout=20000)
        self.assertFalse(self.page.locator('#amaMetadataModal').is_visible())

        copied = ModelMetadata.objects.get(netcdf_file=self.nc_one)
        self.assertEqual(copied.name, 'LMD')

    # -- end to end: the property the whole design exists for -----------------------------------

    def test_a_shared_section_reaches_every_file_in_the_collection_but_not_the_next_one(self):
        """The full round trip through the UI: fill one file's Model Metadata at collection scope,
        and every other file in that collection picks it up while the other collection does not.
        This is the behaviour copy-from-a-file alone could not provide."""
        self.open_bundle()
        self.open_metadata(self.nc_one)

        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.panel().locator('#id_model-institution').fill('NMSU')
        self.assertTrue(self.panel().locator(
            'input[name="model-apply_scope"][value="collection"]').is_checked())

        self.page.evaluate('window.__preSave = true')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()
        self.page.wait_for_function('window.__preSave === undefined', timeout=20000)

        # Both files in alpha now read 2 of 28; the file in beta is still undescribed. Filling
        # one file clears the nudge for its whole collection, because the others inherit it.
        for netcdf_file in (self.nc_one, self.nc_two):
            with self.subTest(netcdf_file=netcdf_file.title):
                self.page.wait_for_function(
                    'document.querySelector(\'.eft-item[data-netcdf-id="{}"]\')'
                    '.innerText.includes("2 of 28")'.format(netcdf_file.pk), timeout=10000)
        self.assertIn('No AMA metadata yet', self.tree_row(self.nc_beta).inner_text())

        # Stored once, on the collection, not copied onto each file.
        self.assertEqual(ModelMetadata.objects.filter(netcdf_file__isnull=False).count(), 0)
        self.assertEqual(ModelMetadata.objects.filter(collection=self.alpha).count(), 1)
        self.assertEqual(ModelMetadata.objects.filter(collection=self.beta).count(), 0)

    def test_a_file_given_its_own_values_stops_following_the_collection(self):
        self.open_bundle()
        self.open_metadata(self.nc_one)
        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.page.evaluate('window.__preSave = true')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()
        self.page.wait_for_function('window.__preSave === undefined', timeout=20000)

        self.open_metadata(self.nc_two)
        self.panel().locator('input[name="model-apply_scope"][value="file"]').check()
        self.panel().locator('#id_model-name').fill('LMD')
        self.page.evaluate('window.__preSave = true')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()
        self.page.wait_for_function('window.__preSave === undefined', timeout=20000)

        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'MarsWRF')
        self.assertEqual(ModelMetadata.override_for_file(self.nc_two).name, 'LMD')
        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))

        # And the radio still reads "just this file" when the panel is reopened.
        self.open_metadata(self.nc_two)
        self.assertTrue(self.panel().locator(
            'input[name="model-apply_scope"][value="file"]').is_checked())

    def test_an_archive_bundle_shows_no_metadata_affordance_at_all(self):
        """AMA is External-only, and the Files card is shared by both branches."""
        archive = Bundle.objects.create(
            name='archive_browser_bundle', user=self.user, version='1800', bundle_type='Archive')
        os.makedirs(archive.directory(), exist_ok=True)
        Product_Bundle.objects.create(bundle=archive)
        collection = AdditionalCollections.objects.create(
            bundle=archive, collection_name='data', collection_type='Data')
        os.makedirs(collection.directory(), exist_ok=True)
        netcdf_file = NetCDFFile.objects.create(
            title='00000.atmos_average.nc', file='00000.atmos_average.nc',
            bundle=archive, collection=collection, processed=True)
        self.write_label(netcdf_file)

        self.page.goto(
            self.live_server_url + reverse('build:bundle', kwargs={'pk_bundle': archive.pk}))
        self.page.wait_for_load_state('domcontentloaded')
        self.page.wait_for_selector('.elsa-file-tree .eft-item', timeout=10000)

        self.assertEqual(self.page.locator('#editMetadataBtn').count(), 0)
        self.assertEqual(self.page.locator('#amaMetadataModal').count(), 0)
        self.assertEqual(self.page.locator('.eft-item[data-netcdf-id]').count(), 0)
        self.assertNotIn('described', self.page.locator('.elsa-file-tree').inner_text())

    # -- the data-loss regression ---------------------------------------------------------------

    def test_select_all_stays_inside_its_own_collection(self):
        """The bug: a document-wide selector ticked every collection's files, and the delete that
        followed removed files the user could not see from that tab."""
        self.open_bundle(self.alpha)

        select_all = self.pane(self.alpha).locator('#selectAllWrapper')
        # No style override any more: the button used to ship with display:none and nothing ever
        # unhid it, so this test had to reveal it before it could click it.
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

    def test_a_collection_can_be_deleted_from_a_tab_you_are_not_standing_on(self):
        """The reason the modals had to leave the panes.

        Bootstrap gives an inactive .tab-pane display:none, so a modal inside beta's pane could
        not be shown while alpha was the open tab. The trigger is on every tab now, so that is a
        thing a user can ask for, and it has to work.
        """
        self.open_bundle(self.alpha)

        self.page.locator(
            '.ect-delete[data-bs-target="#deleteCollectionModal{}"]'.format(self.beta.pk)).click()

        modal = self.page.locator('#deleteCollectionModal{}'.format(self.beta.pk))
        modal.wait_for(state='visible', timeout=10000)
        self.assertIn(self.beta.collection_name, modal.inner_text())

    def test_the_collection_panes_open_on_the_files_not_on_boilerplate(self):
        self.open_bundle(self.alpha)
        pane = self.pane(self.alpha)

        self.assertNotIn('This is a model output', pane.inner_text())
        self.assertEqual(pane.locator('[data-bs-target^="#deleteCollectionModal"]').count(), 0)

    def test_dropping_files_on_the_upload_area_arms_the_real_input(self):
        """The drop area is scenery; the input is still the control.

        A drop has to write into the input and fire the same change event the picker fires, or the
        summary, the validation and the submit path all miss it.
        """
        self.open_bundle(self.alpha)
        pane = self.pane(self.alpha)

        # Build a real DataTransfer in the page and drop it on the area, which is what a browser
        # hands over on a genuine drag from the desktop.
        pane.locator('.ecu-drop').evaluate("""
            zone => {
                const data = new DataTransfer();
                data.items.add(new File(['CDF\\x01'], 'dropped.nc',
                                        {type: 'application/x-netcdf'}));
                zone.dispatchEvent(new DragEvent('drop',
                    {bubbles: true, cancelable: true, dataTransfer: data}));
            }
        """)

        picked = pane.locator('.ecu-drop-picked')
        picked.wait_for(state='visible', timeout=10000)
        self.assertIn('dropped.nc', picked.inner_text())
        self.assertEqual(
            pane.locator('input[name="netcdf_files"]').evaluate('el => el.files.length'), 1)
        # The invitation stands down once the well is holding something.
        self.assertFalse(pane.locator('.ecu-drop-idle').is_visible())

    def test_clearing_a_selection_puts_the_well_back_to_its_invitation(self):
        self.open_bundle(self.alpha)
        pane = self.pane(self.alpha)
        pane.locator('.ecu-drop').evaluate("""
            zone => {
                const data = new DataTransfer();
                data.items.add(new File(['CDF'], 'dropped.nc'));
                zone.dispatchEvent(new DragEvent('drop',
                    {bubbles: true, cancelable: true, dataTransfer: data}));
            }
        """)
        pane.locator('.ecu-drop-picked').wait_for(state='visible', timeout=10000)

        pane.locator('.ecu-clear').click()

        pane.locator('.ecu-drop-idle').wait_for(state='visible', timeout=10000)
        self.assertEqual(
            pane.locator('input[name="netcdf_files"]').evaluate('el => el.files.length'), 0)
        # Clear sits outside the label, or clicking it would open the file picker.
        self.assertEqual(
            pane.locator('.ecu-clear').evaluate('el => !!el.closest("label")'), False)

    def test_the_drop_area_opens_the_picker_without_any_script(self):
        """It is a <label for>, so the click-to-browse path is the browser's, not ours."""
        self.open_bundle(self.alpha)
        zone = self.pane(self.alpha).locator('.ecu-drop')

        self.assertEqual(zone.evaluate('el => el.tagName'), 'LABEL')
        self.assertEqual(
            zone.get_attribute('for'),
            self.pane(self.alpha).locator('input[name="netcdf_files"]').get_attribute('id'))

    def test_each_collection_tab_can_be_opened(self):
        """Collection tab targets used to embed the collection name, which broke on odd names."""
        self.open_bundle()
        for collection in (self.alpha, self.beta):
            with self.subTest(collection=collection.collection_name):
                tab = self.page.locator(
                    'button[data-bs-target="#additional_collection_{}"]'.format(collection.pk))
                self.assertTrue(tab.count() >= 1)

    # -- post-upload notice ---------------------------------------------------------------------

    def seed_upload(self, collection, netcdf_files):
        """Park the view's JSON reply where the reload will find it, then reload.

        This is exactly what the upload's own success handler does on its way into
        window.location.reload(). Pushing a real netCDF through the browser is covered
        server-side; what these tests are about is what the reloaded page does with the reply.
        """
        import json
        tab = '#additional_collection_{}'.format(collection.pk)
        # Built the way the upload view builds it, readings included: the notice reports what the
        # reply tells it, not what it can find in the Files tree.
        filled = views._ama_completeness_by_file(self.bundle)
        self.page.evaluate(
            'args => { sessionStorage.setItem(args.key, args.payload);'
            ' sessionStorage.setItem(args.tabKey, args.tab); }',
            {
                'key': 'elsaNetcdfUpload:{}'.format(self.bundle.pk),
                'tabKey': 'elsaCollectionTab:{}'.format(self.bundle.pk),
                'tab': tab,
                'payload': json.dumps({
                    'collection_id': collection.pk,
                    'collection_name': collection.collection_name,
                    'tab_target': tab,
                    'files': [{
                        'id': f.pk,
                        'title': f.title,
                        'ama_filled': filled.get(f.pk, 0),
                        'ama_total': views.AMA_TOTAL_FIELDS,
                        'ama_url': reverse('build:netcdf_ama', kwargs={
                            'pk_bundle': self.bundle.pk, 'pk_netcdf': f.pk}),
                    } for f in netcdf_files],
                }),
            })
        self.page.reload()
        self.page.wait_for_load_state('domcontentloaded')

    def notice(self):
        """The post-upload dialog's content. Its shell is always in the page, so tests that care
        whether it is being shown ask about visibility, never about presence."""
        return self.page.locator('#netcdfUploadModal .enu-notice')

    def wait_for_notice(self):
        self.page.locator('#netcdfUploadModal').wait_for(state='visible', timeout=10000)
        # Bootstrap slides the dialog in over ~300ms and animates .modal-dialog, not .modal, so
        # that is what has to settle before a click will land where it looks like it will.
        self.page.wait_for_function(
            'getComputedStyle(document.querySelector("#netcdfUploadModal .modal-dialog"))'
            '.transform === "none"', timeout=10000)

    def test_the_reload_comes_back_on_the_collection_that_was_uploaded_into(self):
        """The upload ends in a full reload, which used to drop the user on the document tab: the
        last thing a successful upload did was move them somewhere they had not asked to be."""
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])

        self.pane(self.beta).wait_for(state='visible', timeout=10000)
        self.assertFalse(self.page.locator('#doc_collection').is_visible(),
                         'the reload landed on the document tab instead of the collection')

    def test_the_notice_names_the_file_and_what_it_still_needs(self):
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])

        self.wait_for_notice()
        notice = self.notice()
        text = notice.inner_text()
        self.assertIn(self.nc_beta.title, text)
        self.assertIn(self.beta.collection_name, text)
        # Quoted from the tree's row, not recomputed here.
        self.assertIn('No AMA metadata yet', text)
        # The one place the page says out loud where the editor lives.
        self.assertIn('Edit AMA metadata', text)

    def test_the_notice_comes_up_as_a_dialog_over_the_page(self):
        """As a card in the collection pane it read as more page furniture and went unnoticed,
        which was the one thing it existed not to do."""
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])

        self.wait_for_notice()
        self.assertTrue(self.page.locator('#netcdfUploadModal.show').is_visible())
        self.assertEqual(self.page.locator('.modal-backdrop').count(), 1)

    def test_the_notice_opens_the_editor_on_the_file_it_names(self):
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])

        self.wait_for_notice()
        self.notice().locator('button', has_text='Add AMA metadata').click()

        self.panel().wait_for(state='visible', timeout=10000)
        self.assertEqual(self.panel().get_attribute('data-netcdf-id'), str(self.nc_beta.pk))
        # The handoff closes this dialog before opening the editor. Two open at once leaves a
        # second backdrop stacked over the first, and the first is never cleaned up.
        self.assertFalse(self.page.locator('#netcdfUploadModal').is_visible())
        self.assertEqual(self.page.locator('.modal-backdrop').count(), 1)

    def test_the_notice_says_what_a_file_inherited_and_offers_to_finish_it(self):
        """A file that inherits its collection's defaults did not arrive with nothing, and the
        dialog says so rather than reporting a blank. It is still short of the full set, though,
        so the offer is to finish it, not merely to look at it."""
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.beta, netcdf_file=None, name='LMD')

        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])

        self.wait_for_notice()
        notice = self.notice()
        text = notice.inner_text()
        self.assertIn('inherits', text)
        self.assertIn('1 of 28 described', text)
        self.assertEqual(notice.locator('button', has_text='Add AMA metadata').count(), 0)
        self.assertEqual(notice.locator('button', has_text='Complete AMA metadata').count(), 1)
        # Amber, because it is not complete. Review and a grey pill are for 28 of 28 only.
        self.assertEqual(notice.locator('.enu-status.enu-pending').count(), 1)

    def test_the_notice_lists_every_file_when_several_were_uploaded(self):
        self.open_bundle(self.beta)
        self.seed_upload(self.alpha, [self.nc_one, self.nc_two])

        self.wait_for_notice()
        notice = self.notice()
        text = notice.inner_text()
        self.assertIn('2 files', text)
        self.assertIn(self.nc_one.title, text)
        self.assertIn(self.nc_two.title, text)
        self.assertEqual(notice.locator('button', has_text='Describe').count(), 2)

    def test_the_notice_still_offers_a_way_in_for_a_file_the_tree_cannot_place(self):
        """The offer must not depend on the Files tree finding a row.

        A label the tree cannot place leaves the file with no row, and the notice used to fall
        back to a line of text with nothing to click. The standalone editor renders the same three
        forms, so that is where it goes instead.
        """
        ghost = self.make_netcdf('ghost.nc', self.beta)  # deliberately no label on disk
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [ghost])

        self.wait_for_notice()
        self.assertEqual(self.tree_row(ghost).count(), 0, 'the file was not meant to have a row')

        self.notice().locator('button', has_text='Add AMA metadata').click()
        self.page.wait_for_url('**/netcdf/{}/ama/'.format(ghost.pk), timeout=10000)
        self.page.locator('#amaPanelInner').wait_for(state='visible', timeout=10000)

    def test_the_notice_is_shown_once_and_not_on_every_later_reload(self):
        """It reports one upload. Coming back on a later reload would make it wallpaper."""
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])
        self.wait_for_notice()

        self.page.reload()
        self.page.wait_for_load_state('domcontentloaded')
        self.pane(self.beta).wait_for(state='visible', timeout=10000)

        self.assertFalse(self.notice().is_visible())

    def test_dismissing_the_notice_leaves_the_page_alone(self):
        self.open_bundle(self.alpha)
        self.seed_upload(self.beta, [self.nc_beta])
        self.wait_for_notice()

        self.notice().locator('button', has_text='Not now').click()
        self.page.locator('#netcdfUploadModal').wait_for(state='hidden', timeout=10000)

        # No orphan backdrop: it would sit invisibly over the page and swallow every later click.
        self.page.wait_for_function(
            '!document.querySelector(".modal-backdrop")', timeout=10000)
        self.assertTrue(self.pane(self.beta).is_visible())

    # -- end to end: a real upload, all the way to the label on disk ----------------------------

    def upload_through_the_form(self, collection, names, expect_notice=True):
        """A real upload: real files, the real input, the real Upload button.

        Everything else in this section seeds the upload's reply by hand, which is the right trade
        for testing one behaviour at a time but never exercises the join between them. This walks
        the whole path: the view writes the files and their labels, answers with JSON, the page
        reloads itself, and the reloaded page reads that reply back out.
        """
        import shutil
        import tempfile
        from build.test_ama_audit import real_netcdf_bytes

        payload = real_netcdf_bytes()
        folder = tempfile.mkdtemp(prefix='elsa-e2e-')
        self.addCleanup(shutil.rmtree, folder, True)
        paths = []
        for name in names:
            path = os.path.join(folder, name)
            with open(path, 'wb') as handle:
                handle.write(payload)
            paths.append(path)

        pane = self.pane(collection)
        pane.locator('input[name="netcdf_files"]').set_input_files(paths)
        pane.locator('#uploadBtn').click()
        if expect_notice:
            # The post finishes, then the page reloads itself 1.5s later. The dialog exists only on
            # the far side of that reload, so waiting for it waits for the whole round trip.
            self.wait_for_notice()

    def label_path(self, netcdf_file):
        stem = os.path.splitext(os.path.basename(netcdf_file.file.name))[0]
        return os.path.join(netcdf_file.directory(), stem + '.xml')

    def test_end_to_end_upload_to_metadata_in_the_label_on_disk(self):
        """Upload, notice, editor, save, and the value in the XML, in one pass.

        The filename is one the storage has to sanitise ("a - Copy.nc" is stored "a_-_Copy.nc"),
        because that is the case that broke every join in this chain at once: the tree could not
        match the label to its file, so the row carried no id, so the notice had nothing to offer
        and the viewer had no editor button.
        """
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_bundle(self.beta)

        name = '00000.atmos_average_pstd - Copy.nc'
        self.upload_through_the_form(self.beta, [name])

        uploaded = NetCDFFile.objects.get(title=name)
        self.assertTrue(uploaded.processed, 'the upload did not process')
        self.assertTrue(os.path.exists(self.label_path(uploaded)), 'no label was written')

        # 1. The reload came back on the collection that was uploaded into, not the document tab.
        self.assertTrue(self.pane(self.beta).is_visible())
        self.assertFalse(self.page.locator('#doc_collection').is_visible())

        # 2. The dialog names the file, the collection, and what is still missing.
        notice = self.notice().inner_text()
        self.assertIn(name, notice)
        self.assertIn(self.beta.collection_name, notice)
        self.assertIn('No AMA metadata yet', notice)

        # 3. The uploaded files list names it in full and spends its width on the name.
        listing = self.pane(self.beta).locator('#bulkDeleteNetCDFForm')
        self.assertIn(name, listing.inner_text())
        self.assertNotIn('Own metadata', listing.inner_text())
        width = listing.locator('a[title="{}"]'.format(name)).evaluate(
            'el => el.getBoundingClientRect().width')
        self.assertGreater(width, 150, 'the filename is still capped at the old fixed width')

        # 4. The tree row flags the gap and does not tick anything.
        row = self.tree_row(uploaded)
        self.assertEqual(row.count(), 1, 'the tree could not match the label to its file')
        self.assertEqual(row.locator('.bi-exclamation-triangle-fill').count(), 1)
        self.assertEqual(row.locator('.bi-check-circle-fill').count(), 0)

        # 5. The dialog's offer opens the editor on that file, and hands over cleanly.
        self.notice().locator('button', has_text='Add AMA metadata').click()
        self.panel().wait_for(state='visible', timeout=10000)
        self.assertEqual(self.panel().get_attribute('data-netcdf-id'), str(uploaded.pk))
        self.assertFalse(self.page.locator('#netcdfUploadModal').is_visible())
        self.assertEqual(self.page.locator('.modal-backdrop').count(), 1)

        # 6. A value saved in the editor reaches the label on disk.
        self.page.wait_for_function(
            'getComputedStyle(document.querySelector("#amaMetadataModal .modal-dialog"))'
            '.transform === "none"', timeout=10000)
        self.panel().locator('#id_model-name').fill('MarsWRF')
        self.panel().locator('#amaPanelForm button[type="submit"]').click()

        self.page.wait_for_function(
            'document.querySelector(\'.eft-item[data-netcdf-id="{0}"]\') && '
            'document.querySelector(\'.eft-item[data-netcdf-id="{0}"]\')'
            '.innerText.includes("1 of 28")'.format(uploaded.pk), timeout=20000)

        # 7. The row reports the new count and keeps cautioning: one field of 28 is a long way
        #    short, and it must not be shown the way a fully described label is.
        row = self.tree_row(uploaded)
        self.assertIn('1 of 28 described', row.inner_text())
        self.assertEqual(row.locator('.bi-exclamation-triangle-fill').count(), 1)
        self.assertEqual(row.locator('.bi-check-circle-fill').count(), 0)

        # 8. And the value is really in the XML, not only in the database.
        root = ET.parse(self.label_path(uploaded)).getroot()
        written = root.find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA/ama:Model_Metadata/ama:name',
            namespaces=NS)
        self.assertIsNotNone(written, 'no Model_Metadata name in the label')
        self.assertEqual(written.text, 'MarsWRF')

    def test_end_to_end_several_files_in_one_upload(self):
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_bundle(self.beta)

        names = ['e2e_one.nc', 'e2e_two.nc']
        self.upload_through_the_form(self.beta, names)

        notice = self.notice()
        text = notice.inner_text()
        self.assertIn('2 files', text)
        for name in names:
            self.assertIn(name, text)
        self.assertEqual(notice.locator('button', has_text='Describe').count(), 2)

        # Each row opens the editor on its own file, not on whichever came first.
        second = NetCDFFile.objects.get(title=names[1])
        notice.locator('.enu-file', has_text=names[1]).locator(
            'button', has_text='Describe').click()
        self.panel().wait_for(state='visible', timeout=10000)
        self.assertEqual(self.panel().get_attribute('data-netcdf-id'), str(second.pk))

    def test_end_to_end_a_second_copy_of_the_same_file_is_refused(self):
        """The name needs sanitising, which is exactly the case the check used to miss: the second
        upload overwrote the first file and its label and left two rows pointing at one path."""
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.open_bundle(self.beta)

        name = 'a - Copy.nc'
        self.upload_through_the_form(self.beta, [name])
        self.notice().locator('button', has_text='Not now').click()
        self.page.locator('#netcdfUploadModal').wait_for(state='hidden', timeout=10000)

        self.upload_through_the_form(self.beta, [name], expect_notice=False)

        error = self.pane(self.beta).locator('.netcdf-upload-error')
        error.wait_for(state='visible', timeout=20000)
        self.assertIn('already in', error.inner_text())
        # Refused means refused: no second row, and the first file is untouched.
        self.assertEqual(NetCDFFile.objects.filter(title=name).count(), 1)
        self.assertFalse(self.page.locator('#netcdfUploadModal').is_visible(),
                         'a refused upload announced itself as a success')

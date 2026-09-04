# -*- coding: utf-8 -*-
"""Exploratory audit of the AMA feature. Named test_* so it is picked up by the default test discovery.

Each test probes something the main suite does not, looking for real defects rather than
confirming known-good behaviour. Run with:
    python manage.py test build.test_ama_audit --settings=test_settings
"""

from __future__ import unicode_literals

import io
import os
import re

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from build import views
from build.forms import SimulationConfigurationForm
from build.models import (AdditionalCollections, Bundle, FileDescription,
                          ModelMetadata, NetCDFFile, Product_Bundle,
                          SimulationConfiguration)
from build.tests import NS, AMATestCaseMixin, MINIMAL_LDD, ama_child_names

ET = views.ET


def real_netcdf_bytes():
    """A tiny valid NetCDF file as raw bytes, for exercising the real upload path."""
    import numpy as np
    import tempfile
    import xarray as xr

    dataset = xr.Dataset(
        {'temp': (('lat', 'lon'), np.zeros((2, 2), dtype='float32'), {'units': 'K'})},
        coords={'lat': ('lat', np.array([-45.0, 45.0]), {'units': 'degrees_north'}),
                'lon': ('lon', np.array([0.0, 180.0]), {'units': 'degrees_east'})})
    handle = tempfile.NamedTemporaryFile(suffix='.nc', delete=False)
    handle.close()
    dataset.to_netcdf(handle.name)
    dataset.close()
    with open(handle.name, 'rb') as f:
        data = f.read()
    os.remove(handle.name)
    return data


# --------------------------------------------------------------------------------------------
# 1. Disk hygiene: deleting things must not leave orphans behind
# --------------------------------------------------------------------------------------------

class DiskHygieneAudit(AMATestCaseMixin, TestCase):

    def place_on_disk(self, netcdf_file):
        """Put the .nc and its .xml where the current pipeline actually writes them."""
        directory = netcdf_file.directory()
        nc_path = os.path.join(directory, os.path.basename(netcdf_file.file.name))
        xml_path = os.path.join(directory, os.path.basename(netcdf_file.file.name)[:-3] + '.xml')
        with open(nc_path, 'wb') as handle:
            handle.write(b'CDF\x01')
        with open(xml_path, 'w') as handle:
            handle.write('<xml/>')
        return nc_path, xml_path

    def test_bulk_delete_removes_the_netcdf_from_the_collection_directory(self):
        nc_path, _xml = self.place_on_disk(self.nc_one)
        self.login()

        self.client.post(
            reverse('build:bulk_delete_netcdf', kwargs={'pk_bundle': self.bundle.pk}),
            {'selected_netcdf': [self.nc_one.pk]})

        self.assertFalse(os.path.exists(nc_path),
                         'NetCDF left orphaned on disk after delete: {}'.format(nc_path))

    def test_bulk_delete_removes_the_label_from_the_collection_directory(self):
        _nc, xml_path = self.place_on_disk(self.nc_one)
        self.login()

        self.client.post(
            reverse('build:bulk_delete_netcdf', kwargs={'pk_bundle': self.bundle.pk}),
            {'selected_netcdf': [self.nc_one.pk]})

        self.assertFalse(os.path.exists(xml_path),
                         'XML label left orphaned on disk after delete: {}'.format(xml_path))

    def test_deleting_a_collection_removes_its_netcdf_files_from_disk(self):
        nc_path, xml_path = self.place_on_disk(self.nc_beta)
        self.login()

        self.client.post(reverse('build:delete_collection', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_collection': self.beta.pk}))

        self.assertFalse(os.path.exists(nc_path), 'collection delete left the NetCDF behind')
        self.assertFalse(os.path.exists(xml_path), 'collection delete left the label behind')


# --------------------------------------------------------------------------------------------
# 2. The real upload path, end to end through the view Said changed
# --------------------------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class UploadPathAudit(AMATestCaseMixin, TestCase):

    def setUp(self):
        super().setUp()
        try:
            import numpy  # noqa: F401
            import xarray  # noqa: F401
        except ImportError as exc:
            self.skipTest('xarray unavailable: {}'.format(exc))
        from django.core.cache import cache
        cache.clear()
        cache.set('ldd_schema:' + views.AMA_LDD_URL, MINIMAL_LDD.encode('utf-8'), 300)
        self.addCleanup(cache.clear)
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def upload(self, filename, collection):
        payload = real_netcdf_bytes()
        return self.client.post(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
            {
                'collection': collection.collection_name,
                'netcdf_files': SimpleUploadedFile(filename, payload),
            })

    def test_an_upload_is_attached_to_the_collection_it_was_uploaded_under(self):
        self.upload('fresh.nc', self.beta)
        fresh = NetCDFFile.objects.get(title='fresh.nc')
        self.assertEqual(fresh.collection, self.beta,
                         'upload did not record which collection it went into')

    def test_an_upload_inherits_the_collection_defaults_immediately(self):
        self.make_default_model_metadata(collection=self.beta, name='LMD')
        self.upload('fresh.nc', self.beta)

        fresh = NetCDFFile.objects.get(title='fresh.nc')
        # Django uniquifies the stored name when one already exists, so the label follows
        # file.name rather than the title the user uploaded under.
        label = os.path.join(
            fresh.directory(), os.path.basename(fresh.file.name)[:-3] + '.xml')
        self.assertTrue(os.path.exists(label), 'no label written for the upload')
        root = ET.parse(label).getroot()
        metadata = root.find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA/ama:Model_Metadata', namespaces=NS)
        self.assertEqual(metadata.find('ama:name', namespaces=NS).text, 'LMD',
                         'a newly uploaded file did not pick up its collection defaults')

    def test_an_upload_does_not_inherit_another_collections_defaults(self):
        self.make_default_model_metadata(collection=self.alpha, name='MarsWRF')
        self.upload('fresh.nc', self.beta)

        fresh = NetCDFFile.objects.get(title='fresh.nc')
        root = ET.parse(os.path.join(
            fresh.directory(), os.path.basename(fresh.file.name)[:-3] + '.xml')).getroot()
        metadata = root.find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA/ama:Model_Metadata', namespaces=NS)
        self.assertEqual(len(metadata), 0, 'collection defaults leaked across collections')

    def test_a_scripted_upload_is_answered_with_what_the_reloaded_page_needs(self):
        """The upload page reloads itself, so the reply has to carry what survives the reload.

        A redirect carried nothing, and messages.success() cannot stand in for this: the XHR
        follows the 302 silently and consumes the message rendering a response nobody sees.
        """
        response = self.client.post(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
            {
                'collection': self.beta.collection_name,
                'netcdf_files': SimpleUploadedFile('fresh.nc', real_netcdf_bytes()),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['collection_id'], self.beta.pk)
        self.assertEqual(payload['collection_name'], self.beta.collection_name)
        self.assertEqual(payload['tab_target'],
                         '#additional_collection_{}'.format(self.beta.pk))
        self.assertEqual([entry['title'] for entry in payload['files']], ['fresh.nc'])
        fresh = NetCDFFile.objects.get(title='fresh.nc')
        self.assertEqual([entry['id'] for entry in payload['files']], [fresh.pk])

        # The AMA reading travels with the reply rather than being read back off the Files tree,
        # which cannot say anything about a file whose label it fails to place.
        entry = payload['files'][0]
        self.assertEqual(entry['ama_filled'], 0)
        self.assertEqual(entry['ama_total'], views.AMA_TOTAL_FIELDS)
        self.assertEqual(entry['ama_url'], reverse('build:netcdf_ama', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': fresh.pk}))

    def test_a_scripted_upload_reports_what_the_new_file_already_inherits(self):
        self.make_default_model_metadata(collection=self.beta, name='LMD')

        response = self.client.post(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
            {
                'collection': self.beta.collection_name,
                'netcdf_files': SimpleUploadedFile('fresh.nc', real_netcdf_bytes()),
            },
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        # The helper's defaults fill name and institution.
        self.assertEqual(response.json()['files'][0]['ama_filled'], 2)

    def test_an_upload_without_javascript_still_redirects(self):
        """The plain form post is the fallback for a browser that never runs the upload script."""
        response = self.upload('fresh.nc', self.beta)

        self.assertEqual(response.status_code, 302)

    def test_an_upload_naming_a_collection_of_another_bundle_is_refused(self):
        other = Bundle.objects.create(name='other_bundle', user=self.user, version='1800')
        AdditionalCollections.objects.create(
            bundle=other, collection_name='foreign', collection_type='External')

        response = self.upload('sneaky.nc', AdditionalCollections.objects.get(
            bundle=other, collection_name='foreign'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(NetCDFFile.objects.filter(title='sneaky.nc').count(), 0)


# --------------------------------------------------------------------------------------------
# 3. Content safety: what users type ends up in both HTML and XML
# --------------------------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class ContentSafetyAudit(AMATestCaseMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.real_regenerate = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', self.real_regenerate)
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def test_xml_special_characters_are_escaped_in_the_label(self):
        self.make_default_model_metadata(name='Mars & Venus <GCM>')

        root = ET.parse(os.path.join(
            os.path.dirname(views.__file__), '..', 'templates', 'pds4_labels',
            'base_templates', 'Template_PE.xml')).getroot()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)
        serialized = ET.tostring(root, encoding='unicode')

        self.assertIn('Mars &amp; Venus &lt;GCM&gt;', serialized)
        self.assertNotIn('<GCM>', serialized)

    def test_script_tags_do_not_execute_in_the_panel(self):
        self.make_default_model_metadata(name='<script>alert(1)</script>')

        content = self.panel_get(self.defaults_url()).content.decode('utf-8')

        self.assertNotIn('<script>alert(1)</script>', content)
        self.assertIn('&lt;script&gt;', content)

    def test_a_quote_in_a_value_does_not_break_out_of_the_input_attribute(self):
        self.make_default_model_metadata(name='He said "hello"')
        content = self.panel_get(self.defaults_url()).content.decode('utf-8')
        self.assertNotIn('value="He said "hello""', content)

    def test_unicode_survives_the_round_trip_to_xml(self):
        self.make_default_model_metadata(institution='Université de Paris — LMD')

        root = ET.parse(os.path.join(
            os.path.dirname(views.__file__), '..', 'templates', 'pds4_labels',
            'base_templates', 'Template_PE.xml')).getroot()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)
        metadata = root.find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA/ama:Model_Metadata', namespaces=NS)

        self.assertEqual(metadata.find('ama:institution', namespaces=NS).text,
                         'Université de Paris — LMD')

    def test_a_value_longer_than_the_ldd_allows_is_rejected(self):
        """Every AMA string is maxLength=255 in the schema, so 300 characters must not be stored."""
        response = self.panel_post(self.defaults_url(), {'model-name': 'x' * 300})

        self.assertEqual(ModelMetadata.objects.count(), 0,
                         'a value too long for the LDD was accepted')
        self.assertIn('data-saved="false"', response.content.decode('utf-8'))

    def test_a_newline_in_a_collapsed_string_field(self):
        """ASCII_Short_String_Collapsed forbids embedded newlines."""
        self.panel_post(self.defaults_url(), {'model-name': 'line one\nline two'})
        record = ModelMetadata.default_for_collection(self.alpha)
        if record:
            self.assertNotIn('\n', record.name,
                             'a newline reached a Short_String_Collapsed field')


# --------------------------------------------------------------------------------------------
# 4. Numeric edge cases against the LDD's declared ranges
# --------------------------------------------------------------------------------------------

class NumericAudit(AMATestCaseMixin, TestCase):

    def valid(self, **data):
        return SimulationConfigurationForm(data=data, scope='collection').is_valid()

    def test_the_exact_boundaries_are_accepted(self):
        for field, value in (('northern_boundary', '90'), ('northern_boundary', '-90'),
                             ('southern_boundary', '90'), ('eastern_boundary', '-180'),
                             ('western_boundary', '360')):
            with self.subTest(field=field, value=value):
                self.assertTrue(self.valid(**{field: value}))

    def test_just_outside_the_boundaries_is_rejected(self):
        for field, value in (('northern_boundary', '90.001'), ('southern_boundary', '-90.001'),
                             ('eastern_boundary', '-180.001'), ('western_boundary', '360.001')):
            with self.subTest(field=field, value=value):
                self.assertFalse(self.valid(**{field: value}))

    def test_text_in_a_numeric_field_is_rejected(self):
        self.assertFalse(self.valid(model_timestep='thirty seconds'))

    def test_a_large_float_does_not_reach_the_label_in_scientific_notation(self):
        """ASCII_Real permits exponents, but a stored value should round-trip readably."""
        record = self.make_default_simulation(model_timestep=1e20)
        self.assertEqual(record.filled_values()['model_timestep'], '1e+20')

    def test_negative_zero_is_kept(self):
        record = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, northern_boundary=-0.0)
        self.assertIn('northern_boundary', record.filled_values())


# --------------------------------------------------------------------------------------------
# 5. Structural correctness of the rendered DOM, parsed the way a browser would
# --------------------------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class RenderedDomAudit(AMATestCaseMixin, TestCase):

    def setUp(self):
        super().setUp()
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def bundle_html(self):
        return self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

    def combined_html(self):
        """The bundle page with a panel injected, which is what the DOM really looks like."""
        page = self.bundle_html()
        panel = self.panel_get(self.file_url()).content.decode('utf-8')
        marker = '<div class="ama-panel-content"></div>'
        self.assertIn(marker, page)
        return page.replace(marker, '<div class="ama-panel-content">{}</div>'.format(panel), 1)

    def parsed(self, html):
        import html5lib
        return html5lib.parse(html, namespaceHTMLElements=False)

    def duplicate_ids(self):
        tree = self.parsed(self.combined_html())
        ids = [el.get('id') for el in tree.iter() if el.get('id')]
        return sorted({value for value in ids if ids.count(value) > 1})

    def test_no_ama_element_id_is_duplicated(self):
        """The AMA panel and its form fields must be unique even with a panel open."""
        ama_duplicates = [value for value in self.duplicate_ids()
                          if value.startswith('ama') or value.startswith('id_')]
        self.assertEqual(ama_duplicates, [], 'duplicate AMA element ids in the live DOM')

    def test_the_remaining_duplicate_ids_are_all_upload_card_controls(self):
        """Documents a pre-existing issue rather than asserting it away.

        The NetCDF upload/delete card renders once per collection, so its control ids repeat. The
        JavaScript resolves those elements relative to the submitted form, so they still work, but
        the ids are invalid HTML and should be made unique when that card is next touched.
        """
        expected = {
            'bulkDeleteBtn', 'bulkDeleteNetCDFForm', 'collection',
            'netcdfUploadForm', 'selectAllWrapper', 'uploadBtn', 'uploadCancelBtn',
            'uploadPercent', 'uploadProgressBar', 'uploadProgressWrapper', 'uploadStatusText',
            'uploadSuccessMsg',
        }
        self.assertEqual(set(self.duplicate_ids()) - expected, set(),
                         'a new duplicate id appeared outside the known upload-card set')

    def test_no_ama_label_points_at_a_missing_element(self):
        tree = self.parsed(self.combined_html())
        ids = {el.get('id') for el in tree.iter() if el.get('id')}
        dangling = sorted({
            el.get('for') for el in tree.iter('label')
            if el.get('for') and el.get('for') not in ids
            and (el.get('for').startswith('id_') or el.get('for').startswith('ama'))})
        self.assertEqual(dangling, [], 'AMA labels pointing at ids that do not exist')

    def test_no_form_is_nested_inside_another_form(self):
        tree = self.parsed(self.combined_html())
        for form in tree.iter('form'):
            nested = list(form.iter('form'))
            self.assertEqual(len(nested), 1,
                             'nested <form> found, which browsers silently drop')

    def test_every_button_inside_a_form_declares_its_type(self):
        """A button with no type defaults to submit and will fire the surrounding form."""
        tree = self.parsed(self.combined_html())
        offenders = []
        for form in tree.iter('form'):
            for button in form.iter('button'):
                if not button.get('type'):
                    offenders.append((form.get('id'), (button.text or '').strip()[:40]))
        self.assertEqual(offenders, [], 'typeless buttons inside forms')

    def test_the_section_tabs_are_all_addressable(self):
        panel = self.panel_get(self.file_url()).content.decode('utf-8')
        targets = set(re.findall(r'data-bs-target="#(amaTab\w+)"', panel))
        ids = set(re.findall(r'id="(amaTab\w+)"', panel))
        self.assertEqual(targets, ids, 'tab toggles and panes do not line up')
        self.assertEqual(len(ids), 3, 'expected one pane per AMA section')

    def test_there_is_exactly_one_metadata_editor(self):
        """It used to be one wrapper per collection, each carrying its own copy of the panel
        plumbing. One editor, filled on demand, cannot drift between collections."""
        html = self.bundle_html()
        self.assertEqual(len(re.findall(r'id="amaMetadataPanel"', html)), 1)
        self.assertEqual(re.findall(r'id="(amaPanel\d+)"', html), [])

    def test_every_label_row_that_offers_metadata_carries_a_url_for_it(self):
        # The Files card walks the bundle directory, so a file with no label on disk has no row.
        for netcdf_file in (self.nc_one, self.nc_two, self.nc_beta):
            self.write_label(netcdf_file)
        html = self.bundle_html()
        rows = re.findall(r'data-netcdf-id="(\d+)"\s*\n?\s*data-ama-url="([^"]+)"', html)
        self.assertNotEqual(rows, [], 'no data label offered a metadata url')
        for netcdf_id, url in rows:
            with self.subTest(netcdf_id=netcdf_id):
                self.assertIn('/netcdf/{}/ama/'.format(netcdf_id), url)

    def test_the_panel_form_posts_to_the_scope_it_is_showing(self):
        panel = self.panel_get(self.file_url()).content.decode('utf-8')
        self.assertIn('action="{}"'.format(self.file_url()), panel)
        defaults = self.panel_get(self.defaults_url()).content.decode('utf-8')
        self.assertIn('action="{}"'.format(self.defaults_url()), defaults)


# --------------------------------------------------------------------------------------------
# 6. Awkward states a user can actually reach
# --------------------------------------------------------------------------------------------

@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AwkwardStateAudit(AMATestCaseMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.real_regenerate = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', self.real_regenerate)
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def test_a_bundle_with_no_collections_still_renders(self):
        AdditionalCollections.objects.filter(bundle=self.bundle).delete()
        response = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}))
        self.assertEqual(response.status_code, 200)

    def test_two_collections_with_the_same_name_in_one_bundle(self):
        """collection_name has no unique constraint, so the upload lookup can be ambiguous."""
        twin = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name='alpha', collection_type='External')
        self.assertEqual(twin.directory(), self.alpha.directory(),
                         'two same-named collections share a directory')

    def test_sharing_a_section_works_when_no_default_exists_yet(self):
        self.panel_post(self.file_url(), {
            'sim-horizontal_grid_type': 'cube-sphere', 'sim-apply_scope': 'collection'})

        default = SimulationConfiguration.default_for_collection(self.alpha)
        self.assertIsNotNone(default, 'apply-to-collection did not create the default')
        self.assertEqual(default.horizontal_grid_type, 'cube-sphere')

    def test_sharing_an_empty_section_does_not_wipe_the_default(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon')

        self.panel_post(self.file_url(), {'sim-apply_scope': 'collection'})

        default = SimulationConfiguration.default_for_collection(self.alpha)
        self.assertIsNotNone(default)
        self.assertEqual(default.horizontal_grid_type, 'lat-lon',
                         'ticking apply on an empty form blanked the collection default')

    def test_the_panel_for_a_file_whose_collection_was_deleted(self):
        self.beta.delete()
        response = self.client.get(reverse('build:netcdf_ama', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_beta.pk}))
        self.assertIn(response.status_code, (302, 404))

    def test_an_anonymous_visitor_cannot_read_the_panel(self):
        self.client.logout()
        response = self.panel_get(self.defaults_url())
        self.assertNotEqual(response.status_code, 200,
                            'the AMA panel is readable without logging in')

    def test_a_post_without_a_csrf_token_is_rejected(self):
        from django.test import Client
        strict = Client(enforce_csrf_checks=True)
        strict.login(username='ama_tester', password='pw-for-tests')
        response = strict.post(self.defaults_url(), {'model-name': 'MarsWRF'},
                               HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 403)

    def test_whitespace_only_values_are_treated_as_empty(self):
        self.panel_post(self.defaults_url(), {'model-name': '     '})
        record = ModelMetadata.default_for_collection(self.alpha)
        if record is not None:
            self.assertEqual(record.filled_values(), {},
                             'a whitespace-only value would emit an empty element')

    def test_saving_a_file_panel_does_not_create_rows_for_untouched_sections(self):
        self.panel_post(self.file_url(), {'sim-horizontal_grid_type': 'cube-sphere'})

        self.assertEqual(SimulationConfiguration.objects.filter(
            netcdf_file=self.nc_one).count(), 1)
        self.assertEqual(ModelMetadata.objects.filter(netcdf_file=self.nc_one).count(), 0,
                         'an untouched section created an empty override row')
        self.assertEqual(FileDescription.objects.filter(netcdf_file=self.nc_one).count(), 0)

    def test_a_file_panel_prefilled_from_defaults_does_not_silently_fork_on_save(self):
        """Opening a file, changing nothing and saving should leave it inheriting.

        The GET prefills from the collection default, so a blind save posts those same values
        back. If that stores an override, every file the user merely looked at would silently
        stop following the collection.
        """
        self.make_default_simulation(horizontal_grid_type='lat-lon')
        panel = self.panel_get(self.file_url()).content.decode('utf-8')

        payload = {}
        for tag in re.findall(r'<input[^>]*name="sim-[^"]+"[^>]*>', panel):
            name = re.search(r'name="([^"]+)"', tag).group(1)
            value = re.search(r'value="([^"]*)"', tag)
            # A browser posts only the checked radio. Keeping the last of a group would post
            # "just this file", the opposite instruction to the one this probe is named for.
            if 'type="radio"' in tag and 'checked' not in tag:
                continue
            payload[name] = value.group(1) if value else ''
        # Textareas carry their content between the tags, not in a value attribute. A browser
        # submits them, so leaving them out would make this probe unrealistic.
        for name, value in re.findall(
                r'<textarea[^>]*name="(sim-[^"]+)"[^>]*>(.*?)</textarea>', panel, re.S):
            payload[name] = value.strip()

        self.panel_post(self.file_url(), payload)

        self.assertEqual(
            SimulationConfiguration.objects.filter(netcdf_file=self.nc_one).count(), 0,
            'opening a file and saving without edits forked it off the collection default')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class RenderedStructureAudit(AMATestCaseMixin, TestCase):
    """Tag-balance and nesting checks.

    These exist because the original DOM audit checked ids and nested forms but never tag balance,
    and an over-closed collection card slipped through: it unwound the tab-content container, threw
    later collections outside it, and produced large blocks of empty space on the page.
    """

    def setUp(self):
        super().setUp()
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def rendered(self):
        return self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

    def div_delta(self, html):
        return len(re.findall(r'<div\b', html)) - len(re.findall(r'</div>', html))

    def test_each_collection_block_is_tag_balanced(self):
        """The delta must not move with the number of collections: if it does, the repeated block
        opens and closes a different number of divs and the layout unwinds."""
        with_two = self.div_delta(self.rendered())

        NetCDFFile.objects.filter(collection=self.beta).delete()
        self.beta.delete()
        with_one = self.div_delta(self.rendered())

        NetCDFFile.objects.all().delete()
        self.alpha.delete()
        with_none = self.div_delta(self.rendered())

        self.assertEqual([with_two, with_one], [with_none, with_none],
                         'the per-collection block is not tag balanced')

    def test_every_collection_pane_stays_inside_the_tab_container(self):
        """An over-closed card lets later panes escape tab-content, so they stop being tabs."""
        import html5lib

        tree = html5lib.parse(self.rendered(), namespaceHTMLElements=False)
        parents = {}
        for element in tree.iter('div'):
            for child in element:
                parents[id(child)] = element

        seen = 0
        for element in tree.iter('div'):
            identifier = element.get('id') or ''
            if not identifier.startswith('additional_collection_'):
                continue
            seen += 1
            parent = parents.get(id(element))
            self.assertIsNotNone(parent, '{} has no parent'.format(identifier))
            self.assertIn('tab-content', parent.get('class', ''),
                          '{} escaped the tab container'.format(identifier))
        self.assertEqual(seen, 2, 'expected one pane per collection')

    def test_no_button_targets_a_modal_that_does_not_exist(self):
        """The leftover status cards pointed at modals that had already been deleted, so their
        Edit buttons silently did nothing."""
        html = self.rendered()

        targets = set(re.findall(r'data-bs-toggle="modal"[^>]*data-bs-target="#([\w-]+)"', html))
        targets |= set(re.findall(r'data-bs-target="#([\w-]+)"[^>]*data-bs-toggle="modal"', html))
        present = set(re.findall(r'class="modal fade[^"]*"\s+id="([\w-]+)"', html))
        present |= set(re.findall(r'id="([\w-]+)"[^>]*class="modal fade', html))

        self.assertEqual(sorted(targets - present), [],
                         'buttons point at modals that are not on the page')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class DuplicateUploadAudit(AMATestCaseMixin, TestCase):
    """Two uploads of the same name into one collection used to collide.

    Files are stored under their own name inside the collection directory and the label is named
    after the file, so the second upload overwrote both the first file and its label while leaving
    a second database row pointing at the same path. The two rows then shared one label, so their
    per-file AMA metadata silently overwrote each other.
    """

    def setUp(self):
        super().setUp()
        try:
            import numpy  # noqa: F401
            import xarray  # noqa: F401
        except ImportError as exc:
            self.skipTest('xarray unavailable: {}'.format(exc))
        from django.core.cache import cache
        cache.clear()
        cache.set('ldd_schema:' + views.AMA_LDD_URL, MINIMAL_LDD.encode('utf-8'), 300)
        self.addCleanup(cache.clear)
        Product_Bundle.objects.create(bundle=self.bundle)
        NetCDFFile.objects.all().delete()
        self.login()
        self.payload = real_netcdf_bytes()

    def upload(self, names, collection=None):
        collection = collection or self.alpha
        return self.client.post(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
            {'collection': collection.collection_name,
             'netcdf_files': [SimpleUploadedFile(n, self.payload) for n in names]})

    def test_two_different_names_both_get_a_label(self):
        self.upload(['one.nc', 'two.nc'])

        self.assertEqual(NetCDFFile.objects.count(), 2)
        for name in ('one.xml', 'two.xml'):
            with self.subTest(label=name):
                self.assertTrue(os.path.exists(os.path.join(self.alpha.directory(), name)))

    def test_re_uploading_the_same_name_is_refused(self):
        self.upload(['one.nc'])

        response = self.upload(['one.nc'])

        self.assertEqual(response.status_code, 400)
        self.assertIn('already in', response.json()['error'])
        self.assertEqual(NetCDFFile.objects.count(), 1,
                         'a duplicate row was created for a name that already exists')

    def test_re_uploading_a_name_the_storage_sanitises_is_refused(self):
        """The check compared the raw upload name against the sanitised names on disk.

        "a - Copy.nc" is stored as "a_-_Copy.nc", so the two never matched and a second copy went
        straight through. Storage did not catch it either: the first copy has already been moved
        out of MEDIA_ROOT into the collection by then, so there was no name left to uniquify
        against, and the second upload overwrote the first file and its label.
        """
        self.upload(['a - Copy.nc'])

        response = self.upload(['a - Copy.nc'])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(NetCDFFile.objects.count(), 1,
                         'a second row was created over the top of the first file')
        # The user is told the name they chose, not the one the storage made of it.
        self.assertIn('a - Copy.nc', response.json()['error'])

    def test_two_names_that_sanitise_to_the_same_thing_are_refused(self):
        """They would land on one path on disk, which is the collision the check exists for."""
        response = self.upload(['a - Copy.nc', 'a_-_Copy.nc'])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(NetCDFFile.objects.count(), 0)

    def test_the_same_name_twice_in_one_upload_is_refused(self):
        response = self.upload(['one.nc', 'one.nc'])

        self.assertEqual(response.status_code, 400)
        self.assertEqual(NetCDFFile.objects.count(), 0)

    def test_no_two_records_in_a_collection_ever_share_a_path(self):
        """The invariant the collision broke: one row, one file, one label."""
        self.upload(['one.nc', 'two.nc'])
        self.upload(['one.nc'])       # refused
        self.upload(['three.nc'])

        paths = [os.path.basename(f.file.name)
                 for f in NetCDFFile.objects.filter(collection=self.alpha)]
        self.assertEqual(sorted(paths), sorted(set(paths)), 'two records share a stored path')

    def test_the_same_name_in_a_different_collection_is_fine(self):
        """Different collections are different directories, so there is no collision."""
        self.upload(['one.nc'], collection=self.alpha)
        response = self.upload(['one.nc'], collection=self.beta)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(NetCDFFile.objects.count(), 2)
        self.assertTrue(os.path.exists(os.path.join(self.alpha.directory(), 'one.xml')))
        self.assertTrue(os.path.exists(os.path.join(self.beta.directory(), 'one.xml')))

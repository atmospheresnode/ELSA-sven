# -*- coding: utf-8 -*-
"""Tests for the AMA discipline-metadata feature.

The scoping rule under test throughout: each of Model Metadata, Simulation Configuration and File
Description has a per-collection default that every file in that collection inherits, plus an
optional per-file override. Collections are isolated from each other.
"""

from __future__ import unicode_literals

import os
import re
import shutil
import tempfile

from io import StringIO

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from build import views
from build.forms import (AMA_ENUMERATIONS, FileDescriptionForm, ModelMetadataForm,
                         SimulationConfigurationForm)
from build.models import (AdditionalCollections, Bundle, FileDescription,
                          ModelMetadata, NetCDFFile, Product_Bundle,
                          Product_Collection, SimulationConfiguration)

# views.py binds ET to lxml at the top of the file and then rebinds it to the stdlib ElementTree
# further down, in the NetCDF section. The stdlib one is what actually runs, and the two libraries
# are not interchangeable (their Elements reject each other), so the tests use whatever views is
# using rather than picking a parser of their own.
ET = views.ET

AMA = 'http://pds.nasa.gov/pds4/ama/v1'
PDS = 'http://pds.nasa.gov/pds4/pds/v1'
NS = {
    'xs': 'http://www.w3.org/2001/XMLSchema',
    'pds': PDS,
    'xsi': 'http://www.w3.org/2001/XMLSchema-instance',
    'ama': AMA,
}

TEMPLATE_PE = os.path.join(
    settings.TEMPLATE_DIR, 'pds4_labels', 'base_templates', 'Template_PE.xml')

# A cut-down stand-in for PDS4_AMA_1O00_1300.xsd: enough Variable/Coordinate sequence for the
# harvest to get past the LDD parse without reaching out to pds.nasa.gov.
MINIMAL_LDD = """<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:complexType name="Variable">
    <xs:sequence>
      <xs:element name="variable_name"/>
      <xs:element name="units"/>
    </xs:sequence>
  </xs:complexType>
  <xs:complexType name="Coordinate">
    <xs:sequence>
      <xs:element name="coord_name"/>
      <xs:element name="units"/>
    </xs:sequence>
  </xs:complexType>
</xs:schema>
"""


def ama_child_names(container):
    """Local names of a container's children, in document order."""
    return [child.tag.split('}')[-1] for child in container]


class AMATestCaseMixin(object):
    """A bundle with two collections, so collection isolation is testable everywhere.

    Collection "alpha" holds two files, "beta" holds one.
    """

    def setUp(self):
        super().setUp()
        self.archive_dir = tempfile.mkdtemp(prefix='elsa-ama-test-')
        self.addCleanup(shutil.rmtree, self.archive_dir, True)
        # MEDIA_ROOT too, not just ARCHIVE_DIR: upload tests otherwise write real files into the
        # project's uploads/ directory and leave them behind in the working tree.
        self.media_root = tempfile.mkdtemp(prefix='elsa-ama-media-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        self.settings_patcher = override_settings(
            ARCHIVE_DIR=self.archive_dir, MEDIA_ROOT=self.media_root)
        self.settings_patcher.enable()
        self.addCleanup(self.settings_patcher.disable)

        self.user = User.objects.create_user(
            username='ama_tester', password='pw-for-tests', email='ama@example.com')
        self.other_user = User.objects.create_user(
            username='ama_intruder', password='pw-for-tests', email='intruder@example.com')

        self.bundle = Bundle.objects.create(
            name='ama_test_bundle', user=self.user, version='1800', bundle_type='External')
        os.makedirs(self.bundle.directory(), exist_ok=True)

        self.alpha = self.make_collection('alpha')
        self.beta = self.make_collection('beta')

        self.nc_one = self.make_netcdf('00000.atmos_average.nc', self.alpha)
        self.nc_two = self.make_netcdf('00001.atmos_average.nc', self.alpha)
        self.nc_beta = self.make_netcdf('00002.atmos_average.nc', self.beta)

    def make_collection(self, name):
        collection = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name=name, collection_type='External')
        os.makedirs(collection.directory(), exist_ok=True)
        return collection

    def make_netcdf(self, title, collection):
        return NetCDFFile.objects.create(
            title=title, file=title, bundle=self.bundle, collection=collection, processed=True)

    def write_label(self, netcdf_file):
        """Put a stub .xml beside the data file.

        _bundle_file_tree walks the bundle directory rather than the database, so a file with no
        label on disk produces no tree row at all.
        """
        stem = os.path.splitext(os.path.basename(netcdf_file.file.name))[0]
        path = os.path.join(netcdf_file.directory(), stem + '.xml')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('<?xml version="1.0"?><Product_Observational/>')
        return path

    def login(self):
        self.client.login(username='ama_tester', password='pw-for-tests')

    def panel_get(self, url):
        """GET a URL the way the inline editor does, so the view returns just the panel."""
        return self.client.get(url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def panel_post(self, url, payload=None):
        return self.client.post(url, payload or {}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

    def defaults_url(self, collection=None):
        return reverse('build:ama_collection_defaults', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_collection': (collection or self.alpha).pk})

    def file_url(self, netcdf_file=None):
        return reverse('build:netcdf_ama', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': (netcdf_file or self.nc_one).pk})

    def make_default_simulation(self, collection=None, **overrides):
        values = {
            'horizontal_grid_type': 'lat-lon',
            'model_resolution': '5x5',
            'northern_boundary': 90.0,
            'southern_boundary': -90.0,
            'description': 'Collection default run.',
        }
        values.update(overrides)
        collection = collection or self.alpha
        return SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=collection, netcdf_file=None, **values)

    def make_default_description(self, collection=None, **overrides):
        values = {
            'top_level': 0.01,
            'bottom_level': 700.0,
            'level_unit': 'Pa',
            'postprocessing_methods': 'time:mean(interval=3 hours)',
        }
        values.update(overrides)
        collection = collection or self.alpha
        return FileDescription.objects.create(
            bundle=self.bundle, collection=collection, netcdf_file=None, **values)

    def make_default_model_metadata(self, collection=None, **overrides):
        values = {'name': 'MarsWRF', 'institution': 'NMSU'}
        values.update(overrides)
        collection = collection or self.alpha
        return ModelMetadata.objects.create(
            bundle=self.bundle, collection=collection, netcdf_file=None, **values)


class AMACollectionScopeTests(AMATestCaseMixin, TestCase):
    """The resolution rule the whole feature rests on."""

    def test_every_file_in_a_collection_inherits_its_default(self):
        default = self.make_default_simulation()
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), default)
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_two), default)

    def test_a_file_added_later_inherits_the_existing_default(self):
        """The 'fills out the information for all files added to that collection later' rule."""
        default = self.make_default_simulation()
        latecomer = self.make_netcdf('00003.atmos_average.nc', self.alpha)
        self.assertEqual(SimulationConfiguration.resolve_for_file(latecomer), default)

    def test_collections_do_not_share_values(self):
        alpha_default = self.make_default_simulation(
            collection=self.alpha, horizontal_grid_type='lat-lon')
        self.make_default_simulation(collection=self.beta, horizontal_grid_type='cube-sphere')

        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), alpha_default)
        self.assertEqual(
            SimulationConfiguration.resolve_for_file(self.nc_beta).horizontal_grid_type,
            'cube-sphere')

    def test_a_collection_with_no_default_gets_nothing_from_a_sibling(self):
        self.make_default_simulation(collection=self.alpha)
        self.assertIsNone(SimulationConfiguration.resolve_for_file(self.nc_beta))

    def test_override_wins_over_the_collection_default(self):
        self.make_default_simulation()
        override = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), override)
        self.assertTrue(SimulationConfiguration.resolve_for_file(self.nc_two).is_default())

    def test_all_three_classes_follow_the_same_rule(self):
        for model_class, factory in (
                (ModelMetadata, self.make_default_model_metadata),
                (SimulationConfiguration, self.make_default_simulation),
                (FileDescription, self.make_default_description)):
            with self.subTest(model=model_class.__name__):
                default = factory()
                self.assertEqual(model_class.resolve_for_file(self.nc_one), default)
                self.assertIsNone(model_class.resolve_for_file(self.nc_beta))

    def test_default_lookup_ignores_per_file_rows(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')
        self.assertIsNone(SimulationConfiguration.default_for_collection(self.alpha))

    def test_a_file_with_no_collection_resolves_to_nothing(self):
        orphan = NetCDFFile.objects.create(
            title='orphan.nc', file='orphan.nc', bundle=self.bundle, collection=None)
        self.make_default_simulation()
        self.assertIsNone(SimulationConfiguration.resolve_for_file(orphan))

    def test_deleting_a_file_removes_only_its_override(self):
        default = self.make_default_simulation()
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        self.nc_one.delete()

        self.assertEqual(list(SimulationConfiguration.objects.all()), [default])

    def test_deleting_a_collection_removes_its_ama_rows_and_files(self):
        self.make_default_simulation(collection=self.beta)
        beta_pk = self.beta.pk
        self.beta.delete()

        self.assertEqual(SimulationConfiguration.objects.filter(collection_id=beta_pk).count(), 0)
        self.assertEqual(NetCDFFile.objects.filter(pk=self.nc_beta.pk).count(), 0)


class AMAApplyToCollectionTests(AMATestCaseMixin, TestCase):
    """apply_to_collection is the 'use the same info for the other files' action."""

    def test_promoting_a_file_makes_its_values_the_collection_default(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon')
        source = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        SimulationConfiguration.apply_to_collection(source, self.alpha)

        self.assertEqual(
            SimulationConfiguration.default_for_collection(self.alpha).horizontal_grid_type,
            'cube-sphere')

    def test_it_clears_the_other_files_overrides(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two,
            horizontal_grid_type='icosahedral')
        source = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        cleared = SimulationConfiguration.apply_to_collection(source, self.alpha)

        self.assertTrue(cleared >= 1)
        self.assertEqual(
            SimulationConfiguration.resolve_for_file(self.nc_two).horizontal_grid_type,
            'cube-sphere')

    def test_it_does_not_touch_another_collection(self):
        beta_default = self.make_default_simulation(
            collection=self.beta, horizontal_grid_type='cube-sphere')
        source = self.make_default_simulation(
            collection=self.alpha, horizontal_grid_type='lat-lon')

        SimulationConfiguration.apply_to_collection(source, self.alpha)

        beta_default.refresh_from_db()
        self.assertEqual(beta_default.horizontal_grid_type, 'cube-sphere')


class AMAFilledValuesTests(AMATestCaseMixin, TestCase):
    """filled_values() decides which elements reach the label."""

    def test_blank_and_null_fields_are_dropped(self):
        record = self.make_default_simulation(
            horizontal_grid_type='lat-lon', model_resolution='', vertical_grid_type='   ',
            northern_boundary=None, southern_boundary=None, description='')
        self.assertEqual(record.filled_values(), {'horizontal_grid_type': 'lat-lon'})

    def test_zero_is_kept_because_it_is_a_real_measurement(self):
        record = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, northern_boundary=0.0, model_timestep=0.0)
        self.assertEqual(record.filled_values(),
                         {'model_timestep': '0.0', 'northern_boundary': '0.0'})

    def test_values_come_back_in_ldd_sequence_order(self):
        record = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, description='last in the sequence',
            time_unit='sols', horizontal_grid_type='lat-lon')
        self.assertEqual(list(record.filled_values().keys()),
                         ['horizontal_grid_type', 'time_unit', 'description'])

    def test_model_metadata_drops_blanks_too(self):
        record = ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, name='MarsWRF', type='', version='  ',
            institution='NMSU')
        self.assertEqual(record.filled_values(), {'name': 'MarsWRF', 'institution': 'NMSU'})


class AMALabelWritingTests(AMATestCaseMixin, TestCase):
    """write_ama_user_classes against the real PDS4 template."""

    def parsed_template(self):
        return ET.parse(TEMPLATE_PE).getroot()

    def ama_element(self, root, path):
        element = root.find('.//pds:Context_Area/pds:Discipline_Area/ama:AMA/' + path, namespaces=NS)
        self.assertIsNotNone(element, 'missing {} in template'.format(path))
        return element

    def test_template_ships_with_schema_invalid_empty_elements(self):
        """Guards the premise: every AMA attribute has minLength=1 in the LDD, so the empty
        elements the base template carries are invalid until something clears them."""
        root = self.parsed_template()
        metadata = self.ama_element(root, 'ama:Model_Metadata')
        self.assertEqual(ama_child_names(metadata), ['type', 'name', 'version', 'institution'])
        self.assertTrue(all((child.text or '').strip() == '' for child in metadata))

    def test_unset_classes_leave_no_empty_elements_behind(self):
        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        for path in ('ama:Model_Metadata', 'ama:Simulation_Configuration',
                     'ama:Model_Output/ama:File_Description'):
            with self.subTest(path=path):
                self.assertEqual(len(self.ama_element(root, path)), 0)

    def test_containers_are_preserved_even_when_empty(self):
        """All three are minOccurs=1 inside ama:AMA, so they must survive an empty write."""
        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        ama = root.find('.//pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
        self.assertEqual(ama_child_names(ama),
                         ['Model_Metadata', 'Simulation_Configuration', 'Model_Output'])

    def test_collection_defaults_reach_the_label(self):
        self.make_default_model_metadata(type='GCM')

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        metadata = self.ama_element(root, 'ama:Model_Metadata')
        self.assertEqual(ama_child_names(metadata), ['type', 'name', 'institution'])
        self.assertEqual(metadata.find('ama:name', namespaces=NS).text, 'MarsWRF')

    def test_a_file_in_another_collection_gets_its_own_values(self):
        self.make_default_model_metadata(collection=self.alpha, name='MarsWRF')
        self.make_default_model_metadata(collection=self.beta, name='LMD')

        root_alpha = self.parsed_template()
        views.write_ama_user_classes(root_alpha, NS, self.bundle, netcdf_obj=self.nc_one)
        root_beta = self.parsed_template()
        views.write_ama_user_classes(root_beta, NS, self.bundle, netcdf_obj=self.nc_beta)

        self.assertEqual(
            self.ama_element(root_alpha, 'ama:Model_Metadata').find(
                'ama:name', namespaces=NS).text, 'MarsWRF')
        self.assertEqual(
            self.ama_element(root_beta, 'ama:Model_Metadata').find(
                'ama:name', namespaces=NS).text, 'LMD')

    def test_compass_boundaries_carry_the_required_unit_attribute(self):
        self.make_default_simulation(northern_boundary=45.5, eastern_boundary=180.0)

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        simulation = self.ama_element(root, 'ama:Simulation_Configuration')
        northern = simulation.find('ama:northern_boundary', namespaces=NS)
        self.assertEqual(northern.get('unit'), 'deg')
        self.assertEqual(northern.text, '45.5')
        self.assertIsNone(simulation.find('ama:model_resolution', namespaces=NS).get('unit'))

    def test_simulation_elements_follow_ldd_sequence(self):
        self.make_default_simulation(
            time_unit='sols', model_timestep=30.0, horizontal_grid_type='lat-lon')

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        self.assertEqual(
            ama_child_names(self.ama_element(root, 'ama:Simulation_Configuration')),
            ['horizontal_grid_type', 'model_resolution', 'model_timestep', 'northern_boundary',
             'southern_boundary', 'time_unit', 'description'])

    def test_per_file_override_beats_the_collection_default_in_the_label(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        root_one = self.parsed_template()
        views.write_ama_user_classes(root_one, NS, self.bundle, netcdf_obj=self.nc_one)
        root_two = self.parsed_template()
        views.write_ama_user_classes(root_two, NS, self.bundle, netcdf_obj=self.nc_two)

        self.assertEqual(
            self.ama_element(root_one, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text, 'cube-sphere')
        self.assertEqual(
            self.ama_element(root_two, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text, 'lat-lon')

    def test_file_description_stays_first_child_of_model_output(self):
        """The LDD sequence is File_Description, Variable*, Coordinate*. The harvest appends
        Variables and Coordinates before this writer runs, so filling in place keeps it valid."""
        self.make_default_description()

        root = self.parsed_template()
        model_output = self.ama_element(root, 'ama:Model_Output')
        for tag in ('Variable', 'Coordinate'):
            ET.SubElement(model_output, '{{{}}}{}'.format(AMA, tag))

        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        self.assertEqual(ama_child_names(model_output)[0], 'File_Description')
        self.assertEqual(
            ama_child_names(model_output.find('ama:File_Description', namespaces=NS)),
            ['top_level', 'bottom_level', 'level_unit', 'postprocessing_methods'])

    def test_label_without_an_ama_area_is_left_alone(self):
        root = ET.fromstring(
            '<Product_External xmlns="{}"><Identification_Area/></Product_External>'.format(PDS))
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)
        self.assertEqual(len(root), 1)


class AMAFormTests(AMATestCaseMixin, TestCase):

    def all_form_classes(self):
        return (ModelMetadataForm, SimulationConfigurationForm, FileDescriptionForm)

    def test_every_field_is_optional_because_the_ldd_makes_them_optional(self):
        for form_class in self.all_form_classes():
            with self.subTest(form=form_class.__name__):
                form = form_class(data={}, scope='collection')
                self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_latitude_range_from_the_ldd_is_enforced(self):
        form = SimulationConfigurationForm(data={'northern_boundary': '120'}, scope='collection')
        self.assertFalse(form.is_valid())
        self.assertIn('northern_boundary', form.errors)

    def test_longitude_range_from_the_ldd_is_enforced(self):
        self.assertTrue(SimulationConfigurationForm(
            data={'eastern_boundary': '360'}, scope='collection').is_valid())
        self.assertFalse(SimulationConfigurationForm(
            data={'eastern_boundary': '361'}, scope='collection').is_valid())

    def test_each_scope_gets_only_its_own_control(self):
        """The question genuinely differs: a file asks where its values live, the collection form
        asks whether to discard the per-file ones. Neither control may be posted into the other
        scope, so only one is present at a time."""
        for form_class in self.all_form_classes():
            with self.subTest(form=form_class.__name__, scope='file'):
                fields = form_class(scope='file').fields
                self.assertIn('apply_scope', fields)
                self.assertNotIn('apply_to_collection', fields)
            with self.subTest(form=form_class.__name__, scope='collection'):
                fields = form_class(scope='collection').fields
                self.assertIn('apply_to_collection', fields)
                self.assertNotIn('apply_scope', fields)

    def test_the_file_scope_radio_starts_on_where_the_values_actually_live(self):
        """The old checkbox rendered unchecked however the values were stored, so it never showed
        the current state. The radio has to."""
        following = SimulationConfigurationForm(scope='file', has_override=False)
        self.assertEqual(following.initial['apply_scope'],
                         SimulationConfigurationForm.APPLY_COLLECTION)

        own = SimulationConfigurationForm(scope='file', has_override=True)
        self.assertEqual(own.initial['apply_scope'], SimulationConfigurationForm.APPLY_FILE)

    def test_wants_apply_to_collection_reads_whichever_control_is_present(self):
        collection_form = SimulationConfigurationForm(
            data={'apply_to_collection': 'on'}, scope='collection')
        self.assertTrue(collection_form.wants_apply_to_collection())

        shared = SimulationConfigurationForm(
            data={'apply_scope': 'collection'}, scope='file')
        self.assertTrue(shared.wants_apply_to_collection())
        self.assertFalse(shared.wants_file_scope())

        own = SimulationConfigurationForm(data={'apply_scope': 'file'}, scope='file')
        self.assertFalse(own.wants_apply_to_collection())
        self.assertTrue(own.wants_file_scope())

    def test_start_time_accepts_free_text_model_time(self):
        form = SimulationConfigurationForm(data={'start_time': 'sol 120'}, scope='collection')
        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data['start_time'], 'sol 120')

    def test_field_groups_cover_every_editable_field_exactly_once(self):
        """A field missing from FIELD_GROUPS would silently vanish from the panel, which renders
        groups rather than iterating the form."""
        cases = [
            (SimulationConfigurationForm(scope='file'), SimulationConfiguration),
            (FileDescriptionForm(scope='file'), FileDescription),
            (ModelMetadataForm(scope='file'), ModelMetadata),
        ]
        for form, model_class in cases:
            with self.subTest(form=type(form).__name__):
                grouped = [field.name for group in form.groups() for field in group['fields']]
                self.assertEqual(sorted(grouped), sorted(model_class.ELEMENT_ORDER))
                self.assertEqual(len(grouped), len(set(grouped)), 'a field is grouped twice')

    def test_the_control_fields_are_not_rendered_as_content_fields(self):
        for scope in ('collection', 'file'):
            with self.subTest(scope=scope):
                form = SimulationConfigurationForm(scope=scope)
                grouped = [field.name for group in form.groups() for field in group['fields']]
                self.assertNotIn('apply_to_collection', grouped)
                self.assertNotIn('apply_scope', grouped)

    def test_has_any_value_ignores_the_control_fields(self):
        form = SimulationConfigurationForm(
            data={'apply_to_collection': 'on'}, scope='collection')
        self.assertTrue(form.is_valid())
        self.assertFalse(form.has_any_value())
        self.assertTrue(form.wants_apply_to_collection())

        # A scope radio on its own is not content either: picking "just this file" and filling
        # nothing in still means "inherit", not "store an empty override".
        radio_only = SimulationConfigurationForm(data={'apply_scope': 'file'}, scope='file')
        self.assertTrue(radio_only.is_valid())
        self.assertFalse(radio_only.has_any_value())


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AMAPanelViewTests(AMATestCaseMixin, TestCase):
    """The panel views, with label regeneration stubbed so these stay independent of PDS/xarray."""

    def setUp(self):
        super().setUp()
        self.regenerated = []

        def fake_regenerate(bundle, netcdf_objs=None):
            self.regenerated.append(
                None if netcdf_objs is None else sorted(n.pk for n in netcdf_objs))
            return []

        self.real_regenerate = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = fake_regenerate
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', self.real_regenerate)
        self.login()

    # --- rendering ---------------------------------------------------------------------------

    def test_ajax_returns_only_the_panel_not_a_whole_page(self):
        for url in (self.defaults_url(), self.file_url()):
            with self.subTest(url=url):
                response = self.panel_get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'build/bundle/_ama_panel.html')
                content = response.content.decode('utf-8')
                self.assertIn('id="amaPanelInner"', content)
                self.assertNotIn('<!DOCTYPE html>', content)

    def test_a_direct_visit_still_gets_the_standalone_page(self):
        for url in (self.defaults_url(), self.file_url()):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, 'build/bundle/netcdf_ama.html')

    def test_the_panel_renders_all_three_sections(self):
        content = self.panel_get(self.file_url()).content.decode('utf-8')
        for label in ('Model Metadata', 'Simulation Configuration', 'File Description'):
            with self.subTest(section=label):
                self.assertIn(label, content)
        for prefix, model_class in (('model', ModelMetadata),
                                    ('sim', SimulationConfiguration),
                                    ('desc', FileDescription)):
            for field_name in model_class.ELEMENT_ORDER:
                with self.subTest(field='{}-{}'.format(prefix, field_name)):
                    self.assertIn('name="{}-{}"'.format(prefix, field_name), content)

    def test_the_three_sections_do_not_collide_on_element_ids(self):
        """They share field names (start_time, end_time, time_unit), so without form prefixes the
        ids collide and a <label for> click focuses the wrong section's input."""
        content = self.panel_get(self.file_url()).content.decode('utf-8')

        ids = re.findall(r'\sid="(id_[^"]+)"', content)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        self.assertEqual(duplicates, [])
        for shared in ('start_time', 'end_time', 'time_unit'):
            with self.subTest(field=shared):
                self.assertIn('id="id_sim-{}"'.format(shared), content)
                self.assertIn('id="id_desc-{}"'.format(shared), content)

    def test_a_file_panel_prefills_from_its_collection_default(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon')
        content = self.panel_get(self.file_url()).content.decode('utf-8')

        self.assertIn('data-has-override="false"', content)
        # A select, so "prefilled" means that option is the selected one. The option itself is
        # always present - it is one of the LDD's three - so its mere presence proves nothing.
        self.assertIn('value="lat-lon" selected', content)

    def test_a_file_panel_does_not_prefill_from_another_collection(self):
        self.make_default_simulation(collection=self.alpha, horizontal_grid_type='lat-lon')
        content = self.panel_get(self.file_url(self.nc_beta)).content.decode('utf-8')
        self.assertNotIn('value="lat-lon" selected', content)

    def test_revert_is_offered_only_when_the_file_has_its_own_values(self):
        self.assertNotIn('id="amaRevertForm"',
                         self.panel_get(self.file_url()).content.decode('utf-8'))

        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, level_unit='hPa')
        self.assertIn('id="amaRevertForm"',
                      self.panel_get(self.file_url()).content.decode('utf-8'))

    # --- saving ------------------------------------------------------------------------------

    def test_saving_collection_defaults_stores_all_three_sections(self):
        response = self.panel_post(self.defaults_url(), {
            'model-name': 'MarsWRF',
            'sim-horizontal_grid_type': 'lat-lon',
            'desc-level_unit': 'Pa',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('data-saved="true"', response.content.decode('utf-8'))
        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'MarsWRF')
        self.assertEqual(
            SimulationConfiguration.default_for_collection(self.alpha).horizontal_grid_type,
            'lat-lon')
        self.assertEqual(FileDescription.default_for_collection(self.alpha).level_unit, 'Pa')

    def test_saving_defaults_regenerates_every_file_in_that_collection_only(self):
        self.panel_post(self.defaults_url(), {'model-name': 'MarsWRF'})
        self.assertEqual(self.regenerated, [sorted([self.nc_one.pk, self.nc_two.pk])])

    def test_saving_a_file_creates_an_override_and_regenerates_only_that_file(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon')

        self.panel_post(self.file_url(), {'sim-horizontal_grid_type': 'cube-sphere'})

        override = SimulationConfiguration.objects.get(netcdf_file=self.nc_one)
        self.assertEqual(override.horizontal_grid_type, 'cube-sphere')
        self.assertEqual(override.collection, self.alpha)
        self.assertEqual(self.regenerated, [[self.nc_one.pk]])
        self.assertTrue(SimulationConfiguration.resolve_for_file(self.nc_two).is_default())

    def test_saving_a_file_twice_updates_the_same_override(self):
        self.panel_post(self.file_url(), {'sim-horizontal_grid_type': 'cube-sphere'})
        self.panel_post(self.file_url(), {'sim-horizontal_grid_type': 'icosahedral'})

        overrides = SimulationConfiguration.objects.filter(netcdf_file=self.nc_one)
        self.assertEqual(overrides.count(), 1)
        self.assertEqual(overrides.first().horizontal_grid_type, 'icosahedral')

    def test_clearing_a_file_panel_falls_back_to_the_collection_default(self):
        """An empty override would outrank the default forever, so clearing must delete it."""
        default = self.make_default_simulation(horizontal_grid_type='lat-lon')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        content = self.panel_post(
            self.file_url(), {'sim-horizontal_grid_type': ''}).content.decode('utf-8')

        self.assertEqual(SimulationConfiguration.objects.filter(netcdf_file=self.nc_one).count(), 0)
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), default)
        self.assertIn('data-has-override="false"', content)

    def test_an_all_blank_first_save_does_not_create_a_shadowing_row(self):
        self.panel_post(self.file_url(), {})
        self.assertEqual(SimulationConfiguration.objects.count(), 0)
        self.assertEqual(FileDescription.objects.count(), 0)
        self.assertEqual(ModelMetadata.objects.count(), 0)

    def test_an_invalid_value_saves_nothing_at_all(self):
        response = self.panel_post(self.defaults_url(), {
            'sim-northern_boundary': '120', 'desc-level_unit': 'Pa', 'model-name': 'MarsWRF'})

        self.assertIn('data-saved="false"', response.content.decode('utf-8'))
        self.assertEqual(SimulationConfiguration.objects.count(), 0)
        self.assertEqual(FileDescription.objects.count(), 0,
                         'a failure in one section must not half-save another')
        self.assertEqual(ModelMetadata.objects.count(), 0)
        self.assertEqual(self.regenerated, [])

    # --- apply to collection -----------------------------------------------------------------

    def test_applying_one_section_from_a_file_promotes_only_that_section(self):
        self.make_default_model_metadata(name='MarsWRF')
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

        self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'collection',
            'desc-level_unit': 'Pa', 'desc-apply_scope': 'file',
        })

        # Model Metadata was pushed out to the collection...
        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'LMD')
        self.assertEqual(ModelMetadata.objects.filter(netcdf_file__isnull=False).count(), 0)
        # ...while the other file keeps its own File Description.
        self.assertEqual(FileDescription.objects.get(netcdf_file=self.nc_two).level_unit, 'hPa')

    def test_applying_from_a_file_regenerates_the_whole_collection(self):
        self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'collection'})
        self.assertEqual(self.regenerated, [sorted([self.nc_one.pk, self.nc_two.pk])])

    def test_applying_from_the_defaults_panel_clears_per_file_overrides(self):
        self.make_default_simulation()
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        self.panel_post(self.defaults_url(), {
            'sim-horizontal_grid_type': 'lat-lon', 'sim-apply_to_collection': 'on'})

        self.assertEqual(
            SimulationConfiguration.objects.filter(netcdf_file__isnull=False).count(), 0)
        self.assertEqual(
            SimulationConfiguration.resolve_for_file(self.nc_one).horizontal_grid_type, 'lat-lon')

    def test_apply_to_collection_never_reaches_another_collection(self):
        beta_default = self.make_default_simulation(
            collection=self.beta, horizontal_grid_type='cube-sphere')
        beta_override = SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.beta, netcdf_file=self.nc_beta,
            horizontal_grid_type='icosahedral')

        self.panel_post(self.defaults_url(self.alpha), {
            'sim-horizontal_grid_type': 'lat-lon', 'sim-apply_to_collection': 'on'})

        beta_default.refresh_from_db()
        beta_override.refresh_from_db()
        self.assertEqual(beta_default.horizontal_grid_type, 'cube-sphere')
        self.assertEqual(beta_override.horizontal_grid_type, 'icosahedral')

    def test_saving_without_apply_leaves_other_overrides_alone(self):
        self.make_default_simulation()
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        self.panel_post(self.defaults_url(), {'sim-horizontal_grid_type': 'lat-lon'})

        self.assertEqual(
            SimulationConfiguration.resolve_for_file(self.nc_one).horizontal_grid_type,
            'cube-sphere')

    # --- revert ------------------------------------------------------------------------------

    def test_revert_drops_every_section_for_that_file_only(self):
        for model_class, field in ((ModelMetadata, 'name'),
                                   (SimulationConfiguration, 'horizontal_grid_type'),
                                   (FileDescription, 'level_unit')):
            model_class.objects.create(
                bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
                **{field: 'custom'})
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two,
            horizontal_grid_type='kept')

        self.panel_post(reverse('build:netcdf_ama_reset', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_one.pk}))

        for model_class in (ModelMetadata, SimulationConfiguration, FileDescription):
            with self.subTest(model=model_class.__name__):
                self.assertEqual(model_class.objects.filter(netcdf_file=self.nc_one).count(), 0)
        self.assertEqual(SimulationConfiguration.objects.filter(netcdf_file=self.nc_two).count(), 1)

    # --- authorization -----------------------------------------------------------------------

    def test_all_panel_views_reject_a_different_user(self):
        self.client.logout()
        self.client.login(username='ama_intruder', password='pw-for-tests')

        targets = [
            self.defaults_url(),
            self.file_url(),
            reverse('build:netcdf_ama_reset', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_one.pk}),
        ]
        for url in targets:
            with self.subTest(url=url):
                response = self.panel_post(url, {'model-name': 'MarsWRF'})
                self.assertRedirects(response, reverse('main:restricted_access'),
                                     fetch_redirect_response=False)
        self.assertEqual(ModelMetadata.objects.count(), 0)

    def test_a_collection_from_another_bundle_is_not_reachable(self):
        other_bundle = Bundle.objects.create(
            name='someone_elses_bundle', user=self.user, version='1800')
        stray = AdditionalCollections.objects.create(
            bundle=other_bundle, collection_name='stray', collection_type='External')

        response = self.client.get(reverse('build:ama_collection_defaults', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_collection': stray.pk}))
        self.assertEqual(response.status_code, 404)

    def test_a_file_with_no_collection_is_refused_with_an_explanation(self):
        orphan = NetCDFFile.objects.create(
            title='orphan.nc', file='orphan.nc', bundle=self.bundle, collection=None)

        response = self.client.get(reverse('build:netcdf_ama', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': orphan.pk}))

        self.assertEqual(response.status_code, 302)


class AMAStatusAndFileListTests(AMATestCaseMixin, TestCase):
    """The per-collection data the bundle page renders."""

    def test_file_lists_are_scoped_to_their_collection(self):
        alpha_files = views._netcdf_files_for_collection(self.alpha)
        beta_files = views._netcdf_files_for_collection(self.beta)

        self.assertEqual(sorted(f.pk for f in alpha_files),
                         sorted([self.nc_one.pk, self.nc_two.pk]))
        self.assertEqual([f.pk for f in beta_files], [self.nc_beta.pk])

    def test_override_flag_is_per_file(self):
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

        flags = {f.pk: f.has_ama_override
                 for f in views._netcdf_files_for_collection(self.alpha)}

        self.assertFalse(flags[self.nc_one.pk])
        self.assertTrue(flags[self.nc_two.pk])

    def test_status_counts_filled_fields_per_section(self):
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        status = views._ama_status(self.alpha)

        self.assertEqual(status['model']['filled'], 2)
        self.assertEqual(status['model']['total'], 4)
        self.assertTrue(status['model']['is_set'])
        self.assertFalse(status['sim']['is_set'])
        self.assertEqual(status['model_name'], 'MarsWRF')

    def test_override_count_counts_each_file_once(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, level_unit='hPa')
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='Pa')

        self.assertEqual(views._ama_status(self.alpha)['override_count'], 2)

    def test_status_is_not_polluted_by_another_collection(self):
        self.make_default_model_metadata(collection=self.beta, name='LMD')
        self.assertFalse(views._ama_status(self.alpha)['model']['is_set'])


class AMARegenerationTests(AMATestCaseMixin, TestCase):
    """regenerate_netcdf_labels, with the harvest itself stubbed out."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.processed = []
        self.real_process = views._process_single_netcdf

        def fake_process(bundle, nc_path, collection_directory, ns, allowed_variable_fields,
                         allowed_coord_fields, netcdf_obj=None):
            self.processed.append((nc_path, collection_directory, netcdf_obj))

        views._process_single_netcdf = fake_process
        self.addCleanup(setattr, views, '_process_single_netcdf', self.real_process)

        cache.set('ldd_schema:' + views.AMA_LDD_URL, MINIMAL_LDD.encode('utf-8'), 300)
        self.addCleanup(cache.clear)

    def touch_on_disk(self, netcdf_file):
        path = os.path.join(netcdf_file.directory(), os.path.basename(netcdf_file.file.name))
        with open(path, 'wb') as handle:
            handle.write(b'CDF\x01')
        return path

    def test_files_are_regenerated_from_their_collection_directory(self):
        """The upload files them under the collection, so that is where to look for them."""
        path = self.touch_on_disk(self.nc_one)

        errors = views.regenerate_netcdf_labels(self.bundle, [self.nc_one])

        self.assertEqual(errors, [])
        self.assertEqual(len(self.processed), 1)
        nc_path, collection_directory, netcdf_obj = self.processed[0]
        self.assertEqual(nc_path, path)
        self.assertEqual(collection_directory, self.alpha.directory())
        self.assertEqual(netcdf_obj, self.nc_one)
        self.assertTrue(collection_directory.endswith('alpha'))

    def test_files_missing_from_disk_are_skipped_not_failed(self):
        self.touch_on_disk(self.nc_one)

        errors = views.regenerate_netcdf_labels(self.bundle, [self.nc_one, self.nc_two])

        self.assertEqual(errors, [])
        self.assertEqual(len(self.processed), 1)
        self.nc_two.refresh_from_db()
        self.assertTrue(self.nc_two.processed, 'a skipped file must not be marked as failed')

    def test_a_failing_file_is_reported_without_stopping_the_others(self):
        self.touch_on_disk(self.nc_one)
        self.touch_on_disk(self.nc_two)

        def explode(bundle, nc_path, collection_directory, ns, allowed_variable_fields,
                    allowed_coord_fields, netcdf_obj=None):
            if netcdf_obj.pk == self.nc_one.pk:
                raise ValueError('bad file')
            self.processed.append((nc_path, collection_directory, netcdf_obj))

        views._process_single_netcdf = explode

        errors = views.regenerate_netcdf_labels(self.bundle, [self.nc_one, self.nc_two])

        self.assertEqual(len(errors), 1)
        self.assertIn('bad file', errors[0])
        self.assertEqual(len(self.processed), 1)
        self.nc_one.refresh_from_db()
        self.assertFalse(self.nc_one.processed)

    def test_an_unreachable_ldd_reports_an_error_instead_of_raising(self):
        cache.clear()
        self.touch_on_disk(self.nc_one)

        real_fetch = views._fetch_ldd_content

        def unreachable(url):
            raise IOError('PDS is down')

        views._fetch_ldd_content = unreachable
        self.addCleanup(setattr, views, '_fetch_ldd_content', real_fetch)

        errors = views.regenerate_netcdf_labels(self.bundle, [self.nc_one])

        self.assertEqual(len(errors), 1)
        self.assertIn('data dictionary', errors[0])
        self.assertEqual(self.processed, [])

    def test_a_legacy_file_without_a_collection_uses_the_bundle_directory(self):
        orphan = NetCDFFile.objects.create(
            title='orphan.nc', file='orphan.nc', bundle=self.bundle, collection=None)
        with open(os.path.join(self.bundle.directory(), 'orphan.nc'), 'wb') as handle:
            handle.write(b'CDF\x01')

        views.regenerate_netcdf_labels(self.bundle, [orphan])

        self.assertEqual(len(self.processed), 1)
        self.assertEqual(self.processed[0][1], self.bundle.directory())


class AMAEndToEndLabelTests(AMATestCaseMixin, TestCase):
    """Runs the real harvest over a real NetCDF file and checks the label written to disk."""

    def setUp(self):
        super().setUp()
        try:
            import numpy  # noqa: F401
            import xarray  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on the environment
            self.skipTest('xarray/numpy not available: {}'.format(exc))

        cache.clear()
        cache.set('ldd_schema:' + views.AMA_LDD_URL, MINIMAL_LDD.encode('utf-8'), 300)
        self.addCleanup(cache.clear)

        self.write_netcdf(self.nc_one)

    def write_netcdf(self, netcdf_file):
        import numpy as np
        import xarray as xr

        dataset = xr.Dataset(
            {'temp': (('lat', 'lon'), np.zeros((2, 3), dtype='float32'),
                      {'units': 'K', 'long_name': 'temperature'})},
            coords={'lat': ('lat', np.array([-45.0, 45.0]), {'units': 'degrees_north'}),
                    'lon': ('lon', np.array([0.0, 120.0, 240.0]), {'units': 'degrees_east'})})

        path = os.path.join(netcdf_file.directory(), os.path.basename(netcdf_file.file.name))
        dataset.to_netcdf(path)
        dataset.close()
        return path

    def label_path(self, netcdf_file=None):
        netcdf_file = netcdf_file or self.nc_one
        name = os.path.basename(netcdf_file.file.name)
        if name.endswith('.nc'):
            name = name[:-3]
        return os.path.join(netcdf_file.directory(), name + '.xml')

    def read_label(self, netcdf_file=None):
        return ET.parse(self.label_path(netcdf_file)).getroot()

    def ama_element(self, root, path):
        return root.find('.//pds:Context_Area/pds:Discipline_Area/ama:AMA/' + path, namespaces=NS)

    def test_the_label_is_written_into_the_collection_directory(self):
        self.assertEqual(views.regenerate_netcdf_labels(self.bundle, [self.nc_one]), [])

        self.assertTrue(os.path.exists(self.label_path()))
        self.assertTrue(self.label_path().startswith(self.alpha.directory()))

    def test_collection_values_reach_the_label_alongside_harvested_variables(self):
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        self.make_default_simulation(horizontal_grid_type='lat-lon', northern_boundary=45.0)
        self.make_default_description(level_unit='Pa')

        self.assertEqual(views.regenerate_netcdf_labels(self.bundle, [self.nc_one]), [])
        root = self.read_label()

        metadata = self.ama_element(root, 'ama:Model_Metadata')
        self.assertEqual(ama_child_names(metadata), ['name', 'institution'])
        simulation = self.ama_element(root, 'ama:Simulation_Configuration')
        self.assertEqual(simulation.find('ama:horizontal_grid_type', namespaces=NS).text, 'lat-lon')
        self.assertEqual(simulation.find('ama:northern_boundary', namespaces=NS).get('unit'), 'deg')

        model_output = self.ama_element(root, 'ama:Model_Output')
        child_names = ama_child_names(model_output)
        self.assertEqual(child_names[0], 'File_Description')
        self.assertIn('Variable', child_names)
        variable_names = [v.find('ama:variable_name', namespaces=NS).text
                          for v in model_output.findall('ama:Variable', namespaces=NS)]
        self.assertIn('temp', variable_names)

    def test_editing_after_upload_updates_the_label_on_disk(self):
        """The bug this feature exists to solve: labels were only written at upload time."""
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        self.assertEqual(len(self.ama_element(self.read_label(), 'ama:Simulation_Configuration')), 0)

        self.make_default_simulation(horizontal_grid_type='lat-lon')
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])

        self.assertEqual(
            self.ama_element(self.read_label(), 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text, 'lat-lon')

    def test_regeneration_is_idempotent(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon')

        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        first = open(self.label_path(), encoding='utf-8').read()
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        second = open(self.label_path(), encoding='utf-8').read()

        self.assertEqual(first, second)
        lid = self.read_label().find(
            './/pds:Identification_Area/pds:logical_identifier', namespaces=NS).text
        self.assertEqual(lid.count('00000.atmos_average.nc'), 1)

    def test_no_empty_ama_elements_are_written(self):
        """Every AMA attribute is minLength=1, so an empty element fails schema validation."""
        self.make_default_simulation(horizontal_grid_type='lat-lon')
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])

        root = self.read_label()
        for path in ('ama:Model_Metadata', 'ama:Simulation_Configuration',
                     'ama:Model_Output/ama:File_Description'):
            for child in self.ama_element(root, path):
                with self.subTest(element=child.tag):
                    self.assertTrue((child.text or '').strip())

    def test_two_collections_produce_two_different_labels(self):
        self.write_netcdf(self.nc_beta)
        self.make_default_model_metadata(collection=self.alpha, name='MarsWRF')
        self.make_default_model_metadata(collection=self.beta, name='LMD')

        self.assertEqual(views.regenerate_netcdf_labels(
            self.bundle, [self.nc_one, self.nc_beta]), [])

        self.assertEqual(
            self.ama_element(self.read_label(self.nc_one), 'ama:Model_Metadata').find(
                'ama:name', namespaces=NS).text, 'MarsWRF')
        self.assertEqual(
            self.ama_element(self.read_label(self.nc_beta), 'ama:Model_Metadata').find(
                'ama:name', namespaces=NS).text, 'LMD')

    def test_generated_ama_area_validates_against_the_real_ldd_schema(self):
        """Validates the AMA subtree against PDS4_AMA_1O00_1300.xsd itself.

        Skipped when pds.nasa.gov is unreachable - the schema and the PDS common schema it imports
        are fetched at run time rather than vendored into the repo.
        """
        try:
            import urllib.request

            from lxml import etree as lxml_etree
        except ImportError as exc:  # pragma: no cover
            self.skipTest('lxml not available: {}'.format(exc))

        schema_dir = tempfile.mkdtemp(prefix='elsa-ama-schema-')
        self.addCleanup(shutil.rmtree, schema_dir, True)
        pds_schema_url = 'https://pds.nasa.gov/pds4/pds/v1/PDS4_PDS_1O00.xsd'

        try:
            with urllib.request.urlopen(views.AMA_LDD_URL, timeout=30) as response:
                ama_schema = response.read().decode('utf-8')
            with urllib.request.urlopen(pds_schema_url, timeout=60) as response:
                pds_schema = response.read()
        except Exception as exc:  # pragma: no cover - network dependent
            self.skipTest('PDS schemas unreachable: {}'.format(exc))

        # lxml does not follow the schema's absolute https import, so point it at a local copy.
        with open(os.path.join(schema_dir, 'pds.xsd'), 'wb') as handle:
            handle.write(pds_schema)
        ama_schema_path = os.path.join(schema_dir, 'ama.xsd')
        with open(ama_schema_path, 'w', encoding='utf-8') as handle:
            handle.write(ama_schema.replace(
                'schemaLocation="{}"'.format(pds_schema_url), 'schemaLocation="pds.xsd"'))

        schema = lxml_etree.XMLSchema(lxml_etree.parse(ama_schema_path))

        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        self.make_default_simulation(northern_boundary=45.0, eastern_boundary=180.0)
        self.make_default_description()
        self.assertEqual(views.regenerate_netcdf_labels(self.bundle, [self.nc_one]), [])

        generated = lxml_etree.parse(self.label_path()).getroot().find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
        self.assertTrue(
            schema.validate(lxml_etree.ElementTree(generated)),
            'generated AMA area failed schema validation: {}'.format(schema.error_log))

        template_ama = lxml_etree.parse(TEMPLATE_PE).getroot().find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
        self.assertFalse(
            schema.validate(lxml_etree.ElementTree(template_ama)),
            'the base template was expected to fail minLength=1 on its empty elements')

    def test_saving_through_the_view_rewrites_the_label(self):
        """End to end from an HTTP POST, with nothing stubbed."""
        self.login()
        response = self.client.post(
            self.defaults_url(), {'model-name': 'MarsWRF', 'model-institution': 'NMSU'})

        self.assertEqual(response.status_code, 302)
        metadata = self.ama_element(self.read_label(), 'ama:Model_Metadata')
        self.assertEqual(metadata.find('ama:name', namespaces=NS).text, 'MarsWRF')


class AMATemplateTests(AMATestCaseMixin, TestCase):
    """Renders the bundle page rather than only loading templates, so bad {% url %} tags and
    missing context keys surface as failures."""

    def setUp(self):
        super().setUp()
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def bundle_page(self):
        return self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

    def test_templates_load(self):
        for name in ('build/bundle/bundle.html', 'build/bundle/netcdf_ama.html',
                     'build/bundle/_ama_panel.html', 'build/bundle/_ama_form_fields.html'):
            with self.subTest(template=name):
                self.assertIsNotNone(get_template(name))

    def test_each_collection_lists_only_its_own_files(self):
        content = self.bundle_page()

        # Each file appears once, in its own collection's list.
        self.assertEqual(content.count('value="{}" id="nc_{}"'.format(
            self.nc_one.pk, self.nc_one.pk)), 1)
        self.assertEqual(content.count('value="{}" id="nc_{}"'.format(
            self.nc_beta.pk, self.nc_beta.pk)), 1)

    def test_there_is_exactly_one_metadata_editor_for_the_whole_page(self):
        """Metadata used to be edited in a panel per collection, hundreds of pixels below the
        button that opened it. It is now one modal, filled from whichever label is selected, so a
        bundle with many collections still carries exactly one."""
        content = self.bundle_page()

        self.assertEqual(content.count('id="amaMetadataModal"'), 1)
        self.assertEqual(content.count('id="amaMetadataPanel"'), 1)
        for collection in (self.alpha, self.beta):
            with self.subTest(collection=collection.collection_name):
                self.assertNotIn('id="amaPanel{}"'.format(collection.pk), content)

    def test_the_files_card_is_where_metadata_is_opened_from(self):
        for netcdf_file in (self.nc_one, self.nc_beta):
            self.write_label(netcdf_file)
        content = self.bundle_page()

        self.assertIn('id="editMetadataBtn"', content)
        for netcdf_file in (self.nc_one, self.nc_beta):
            with self.subTest(netcdf_file=netcdf_file.title):
                self.assertIn('data-netcdf-id="{}"'.format(netcdf_file.pk), content)
                self.assertIn(reverse('build:netcdf_ama', kwargs={
                    'pk_bundle': self.bundle.pk, 'pk_netcdf': netcdf_file.pk}), content)

    def test_the_bundle_page_does_not_carry_the_heavy_forms(self):
        """All 28 fields load on demand; rendering them per file is what this layout avoids."""
        content = self.bundle_page()
        self.assertNotIn('name="sim-horizontal_grid_type"', content)
        self.assertNotIn('name="desc-postprocessing_methods"', content)
        self.assertNotIn('name="model-name"', content)

    def test_per_file_ama_buttons_are_not_nested_forms(self):
        """The file list sits inside the bulk-delete form, so a nested form would be invalid HTML
        and a default-type button would submit the delete form."""
        content = self.bundle_page()

        start = content.index('id="bulkDeleteNetCDFForm"')
        end = content.index('</form>', start)
        file_list = content[start:end]

        self.assertNotIn('<form', file_list)
        self.assertNotIn('<button type="submit"', file_list)

    def test_own_metadata_badge_only_shows_for_files_with_an_override(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='cube-sphere')

        self.assertEqual(self.bundle_page().count('Own metadata'), 1)

    def test_upload_cancel_button_is_present_and_wired(self):
        content = self.bundle_page()
        self.assertIn('id="uploadCancelBtn"', content)
        self.assertIn('_netcdfUploadXhr', content)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AMAFormRoundTripTests(AMATestCaseMixin, TestCase):
    """Every AMA attribute, posted through the real panel view and read back.

    The per-section tests elsewhere check behaviour; these check coverage. A field that is declared
    on the model but missing from FIELD_GROUPS, mis-prefixed, or dropped by clean() would still
    pass every other test in this file while silently never reaching a label.
    """

    def setUp(self):
        super().setUp()
        self.login()
        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)

    def sample_for(self, model_class, field_name, index):
        """A valid value for one field, respecting whatever the LDD imposes on it.

        Three attributes are closed vocabularies in the Schematron, so a generated string would be
        rejected - correctly. Pick from the list for those.
        """
        if field_name in AMA_ENUMERATIONS:
            options = AMA_ENUMERATIONS[field_name]
            return options[index % len(options)]
        field = model_class._meta.get_field(field_name)
        if field.get_internal_type() in ('FloatField', 'IntegerField', 'PositiveIntegerField'):
            low, high = None, None
            for validator in field.validators:
                name = type(validator).__name__
                if name == 'MinValueValidator':
                    low = validator.limit_value
                elif name == 'MaxValueValidator':
                    high = validator.limit_value
            value = float(index + 1)
            if low is not None:
                value = max(value, float(low))
            if high is not None:
                value = min(value, float(high))
            return value
        return 'value {}'.format(index)

    def full_payload(self, scope_value=None):
        """One value for all 28 attributes, correctly prefixed."""
        payload = {}
        expected = {}
        index = 0
        for model_class, _form_class, prefix, _label in views.AMA_SECTIONS:
            for field_name in model_class.ELEMENT_ORDER:
                value = self.sample_for(model_class, field_name, index)
                payload['{}-{}'.format(prefix, field_name)] = value
                expected[(model_class, field_name)] = value
                index += 1
            if scope_value is not None:
                payload['{}-apply_scope'.format(prefix)] = scope_value
        return payload, expected

    def assert_all_stored(self, expected, lookup):
        for (model_class, field_name), value in expected.items():
            record = lookup(model_class)
            self.assertIsNotNone(
                record, 'no {} row was stored at all'.format(model_class.__name__))
            stored = getattr(record, field_name)
            with self.subTest(model=model_class.__name__, field=field_name):
                if isinstance(value, float):
                    self.assertAlmostEqual(float(stored), value)
                else:
                    self.assertEqual(stored, value)

    def test_every_attribute_round_trips_in_collection_scope(self):
        payload, expected = self.full_payload()
        response = self.panel_post(self.defaults_url(), payload)
        self.assertEqual(response.status_code, 200)

        self.assert_all_stored(
            expected, lambda model_class: model_class.default_for_collection(self.alpha))

    def test_every_attribute_round_trips_in_file_scope(self):
        payload, expected = self.full_payload(scope_value='file')
        response = self.panel_post(self.file_url(), payload)
        self.assertEqual(response.status_code, 200)

        self.assert_all_stored(
            expected, lambda model_class: model_class.override_for_file(self.nc_one))

    def test_every_attribute_comes_back_in_the_rendered_panel(self):
        """Stored is not the same as shown: a field missing from FIELD_GROUPS would save and then
        never appear again, because the panel renders groups rather than iterating the form."""
        payload, _expected = self.full_payload()
        self.panel_post(self.defaults_url(), payload)

        panel = self.panel_get(self.defaults_url()).content.decode('utf-8')
        for model_class, _form_class, prefix, _label in views.AMA_SECTIONS:
            for field_name in model_class.ELEMENT_ORDER:
                with self.subTest(prefix=prefix, field=field_name):
                    self.assertIn('name="{}-{}"'.format(prefix, field_name), panel)

    def test_the_three_names_that_appear_in_two_sections_do_not_cross_over(self):
        """start_time, end_time and time_unit are attributes of BOTH Simulation Configuration and
        File Description. Without per-section prefixes one would overwrite the other."""
        shared = ('start_time', 'end_time', 'time_unit')
        for name in shared:
            self.assertIn(name, SimulationConfiguration.ELEMENT_ORDER)
            self.assertIn(name, FileDescription.ELEMENT_ORDER)

        self.panel_post(self.defaults_url(), {
            'sim-start_time': 'sim start', 'sim-end_time': 'sim end', 'sim-time_unit': 'sols',
            'desc-start_time': 'desc start', 'desc-end_time': 'desc end', 'desc-time_unit': 'hours',
        })

        sim = SimulationConfiguration.default_for_collection(self.alpha)
        desc = FileDescription.default_for_collection(self.alpha)
        self.assertEqual((sim.start_time, sim.end_time, sim.time_unit),
                         ('sim start', 'sim end', 'sols'))
        self.assertEqual((desc.start_time, desc.end_time, desc.time_unit),
                         ('desc start', 'desc end', 'hours'))

    def test_the_sections_can_be_held_at_different_scopes_in_one_save(self):
        """The whole point of a control per section: shared model, per-file description."""
        self.make_default_model_metadata(name='MarsWRF')

        self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'collection',
            'desc-level_unit': 'Pa', 'desc-apply_scope': 'file',
        })

        # Model Metadata went out to the collection and left no per-file row behind...
        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'LMD')
        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        # ...while File Description stayed on this file alone.
        self.assertIsNone(FileDescription.default_for_collection(self.alpha))
        self.assertEqual(FileDescription.override_for_file(self.nc_one).level_unit, 'Pa')


class AMAScopeControlEdgeCaseTests(AMATestCaseMixin, TestCase):
    """What the scope control does when the POST is not what the panel would have sent."""

    def setUp(self):
        super().setUp()
        self.login()
        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)

    def test_an_omitted_scope_falls_back_to_inheriting(self):
        """A client that posts no radio at all must not be treated as asking for either action.
        The safe reading is the one that changes nothing about where the values live."""
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')

        self.panel_post(self.file_url(), {
            'model-name': 'MarsWRF', 'model-institution': 'NMSU'})

        # Same values as the default and no explicit instruction, so nothing was forked.
        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'MarsWRF')

    def test_an_omitted_scope_still_stores_a_genuine_difference(self):
        self.make_default_model_metadata(name='MarsWRF')

        self.panel_post(self.file_url(), {'model-name': 'LMD'})

        self.assertEqual(ModelMetadata.override_for_file(self.nc_one).name, 'LMD')
        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'MarsWRF')

    def test_a_scope_value_outside_the_choices_is_rejected_and_saves_nothing(self):
        """It must fail the section rather than being read as one of the two real instructions."""
        self.make_default_model_metadata(name='MarsWRF')

        form = ModelMetadataForm(data={'name': 'LMD', 'apply_scope': 'everything'}, scope='file')
        self.assertFalse(form.is_valid())
        self.assertIn('apply_scope', form.errors)

        self.panel_post(self.file_url(), {'model-name': 'LMD', 'model-apply_scope': 'everything'})
        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        self.assertEqual(ModelMetadata.default_for_collection(self.alpha).name, 'MarsWRF')

    def test_a_bad_scope_in_one_section_does_not_save_the_others(self):
        """All three are validated before any is written, so one bad value cannot leave a
        half-saved panel."""
        self.panel_post(self.file_url(), {
            'model-name': 'LMD',
            'sim-horizontal_grid_type': 'lat-lon', 'sim-apply_scope': 'nonsense',
        })

        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        self.assertIsNone(SimulationConfiguration.override_for_file(self.nc_one))


class AMACopyValidationTests(AMATestCaseMixin, TestCase):
    """The copy view's rejections. Each one would otherwise write the wrong thing silently."""

    def setUp(self):
        super().setUp()
        self.login()
        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

    def copy_url(self, netcdf_file=None):
        return reverse('build:netcdf_ama_copy', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': (netcdf_file or self.nc_one).pk})

    def test_an_unknown_section_name_writes_nothing(self):
        self.panel_post(self.copy_url(), {
            'section': 'not_a_section', 'source_netcdf': self.nc_two.pk})

        for model_class, _f, _p, _l in views.AMA_SECTIONS:
            self.assertIsNone(model_class.override_for_file(self.nc_one))

    def test_a_missing_source_writes_nothing(self):
        self.panel_post(self.copy_url(), {'section': 'desc'})
        self.assertIsNone(FileDescription.override_for_file(self.nc_one))

    def test_a_source_id_that_does_not_exist_writes_nothing(self):
        self.panel_post(self.copy_url(), {'section': 'desc', 'source_netcdf': 999999})
        self.assertIsNone(FileDescription.override_for_file(self.nc_one))

    def test_copying_an_empty_section_writes_nothing(self):
        """Copying nothing onto a file would look like a successful copy and quietly do harm if it
        replaced values the file already had."""
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, level_unit='Pa')

        self.panel_post(self.copy_url(), {
            'section': 'model', 'source_netcdf': self.nc_two.pk})

        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        self.assertEqual(FileDescription.override_for_file(self.nc_one).level_unit, 'Pa')

    def test_a_get_does_not_copy(self):
        self.client.get(self.copy_url())
        self.assertIsNone(FileDescription.override_for_file(self.nc_one))

    def test_copying_replaces_the_target_section_rather_than_merging_into_it(self):
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            level_unit='Pa', top_level=9.0)

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_two.pk})

        copied = FileDescription.override_for_file(self.nc_one)
        self.assertEqual(copied.level_unit, 'hPa')
        # The source has no top_level, so the target must not keep its old one.
        self.assertIsNone(copied.top_level)



class AMAEveryFieldReachesTheLabelTests(AMATestCaseMixin, TestCase):
    """The last link in the chain: a value entered in the form ends up in the XML.

    The round-trip tests prove a value is stored and shown again. This proves it is written into
    the label, which is the only thing the archive ever sees. A field that is stored but never
    serialised would pass everything else in this file.
    """

    def sample_for(self, model_class, field_name, index):
        if field_name in AMA_ENUMERATIONS:
            options = AMA_ENUMERATIONS[field_name]
            return options[index % len(options)]
        field = model_class._meta.get_field(field_name)
        if field.get_internal_type() in ('FloatField', 'IntegerField', 'PositiveIntegerField'):
            low, high = None, None
            for validator in field.validators:
                name = type(validator).__name__
                if name == 'MinValueValidator':
                    low = validator.limit_value
                elif name == 'MaxValueValidator':
                    high = validator.limit_value
            value = float(index + 1)
            if low is not None:
                value = max(value, float(low))
            if high is not None:
                value = min(value, float(high))
            return value
        return 'value {}'.format(index)

    def written_label(self):
        root = ET.parse(TEMPLATE_PE).getroot()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)
        return root

    def container(self, root, path):
        return root.find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA/' + path, namespaces=NS)

    CONTAINERS = {
        'model': 'ama:Model_Metadata',
        'sim': 'ama:Simulation_Configuration',
        'desc': 'ama:Model_Output/ama:File_Description',
    }

    def fill_everything(self, netcdf_file=None):
        """One value per attribute, written straight to the models."""
        expected = {}
        index = 0
        for model_class, _form_class, prefix, _label in views.AMA_SECTIONS:
            values = {}
            for field_name in model_class.ELEMENT_ORDER:
                value = self.sample_for(model_class, field_name, index)
                values[field_name] = value
                expected[(prefix, field_name)] = value
                index += 1
            model_class.objects.create(
                bundle=self.bundle, collection=self.alpha, netcdf_file=netcdf_file, **values)
        return expected

    def test_every_attribute_is_written_into_the_label(self):
        expected = self.fill_everything()
        root = self.written_label()

        for (prefix, field_name), value in expected.items():
            container = self.container(root, self.CONTAINERS[prefix])
            element = container.find('ama:' + field_name, namespaces=NS)
            with self.subTest(section=prefix, field=field_name):
                self.assertIsNotNone(
                    element, '{} never reached the label'.format(field_name))
                text = (element.text or '').strip()
                self.assertNotEqual(text, '', '{} was written empty'.format(field_name))
                if isinstance(value, float):
                    self.assertAlmostEqual(float(text), value)
                else:
                    self.assertEqual(text, value)

    def test_the_label_carries_no_attribute_the_models_do_not_declare(self):
        """A stray element would be schema-invalid, and the LDD is the authority on the set."""
        self.fill_everything()
        root = self.written_label()

        for model_class, _form_class, prefix, _label in views.AMA_SECTIONS:
            container = self.container(root, self.CONTAINERS[prefix])
            written = [child.tag.split('}')[-1] for child in container]
            with self.subTest(section=prefix):
                self.assertEqual(sorted(written), sorted(model_class.ELEMENT_ORDER))

    def test_element_order_follows_the_ldd_sequence(self):
        """The AMA classes are xs:sequence, so order is part of validity, not presentation."""
        self.fill_everything()
        root = self.written_label()

        for model_class, _form_class, prefix, _label in views.AMA_SECTIONS:
            container = self.container(root, self.CONTAINERS[prefix])
            written = [child.tag.split('}')[-1] for child in container]
            with self.subTest(section=prefix):
                self.assertEqual(written, list(model_class.ELEMENT_ORDER))

    def test_a_per_file_value_beats_the_default_for_every_attribute(self):
        self.fill_everything()
        overrides = {}
        index = 500
        for model_class, _form_class, prefix, _label in views.AMA_SECTIONS:
            values = {}
            for field_name in model_class.ELEMENT_ORDER:
                value = self.sample_for(model_class, field_name, index)
                values[field_name] = value
                overrides[(prefix, field_name)] = value
                index += 1
            model_class.objects.filter(netcdf_file=self.nc_one).delete()
            model_class.objects.create(
                bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, **values)

        root = self.written_label()
        for (prefix, field_name), value in overrides.items():
            container = self.container(root, self.CONTAINERS[prefix])
            element = container.find('ama:' + field_name, namespaces=NS)
            with self.subTest(section=prefix, field=field_name):
                text = (element.text or '').strip()
                if isinstance(value, float):
                    self.assertAlmostEqual(float(text), value)
                else:
                    self.assertEqual(text, value)



class AMAEnumeratedFieldTests(AMATestCaseMixin, TestCase):
    """The three attributes the AMA Schematron restricts to a closed vocabulary.

    The constraint is NOT in the XSD - PDS4_AMA_1O00_1300.xsd contains no xs:enumeration at all -
    so lxml schema validation cannot catch a wrong value. It surfaces only when the archive is
    validated with the Schematron, long after the user has moved on. Offering the list is the only
    place the error can be prevented.
    """

    EXPECTED = {
        'type': ('ASSIMILATION', 'GCM', 'MESOSCALE'),
        'horizontal_grid_type': ('cube-sphere', 'icosahedral', 'lat-lon'),
        'vertical_grid_type': ('Altitude', 'Hybrid-Sigma', 'Isentropic', 'Pressure', 'Sigma'),
    }

    def test_the_vocabularies_match_the_ldd(self):
        """Transcribed from PDS4_AMA_1O00_1300.sch. If the LDD is upgraded these must be rechecked,
        and this test is what will say so."""
        self.assertEqual({k: tuple(v) for k, v in AMA_ENUMERATIONS.items()}, self.EXPECTED)

    def test_each_one_is_a_select_not_a_free_text_box(self):
        cases = [(ModelMetadataForm, 'type'),
                 (SimulationConfigurationForm, 'horizontal_grid_type'),
                 (SimulationConfigurationForm, 'vertical_grid_type')]
        for form_class, field_name in cases:
            with self.subTest(field=field_name):
                field = form_class(scope='collection').fields[field_name]
                self.assertTrue(hasattr(field, 'choices'), '{} is still free text'.format(field_name))
                offered = [value for value, _label in field.choices if value]
                self.assertEqual(offered, list(self.EXPECTED[field_name]))

    def test_a_value_outside_the_vocabulary_is_refused(self):
        """These are exactly the values the old placeholders told users to type."""
        cases = [
            (ModelMetadataForm, 'type', 'General Circulation Model'),
            (SimulationConfigurationForm, 'horizontal_grid_type', 'lat/lon'),
            (SimulationConfigurationForm, 'vertical_grid_type', 'sigma'),
        ]
        for form_class, field_name, bad_value in cases:
            with self.subTest(field=field_name, value=bad_value):
                form = form_class(data={field_name: bad_value}, scope='collection')
                self.assertFalse(form.is_valid(), '{} accepted {!r}'.format(field_name, bad_value))
                self.assertIn(field_name, form.errors)

    def test_the_valid_spellings_are_accepted(self):
        cases = [(ModelMetadataForm, 'type', 'GCM'),
                 (SimulationConfigurationForm, 'horizontal_grid_type', 'lat-lon'),
                 (SimulationConfigurationForm, 'vertical_grid_type', 'Sigma')]
        for form_class, field_name, good in cases:
            with self.subTest(field=field_name, value=good):
                form = form_class(data={field_name: good}, scope='collection')
                self.assertTrue(form.is_valid(), form.errors.as_text())
                self.assertEqual(form.cleaned_data[field_name], good)

    def test_blank_is_still_allowed(self):
        """They are minOccurs=0 in the XSD, so leaving one unset stays valid."""
        form = ModelMetadataForm(data={'name': 'MarsWRF'}, scope='collection')
        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data['type'], '')

    def test_a_value_stored_before_the_list_existed_is_kept_and_flagged(self):
        """Rows written while these were text boxes may hold anything. Dropping such a value on
        the next save would destroy the user's data without telling them."""
        legacy = ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            type='General Circulation Model')

        form = ModelMetadataForm(instance=legacy, scope='collection')
        labels = dict(form.fields['type'].choices)
        self.assertIn('General Circulation Model', labels)
        self.assertIn('not a valid PDS4 value', labels['General Circulation Model'])

    def test_the_flagged_legacy_value_can_still_be_replaced_with_a_valid_one(self):
        legacy = ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            type='General Circulation Model')

        form = ModelMetadataForm(data={'type': 'GCM'}, instance=legacy, scope='collection')
        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.save(commit=False).type, 'GCM')



def parse_schematron_enumerations(sch_text):
    """Pull the "value must be one of" rules out of a PDS4 Schematron file.

    This is NOT a Schematron engine. The AMA dictionary's .sch declares queryBinding="xslt2" and
    lxml's isoschematron refuses those outright ("does not work with schemas using the xslt2 query
    language"), libxslt being XSLT 1.0 only; there is no Saxon on this machine either. So rather
    than transcribing the vocabularies into the test - which would only prove the transcription
    matches itself - this reads them back out of the live file.

    It understands the two assertion shapes the AMA dictionary actually uses:

        <sch:assert test=". = ('A', 'B')">          the element's own text
        <sch:assert test="@unit = ('deg', 'rad')">  the element's unit attribute

    Everything else in the file is ignored, which the tests using it say out loud.

    Returns (namespaces, rules) where each rule is (context_path, 'text'|'unit', frozenset).
    """
    import re as _re

    namespaces = dict(_re.findall(
        r'<sch:ns\s+uri="([^"]+)"\s+prefix="([^"]+)"\s*/>', sch_text))
    namespaces = {prefix: uri for uri, prefix in namespaces.items()}

    rules = []
    for context, body in _re.findall(
            r'<sch:rule\s+context="([^"]+)">(.*?)</sch:rule>', sch_text, _re.S):
        for test in _re.findall(r'<sch:assert\s+test="([^"]+)"', body):
            match = _re.match(r"^(\.|@unit)\s*=\s*\((.*)\)$", test.strip())
            if not match:
                continue
            target = 'text' if match.group(1) == '.' else 'unit'
            allowed = frozenset(_re.findall(r"'([^']*)'", match.group(2)))
            if allowed:
                rules.append((context, target, allowed))
    return namespaces, rules


class AMASchematronTests(AMATestCaseMixin, TestCase):
    """Checks ELSA's generated labels against the LDD's Schematron, not just its XSD.

    This is the gap that let three fields ship wrong. PDS4 validates in two layers, and the AMA
    XSD contains no xs:enumeration at all - every closed vocabulary lives only in the .sch. The
    existing XSD test uses lxml XMLSchema and therefore cannot see this class of error at any
    severity.

    The .sch is fetched at run time, like the XSD test beside it, and these skip when
    pds.nasa.gov is unreachable.
    """

    def schematron(self):
        import urllib.request

        # Derived from AMA_LDD_URL by convention: PDS publishes the Schematron beside the schema
        # under the same stem. The codebase only names the .xsd.
        sch_url = views.AMA_LDD_URL.rsplit('.', 1)[0] + '.sch'
        try:
            with urllib.request.urlopen(sch_url, timeout=30) as response:
                return response.read().decode('utf-8')
        except Exception as exc:  # pragma: no cover - network dependent
            self.skipTest('AMA Schematron unreachable at {}: {}'.format(sch_url, exc))

    def written_label(self):
        root = ET.parse(TEMPLATE_PE).getroot()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)
        return root

    def violations(self, root, namespaces, rules):
        """Every place the generated label breaks one of the enumeration rules."""
        from lxml import etree as lxml_etree

        tree = lxml_etree.fromstring(ET.tostring(root))
        found = []
        for context, target, allowed in rules:
            for node in tree.xpath('//' + context, namespaces=namespaces):
                value = (node.text or '').strip() if target == 'text' else node.get('unit')
                if value in (None, ''):
                    continue
                if value not in allowed:
                    found.append((context, target, value, sorted(allowed)))
        return found

    def test_the_form_vocabularies_match_the_published_schematron(self):
        """The one that matters: ELSA's lists are checked against the LDD, not against a copy of
        themselves. If PDS revises the dictionary, this is what will say so."""
        namespaces, rules = parse_schematron_enumerations(self.schematron())
        del namespaces

        published = {}
        for context, target, allowed in rules:
            if target != 'text':
                continue
            attribute = context.rsplit('/', 1)[-1].split(':')[-1]
            published[attribute] = allowed

        for field_name, offered in AMA_ENUMERATIONS.items():
            with self.subTest(field=field_name):
                self.assertIn(field_name, published,
                              '{} is no longer an enumerated attribute in the LDD'.format(field_name))
                self.assertEqual(
                    set(offered), set(published[field_name]),
                    'the form offers {} but the LDD allows {}'.format(
                        sorted(offered), sorted(published[field_name])))

    def test_a_label_built_from_the_forms_offered_values_passes(self):
        namespaces, rules = parse_schematron_enumerations(self.schematron())

        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            type='GCM', name='MarsWRF', institution='NMSU')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat-lon', vertical_grid_type='Sigma',
            northern_boundary=45.0, southern_boundary=-45.0,
            eastern_boundary=180.0, western_boundary=0.0)

        self.assertEqual(self.violations(self.written_label(), namespaces, rules), [])

    def test_the_old_free_text_placeholders_would_have_failed(self):
        """The values the removed placeholders told users to type. This is the regression: they
        passed XSD validation and would only have failed at submission."""
        namespaces, rules = parse_schematron_enumerations(self.schematron())

        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            type='General Circulation Model')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon', vertical_grid_type='sigma')

        broken = {value for _c, _t, value, _a in
                  self.violations(self.written_label(), namespaces, rules)}
        self.assertEqual(broken, {'General Circulation Model', 'lat/lon', 'sigma'})

    def test_the_boundary_units_elsa_writes_are_accepted(self):
        """ELSA hardcodes unit="deg" on the four compass boundaries; the Schematron restricts it."""
        namespaces, rules = parse_schematron_enumerations(self.schematron())

        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            northern_boundary=90.0, southern_boundary=-90.0,
            eastern_boundary=360.0, western_boundary=-180.0)

        unit_problems = [v for v in self.violations(self.written_label(), namespaces, rules)
                         if v[1] == 'unit']
        self.assertEqual(unit_problems, [])

    def test_an_empty_label_breaks_no_enumeration_rule(self):
        """All 28 attributes are minOccurs="0", so a label carrying none of them is valid."""
        namespaces, rules = parse_schematron_enumerations(self.schematron())
        self.assertEqual(self.violations(self.written_label(), namespaces, rules), [])

    def test_the_checker_reads_real_rules_rather_than_finding_none(self):
        """Guards the premise. If the .sch format changed and the parse silently returned nothing,
        every test above would pass while checking absolutely nothing."""
        namespaces, rules = parse_schematron_enumerations(self.schematron())

        self.assertIn('ama', namespaces)
        self.assertGreaterEqual(len(rules), 10, 'the Schematron parse found almost no rules')
        contexts = {context for context, _t, _a in rules}
        for expected in ('ama:Model_Metadata/ama:type',
                         'ama:Simulation_Configuration/ama:horizontal_grid_type',
                         'ama:Simulation_Configuration/ama:vertical_grid_type'):
            self.assertIn(expected, contexts)



class AMAVocabularyCheckCommandTests(AMATestCaseMixin, TestCase):
    """The scan that says how far the bad-vocabulary problem spreads in existing data."""

    def setUp(self):
        super().setUp()
        self.regenerated = []

        def fake_regenerate(bundle, netcdf_objs=None):
            self.regenerated.append(
                None if netcdf_objs is None else sorted(n.pk for n in netcdf_objs))
            return []

        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = fake_regenerate
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)
        # The command imports the name directly, so the module attribute has to be swapped too.
        import build.management.commands.ama_vocabulary_check as command_module
        self.command_module = command_module
        real_cmd = command_module.regenerate_netcdf_labels
        command_module.regenerate_netcdf_labels = fake_regenerate
        self.addCleanup(setattr, command_module, 'regenerate_netcdf_labels', real_cmd)

    def run_command(self, *args):
        out = StringIO()
        err = StringIO()
        call_command('ama_vocabulary_check', *args, stdout=out, stderr=err)
        return out.getvalue()

    def test_a_clean_database_reports_nothing(self):
        self.make_default_model_metadata(type='GCM')
        self.assertIn('No non-conforming AMA values found', self.run_command())

    def test_a_conforming_value_is_not_reported(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat-lon', vertical_grid_type='Sigma')
        self.assertIn('No non-conforming AMA values found', self.run_command())

    def test_it_names_the_bundle_the_collection_and_the_bad_value(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon')

        output = self.run_command()
        self.assertIn(self.bundle.name, output)
        self.assertIn('alpha', output)
        self.assertIn("'lat/lon'", output)
        self.assertIn('collection default', output)
        self.assertIn('cube-sphere', output)  # the allowed list is shown

    def test_it_distinguishes_a_per_file_value_from_a_collection_default(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            vertical_grid_type='sigma')

        self.assertIn('file {}'.format(self.nc_one.title), self.run_command())

    def test_a_spelling_only_difference_is_offered_as_a_fix(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon')

        output = self.run_command()
        self.assertIn('spelling only', output)
        self.assertIn('use --fix', output)

    def test_a_value_needing_judgement_is_suggested_but_never_auto_applied(self):
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            type='General Circulation Model')

        output = self.run_command('--fix')
        self.assertIn('NOT applied', output)
        self.assertIn("'GCM'", output)
        # Still exactly as the user left it.
        self.assertEqual(
            ModelMetadata.default_for_collection(self.alpha).type,
            'General Circulation Model')

    def test_a_value_with_no_match_at_all_says_it_needs_a_person(self):
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None, type='whatever')
        self.assertIn('needs a person', self.run_command())

    def test_fix_normalises_case_and_separators(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon', vertical_grid_type='sigma')

        self.run_command('--fix')

        record = SimulationConfiguration.default_for_collection(self.alpha)
        self.assertEqual(record.horizontal_grid_type, 'lat-lon')
        self.assertEqual(record.vertical_grid_type, 'Sigma')

    def test_fix_rebuilds_every_label_a_collection_default_reaches(self):
        """A default is inherited, so correcting it makes every file's label in that collection
        stale - not only the row that changed."""
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon')

        self.run_command('--fix')

        self.assertEqual(self.regenerated, [sorted([self.nc_one.pk, self.nc_two.pk])])

    def test_fix_of_a_per_file_value_rebuilds_only_that_file(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one,
            horizontal_grid_type='lat/lon')

        self.run_command('--fix')

        self.assertEqual(self.regenerated, [[self.nc_one.pk]])

    def test_a_report_without_fix_changes_nothing_and_rebuilds_nothing(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon')

        self.run_command()

        self.assertEqual(
            SimulationConfiguration.default_for_collection(self.alpha).horizontal_grid_type,
            'lat/lon')
        self.assertEqual(self.regenerated, [])

    def test_strict_exits_non_zero_while_anything_remains(self):
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            type='General Circulation Model')

        with self.assertRaises(CommandError):
            self.run_command('--strict')

    def test_strict_is_quiet_once_fix_has_cleared_everything(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None,
            horizontal_grid_type='lat/lon')

        self.run_command('--fix', '--strict')

    def test_a_blank_value_is_not_a_violation(self):
        """All three are minOccurs="0", so unset is valid and must not be reported."""
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=None, type='', name='MarsWRF')
        self.assertIn('No non-conforming AMA values found', self.run_command())



class AMASaveMessageTests(AMATestCaseMixin, TestCase):
    """What a save tells the user afterwards.

    A save used to emit one message per section plus one for the labels, so sharing two sections
    stacked three full-width alerts across the top of the page - and the per-section one counted
    the file being edited, announcing "1 file reset to the collection values" when nobody else's
    values had been touched.
    """

    def setUp(self):
        super().setUp()
        self.login()
        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)

    def messages_from(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]

    def test_a_plain_save_says_one_thing(self):
        response = self.panel_post(self.file_url(), {'model-name': 'MarsWRF'})
        self.assertEqual(self.messages_from(response), ['Saved.'])

    def test_sharing_two_sections_still_says_one_thing(self):
        response = self.panel_post(self.file_url(), {
            'model-name': 'MarsWRF', 'model-apply_scope': 'collection',
            'sim-horizontal_grid_type': 'lat-lon', 'sim-apply_scope': 'collection',
        })
        self.assertEqual(len(self.messages_from(response)), 1)

    def test_it_does_not_count_the_file_being_edited_as_a_file_it_reset(self):
        """The bug: promoting your own file's values reported your own file as having been reset."""
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, name='LMD')

        response = self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'collection'})

        message = self.messages_from(response)[0]
        self.assertNotIn('replacing', message)
        self.assertEqual(
            message, 'Saved. Model Metadata now applies to every file in alpha.')

    def test_it_does_say_so_when_another_file_loses_its_own_values(self):
        """The one outcome a user might not have intended is worth stating plainly."""
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, name='other')

        response = self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'collection'})

        self.assertEqual(
            self.messages_from(response)[0],
            'Saved. Model Metadata now applies to every file in alpha, replacing the values '
            '1 other file had of its own.')

    def test_the_sentence_agrees_with_itself_for_several_sections_and_files(self):
        for netcdf_file in (self.nc_two,):
            ModelMetadata.objects.create(
                bundle=self.bundle, collection=self.alpha, netcdf_file=netcdf_file, name='a')
            SimulationConfiguration.objects.create(
                bundle=self.bundle, collection=self.alpha, netcdf_file=netcdf_file,
                horizontal_grid_type='lat-lon')

        response = self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'collection',
            'sim-horizontal_grid_type': 'icosahedral', 'sim-apply_scope': 'collection'})

        message = self.messages_from(response)[0]
        self.assertIn('Model Metadata and Simulation Configuration now apply', message)
        self.assertIn('1 other file had of its own', message)

    def test_a_save_that_shares_nothing_explains_nothing(self):
        """Values staying where they already were needs no sentence."""
        response = self.panel_post(self.file_url(), {
            'model-name': 'LMD', 'model-apply_scope': 'file'})
        self.assertEqual(self.messages_from(response), ['Saved.'])

    def test_a_label_failure_is_still_reported(self):
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: ['disk full']

        response = self.panel_post(self.file_url(), {'model-name': 'MarsWRF'})

        message = self.messages_from(response)[0]
        self.assertIn('could not be refreshed', message)
        self.assertIn('disk full', message)

    def test_a_copy_says_one_thing_too(self):
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

        response = self.panel_post(
            reverse('build:netcdf_ama_copy', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_one.pk}),
            {'section': 'desc', 'source_netcdf': self.nc_two.pk})

        self.assertEqual(
            self.messages_from(response),
            ['File Description copied from "{}".'.format(self.nc_two.title)])



class AMAFileTreeTests(AMATestCaseMixin, TestCase):
    """The Files card's per-label metadata reading, which is how a user sees that a label is thin."""

    def entry_for(self, netcdf_file, bundle=None):
        tree = views._bundle_file_tree(bundle or self.bundle)
        stem = os.path.splitext(os.path.basename(netcdf_file.file.name))[0]
        for folder in tree['folders']:
            for label in folder['labels']:
                if label['name'] == stem + '.xml':
                    return label
        return None

    def test_a_label_with_no_ama_reads_zero_of_the_full_field_count(self):
        self.write_label(self.nc_one)
        entry = self.entry_for(self.nc_one)

        self.assertEqual(entry['netcdf_id'], self.nc_one.pk)
        self.assertEqual(entry['ama_filled'], 0)
        # 4 + 17 + 7. Asserted against the models rather than the literal so adding a field to an
        # AMA class moves both sides together.
        self.assertEqual(entry['ama_total'], sum(
            len(model_class.ELEMENT_ORDER)
            for model_class, _f, _p, _l in views.AMA_SECTIONS))

    def test_a_file_with_no_values_of_its_own_counts_what_it_inherits(self):
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        self.write_label(self.nc_one)

        self.assertEqual(self.entry_for(self.nc_one)['ama_filled'], 2)

    def test_an_override_is_counted_instead_of_the_default_not_as_well_as(self):
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, name='LMD')
        self.write_label(self.nc_one)

        # The override supplies one value, so the inherited two must not be added on top.
        self.assertEqual(self.entry_for(self.nc_one)['ama_filled'], 1)

    def test_counts_are_summed_across_all_three_sections(self):
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        self.make_default_description(level_unit='Pa', top_level=0.01, bottom_level=700.0,
                                      postprocessing_methods='')
        self.write_label(self.nc_one)

        self.assertEqual(self.entry_for(self.nc_one)['ama_filled'], 5)

    def test_an_archive_bundle_gets_no_reading_and_no_extra_queries(self):
        """AMA is External-only. An Archive bundle must not pay for the lookup, and must not show
        a metadata reading the user has no way to act on."""
        archive = Bundle.objects.create(
            name='archive_bundle', user=self.user, version='1800', bundle_type='Archive')
        os.makedirs(archive.directory(), exist_ok=True)
        collection = AdditionalCollections.objects.create(
            bundle=archive, collection_name='data', collection_type='Data')
        os.makedirs(collection.directory(), exist_ok=True)
        netcdf_file = NetCDFFile.objects.create(
            title='00000.atmos_average.nc', file='00000.atmos_average.nc',
            bundle=archive, collection=collection, processed=True)
        self.write_label(netcdf_file)

        entry = self.entry_for(netcdf_file, bundle=archive)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry['ama_total'])
        self.assertIsNone(entry['netcdf_id'])

    def test_a_file_with_no_collection_gets_no_reading(self):
        """It has nothing to inherit from and no editor to open, so "0 of 28" would be a lie."""
        orphan = NetCDFFile.objects.create(
            title='99999.atmos_average.nc', file='99999.atmos_average.nc',
            bundle=self.bundle, collection=None, processed=True)
        stem = '99999.atmos_average'
        with open(os.path.join(self.bundle.directory(), stem + '.xml'), 'w') as handle:
            handle.write('<?xml version="1.0"?><Product_Observational/>')

        tree = views._bundle_file_tree(self.bundle)
        entry = next(
            (label for label in tree['root_labels'] if label['name'] == stem + '.xml'), None)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry['ama_total'])
        self.assertIsNone(entry['netcdf_id'])
        del orphan


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AMAScopeRadioTests(AMATestCaseMixin, TestCase):
    """The per-section scope control, and the one save rule that only it makes possible."""

    def setUp(self):
        super().setUp()
        self.login()
        self.regenerated = []

        def fake_regenerate(bundle, netcdf_objs=None):
            self.regenerated.append(
                None if netcdf_objs is None else sorted(n.pk for n in netcdf_objs))
            return []

        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = fake_regenerate
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)

    def test_choosing_just_this_file_stores_an_override_even_when_it_matches_the_default(self):
        """Without this the radio springs back to "same for every file" whenever the values happen
        to agree, which reads as the setting not sticking."""
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')

        self.panel_post(self.file_url(), {
            'model-name': 'MarsWRF', 'model-institution': 'NMSU',
            'model-apply_scope': 'file',
        })

        override = ModelMetadata.override_for_file(self.nc_one)
        self.assertIsNotNone(override)
        self.assertEqual(override.name, 'MarsWRF')

    def test_leaving_it_shared_does_not_fork_the_file_off_the_default(self):
        """The panel is pre-filled from the default, so submitting it unchanged posts the same
        values back. That must stay inheritance, not a silent fork."""
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')

        self.panel_post(self.file_url(), {
            'model-name': 'MarsWRF', 'model-institution': 'NMSU',
            'model-apply_scope': 'collection',
        })

        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))

    def test_an_empty_section_still_inherits_whatever_the_radio_says(self):
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, name='LMD')

        self.panel_post(self.file_url(), {'model-apply_scope': 'file'})

        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        self.assertEqual(ModelMetadata.resolve_for_file(self.nc_one).name, 'MarsWRF')

    def test_the_radio_comes_back_selected_on_where_the_values_live(self):
        self.make_default_model_metadata(name='MarsWRF')
        ModelMetadata.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, name='LMD')

        content = self.panel_get(self.file_url()).content.decode('utf-8')

        # Model Metadata is stored on the file, so its radio sits on "just this file"; the two
        # sections the file has no values for sit on "same for every file".
        self.assertRegex(content, r'name="model-apply_scope" value="file"[^>]*checked')
        self.assertRegex(content, r'name="sim-apply_scope" value="collection"[^>]*checked')
        self.assertRegex(content, r'name="desc-apply_scope" value="collection"[^>]*checked')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AMACopyFromFileTests(AMATestCaseMixin, TestCase):
    """Copy-from-another-file: the concrete counterpart to collection inheritance."""

    def setUp(self):
        super().setUp()
        self.login()

        real = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', real)

    def copy_url(self, netcdf_file=None):
        return reverse('build:netcdf_ama_copy', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': (netcdf_file or self.nc_one).pk})

    def test_it_copies_one_section_from_a_sibling(self):
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two,
            level_unit='hPa', top_level=0.5)

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_two.pk})

        copied = FileDescription.override_for_file(self.nc_one)
        self.assertIsNotNone(copied)
        self.assertEqual(copied.level_unit, 'hPa')

    def test_it_leaves_the_other_sections_alone(self):
        self.make_default_model_metadata(name='MarsWRF')
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_two.pk})

        self.assertIsNone(ModelMetadata.override_for_file(self.nc_one))
        self.assertIsNone(SimulationConfiguration.override_for_file(self.nc_one))

    def test_it_copies_what_the_source_label_says_not_only_its_overrides(self):
        """Copying from a file that is itself following the collection default must copy the
        values that file's label actually carries, not nothing."""
        self.make_default_model_metadata(name='MarsWRF', institution='NMSU')

        self.panel_post(self.copy_url(), {
            'section': 'model', 'source_netcdf': self.nc_two.pk})

        copied = ModelMetadata.override_for_file(self.nc_one)
        self.assertIsNotNone(copied)
        self.assertEqual(copied.name, 'MarsWRF')

    def test_it_never_writes_the_collection_default(self):
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_two.pk})

        self.assertIsNone(FileDescription.default_for_collection(self.alpha))

    def test_it_refuses_a_source_in_another_collection(self):
        """Values from another collection describe a different model run, which is the one thing
        the collection scope exists to keep apart."""
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.beta, netcdf_file=self.nc_beta, level_unit='hPa')

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_beta.pk})

        self.assertIsNone(FileDescription.override_for_file(self.nc_one))

    def test_it_refuses_to_copy_a_file_onto_itself(self):
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_one, level_unit='hPa')

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_one.pk})

        self.assertEqual(FileDescription.override_for_file(self.nc_one).level_unit, 'hPa')

    def test_another_users_bundle_is_not_reachable(self):
        self.client.logout()
        self.client.login(username='ama_intruder', password='pw-for-tests')
        FileDescription.objects.create(
            bundle=self.bundle, collection=self.alpha, netcdf_file=self.nc_two, level_unit='hPa')

        self.panel_post(self.copy_url(), {
            'section': 'desc', 'source_netcdf': self.nc_two.pk})

        self.assertIsNone(FileDescription.override_for_file(self.nc_one))


class AMARegressionTests(AMATestCaseMixin, TestCase):
    """Regressions found by the end-to-end audit. Each one was a real defect."""

    def setUp(self):
        super().setUp()
        self.real_regenerate = views.regenerate_netcdf_labels
        views.regenerate_netcdf_labels = lambda bundle, netcdf_objs=None: []
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', self.real_regenerate)
        Product_Bundle.objects.create(bundle=self.bundle)
        self.login()

    def place_on_disk(self, netcdf_file):
        directory = netcdf_file.directory()
        nc_path = os.path.join(directory, os.path.basename(netcdf_file.file.name))
        xml_path = os.path.join(directory, os.path.basename(netcdf_file.file.name)[:-3] + '.xml')
        with open(nc_path, 'wb') as handle:
            handle.write(b'CDF\x01')
        with open(xml_path, 'w') as handle:
            handle.write('<xml/>')
        return nc_path, xml_path

    def test_deleting_a_file_removes_it_from_the_collection_directory(self):
        """Bulk delete looked in the bundle root while uploads live under the collection, so both
        the NetCDF and its label were left orphaned on disk."""
        nc_path, xml_path = self.place_on_disk(self.nc_one)

        self.client.post(
            reverse('build:bulk_delete_netcdf', kwargs={'pk_bundle': self.bundle.pk}),
            {'selected_netcdf': [self.nc_one.pk]})

        self.assertFalse(os.path.exists(nc_path))
        self.assertFalse(os.path.exists(xml_path))

    def test_select_all_and_bulk_delete_are_scoped_to_one_collection(self):
        """Both used a document-wide selector. With one file list per collection that meant Select
        All ticked every collection's files and the delete removed files from tabs the user was
        not looking at."""
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertIn('toggleSelectAll(this)', content)
        self.assertNotIn("var boxes = document.querySelectorAll('.netcdf-checkbox')", content)
        self.assertIn('e.relatedTarget', content)

    def test_non_ascii_is_rejected_because_the_ldd_forbids_it(self):
        """Both AMA base types carry the pattern \\p{IsBasicLatin}*, so an accented institution
        name produces a label that fails PDS4 validation."""
        response = self.panel_post(self.defaults_url(), {'model-institution': 'Université'})

        self.assertEqual(ModelMetadata.objects.count(), 0)
        self.assertIn('data-saved="false"', response.content.decode('utf-8'))

        form = ModelMetadataForm(data={'institution': 'Université'}, scope='collection')
        self.assertFalse(form.is_valid())
        self.assertIn('ASCII', form.errors['institution'][0])

    def test_plain_ascii_is_still_accepted(self):
        form = ModelMetadataForm(data={'institution': 'Universite de Paris'}, scope='collection')
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_whitespace_is_collapsed_the_way_xs_token_would(self):
        """The values derive from xs:token, so an archive reader collapses runs of whitespace.
        Collapsing on save keeps what is stored identical to what is archived."""
        form = ModelMetadataForm(
            data={'name': '  Mars\n\tWRF   model  '}, scope='collection')
        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data['name'], 'Mars WRF model')

    def test_sharing_an_empty_section_does_not_blank_the_default(self):
        """A user who fills one section and leaves another on "same for every file" should not
        wipe the section they never touched."""
        self.make_default_simulation(horizontal_grid_type='lat-lon')

        self.panel_post(self.file_url(), {'sim-apply_scope': 'collection'})

        default = SimulationConfiguration.default_for_collection(self.alpha)
        self.assertIsNotNone(default)
        self.assertEqual(default.horizontal_grid_type, 'lat-lon')

    def test_saving_a_file_unchanged_leaves_it_following_the_collection(self):
        """The file panel is pre-filled from the collection default, so a blind save posts those
        values back. Storing them would fork the file off the default for good."""
        self.make_default_simulation(horizontal_grid_type='lat-lon', description='shared')

        default = SimulationConfiguration.default_for_collection(self.alpha)
        payload = {'sim-{}'.format(name): value
                   for name, value in default.filled_values().items()}
        self.panel_post(self.file_url(), payload)

        self.assertEqual(
            SimulationConfiguration.objects.filter(netcdf_file=self.nc_one).count(), 0)

    def test_a_genuine_edit_still_creates_an_override(self):
        self.make_default_simulation(horizontal_grid_type='lat-lon', description='shared')

        default = SimulationConfiguration.default_for_collection(self.alpha)
        payload = {'sim-{}'.format(name): value
                   for name, value in default.filled_values().items()}
        payload['sim-horizontal_grid_type'] = 'cube-sphere'
        self.panel_post(self.file_url(), payload)

        self.assertEqual(
            SimulationConfiguration.objects.get(netcdf_file=self.nc_one).horizontal_grid_type,
            'cube-sphere')

    def test_uploads_move_across_filesystems(self):
        """os.rename raises EXDEV when the upload area and the archive are separate mounts, which
        is a normal deployment. shutil.move handles it."""
        source = os.path.join(os.path.dirname(views.__file__), 'views.py')
        with open(source, encoding='utf-8') as handle:
            body = handle.read()
        self.assertIn('shutil.move(nc_path, destination)', body)
        self.assertNotIn('os.rename(nc_path,', body)

    def test_template_comments_do_not_leak_into_the_page(self):
        """Django's {# #} is single-line only; a multi-line one renders as visible page text."""
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertNotIn('{#', content)
        self.assertNotIn('Rendered by hand rather than', content)

    def test_each_collection_file_input_has_its_own_id(self):
        """A shared id made every tab's label focus the first tab's file input."""
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertIn('id="id_netcdf_files_{}"'.format(self.alpha.pk), content)
        self.assertIn('id="id_netcdf_files_{}"'.format(self.beta.pk), content)
        self.assertNotIn('id="id_netcdf_files"', content)

    def test_collection_tabs_use_a_selector_safe_id(self):
        """The collection name went straight into a CSS selector, so a name with a space or a
        quote made the tab impossible to open - and the AMA panel inside it unreachable."""
        spaced = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name="My Data", collection_type='External')

        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertIn('additional_collection_{}'.format(spaced.pk), content)
        self.assertNotIn('#additional_My Data_collection', content)

    def test_a_failed_save_keeps_the_panel_marked_dirty(self):
        """A rejected save returns the user's values plus the errors. Clearing the dirty flag
        would let a tab switch discard them with no warning."""
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertIn("dirty = !(inner && inner.dataset.saved === 'true');", content)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AMAUnassignedFileTests(AMATestCaseMixin, TestCase):
    """Files that predate the collection field and could not be backfilled.

    They belong to no collection. They used to be displayed inside the first collection, which
    made them appear to hop from one collection to another whenever a collection was deleted -
    their collection was NULL the entire time and nothing had actually moved.
    """

    def setUp(self):
        super().setUp()
        Product_Bundle.objects.create(bundle=self.bundle)
        self.orphan = NetCDFFile.objects.create(
            title='legacy.nc', file='legacy.nc', bundle=self.bundle, collection=None)
        self.login()

    def bundle_page(self):
        return self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

    def test_an_unassigned_file_is_not_listed_inside_any_collection(self):
        for collection in views._collections_with_ama(self.bundle):
            with self.subTest(collection=collection.collection_name):
                self.assertNotIn(self.orphan.pk, [f.pk for f in collection.ama_files])

    def test_deleting_a_collection_does_not_appear_to_move_it(self):
        """The exact symptom that was reported."""
        def collections_showing_it():
            return [c.collection_name for c in views._collections_with_ama(self.bundle)
                    for f in c.ama_files if f.pk == self.orphan.pk]

        self.assertEqual(collections_showing_it(), [])
        self.alpha.delete()
        self.orphan.refresh_from_db()

        self.assertEqual(collections_showing_it(), [])
        self.assertIsNone(self.orphan.collection, 'the file was never in a collection')

    def test_it_is_listed_in_its_own_section(self):
        content = self.bundle_page()
        self.assertIn('Files Not In Any Collection', content)
        self.assertIn('Unassigned (1)', content)
        self.assertIn(reverse('build:assign_netcdf_collection', kwargs={
            'pk_bundle': self.bundle.pk, 'pk_netcdf': self.orphan.pk}), content)

    def test_the_section_disappears_once_nothing_is_unassigned(self):
        self.orphan.collection = self.alpha
        self.orphan.save(update_fields=['collection'])
        self.assertNotIn('Files Not In Any Collection', self.bundle_page())

    def test_moving_it_into_a_collection_attaches_it(self):
        self.client.post(
            reverse('build:assign_netcdf_collection', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.orphan.pk}),
            {'collection': self.alpha.pk})

        self.orphan.refresh_from_db()
        self.assertEqual(self.orphan.collection, self.alpha)

    def test_moving_it_relocates_the_file_and_its_label_on_disk(self):
        source = os.path.join(self.bundle.directory(), 'legacy.nc')
        stale_label = os.path.join(self.bundle.directory(), 'legacy.xml')
        with open(source, 'wb') as handle:
            handle.write(b'CDF\x01')
        with open(stale_label, 'w') as handle:
            handle.write('<xml/>')

        self.client.post(
            reverse('build:assign_netcdf_collection', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.orphan.pk}),
            {'collection': self.alpha.pk})

        self.assertFalse(os.path.exists(source), 'the NetCDF was left in the bundle root')
        self.assertFalse(os.path.exists(stale_label), 'the stale label was left behind')
        self.assertTrue(
            os.path.exists(os.path.join(self.alpha.directory(), 'legacy.nc')),
            'the NetCDF did not arrive in the collection directory')

    def test_it_then_inherits_that_collections_defaults(self):
        self.make_default_model_metadata(collection=self.alpha, name='MarsWRF')

        self.client.post(
            reverse('build:assign_netcdf_collection', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.orphan.pk}),
            {'collection': self.alpha.pk})
        self.orphan.refresh_from_db()

        self.assertEqual(ModelMetadata.resolve_for_file(self.orphan).name, 'MarsWRF')

    def test_a_collection_from_another_bundle_is_refused(self):
        other = Bundle.objects.create(name='other_one', user=self.user, version='1800')
        stray = AdditionalCollections.objects.create(
            bundle=other, collection_name='stray', collection_type='External')

        self.client.post(
            reverse('build:assign_netcdf_collection', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.orphan.pk}),
            {'collection': stray.pk})

        self.orphan.refresh_from_db()
        self.assertIsNone(self.orphan.collection)

    def test_another_user_cannot_move_it(self):
        self.client.logout()
        self.client.login(username='ama_intruder', password='pw-for-tests')

        response = self.client.post(
            reverse('build:assign_netcdf_collection', kwargs={
                'pk_bundle': self.bundle.pk, 'pk_netcdf': self.orphan.pk}),
            {'collection': self.alpha.pk})

        self.assertRedirects(response, reverse('main:restricted_access'),
                             fetch_redirect_response=False)
        self.orphan.refresh_from_db()
        self.assertIsNone(self.orphan.collection)

    def test_it_can_still_be_deleted(self):
        self.client.post(
            reverse('build:bulk_delete_netcdf', kwargs={'pk_bundle': self.bundle.pk}),
            {'selected_netcdf': [self.orphan.pk]})
        self.assertEqual(NetCDFFile.objects.filter(pk=self.orphan.pk).count(), 0)


class PDS4CollectionTypeTests(SimpleTestCase):
    """The enumerated Collection/type each collection declares, and the reference type that pairs.

    The stored Product_Collection.collection is ELSA's internal key (directory name, LID segment,
    label filename); the label has to carry a value from the PDS4 Collection/type enumeration, and
    the two differ in two places: XML_Schema is spelled "XML Schema", and every collection in an
    External bundle is of type External because AMA LIDs (urn:nasa:pds-ama) are not admissible in
    a Document collection.

    Nothing here touches the database, so the objects are left unsaved.
    """

    def collection(self, key, bundle_type='Archive'):
        return Product_Collection(
            bundle=Bundle(name='Test', bundleID='test', bundle_type=bundle_type),
            collection=key)

    def test_archive_types_are_the_stored_key(self):
        for key in ('Document', 'Context', 'Data'):
            self.assertEqual(self.collection(key).collection_type(), key)

    def test_xml_schema_loses_its_underscore(self):
        self.assertEqual(self.collection('XML_Schema').collection_type(), 'XML Schema')

    def test_every_external_collection_is_typed_external(self):
        for key in ('Document', 'Context', 'XML_Schema'):
            self.assertEqual(
                self.collection(key, bundle_type='External').collection_type(), 'External')

    def test_reference_type_follows_the_collection_type(self):
        self.assertEqual(self.collection('Document').reference_type(),
                         'bundle_has_document_collection')
        self.assertEqual(self.collection('XML_Schema').reference_type(),
                         'bundle_has_schema_collection')
        self.assertEqual(self.collection('Document', bundle_type='External').reference_type(),
                         'bundle_has_external_collection')

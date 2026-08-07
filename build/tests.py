# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import os
import re
import shutil
import tempfile

from django.conf import settings
from django.contrib.auth.models import User
from django.core.cache import cache
from django.template.loader import get_template
from django.test import TestCase, override_settings
from django.urls import reverse

from build import views
from build.forms import (FileDescriptionForm, ModelMetadataForm,
                         SimulationConfigurationForm)
from build.models import (AdditionalCollections, Bundle, FileDescription,
                          ModelMetadata, NetCDFFile, Product_Bundle,
                          SimulationConfiguration)

# views.py binds ET to lxml at the top of the file and then rebinds it to the stdlib
# ElementTree further down, in the NetCDF section. The stdlib one is what actually runs, and the
# two libraries are not interchangeable (their Elements reject each other), so the tests use
# whatever views is using rather than picking a parser of their own.
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


def ama_child_names(container):
    """Local names of a container's children, in document order."""
    return [child.tag.split('}')[-1] for child in container]


class AMATestCaseMixin(object):
    """Shared fixtures: a user, a bundle rooted in a temp archive dir, and two NetCDF records."""

    def setUp(self):
        super().setUp()
        self.archive_dir = tempfile.mkdtemp(prefix='elsa-ama-test-')
        self.addCleanup(shutil.rmtree, self.archive_dir, True)

        self.settings_patcher = override_settings(ARCHIVE_DIR=self.archive_dir)
        self.settings_patcher.enable()
        self.addCleanup(self.settings_patcher.disable)

        self.user = User.objects.create_user(
            username='ama_tester', password='pw-for-tests', email='ama@example.com')
        self.other_user = User.objects.create_user(
            username='ama_intruder', password='pw-for-tests', email='intruder@example.com')

        self.bundle = Bundle.objects.create(
            name='ama_test_bundle', user=self.user, version='1800', bundle_type='External')
        os.makedirs(self.bundle.directory(), exist_ok=True)

        self.nc_one = NetCDFFile.objects.create(
            title='00000.atmos_average.nc', file='00000.atmos_average.nc',
            bundle=self.bundle, processed=True)
        self.nc_two = NetCDFFile.objects.create(
            title='00001.atmos_average.nc', file='00001.atmos_average.nc',
            bundle=self.bundle, processed=True)

    def login(self):
        self.client.login(username='ama_tester', password='pw-for-tests')

    def make_default_simulation(self, **overrides):
        values = {
            'horizontal_grid_type': 'lat/lon',
            'model_resolution': '5x5',
            'northern_boundary': 90.0,
            'southern_boundary': -90.0,
            'description': 'Bundle-wide default run.',
        }
        values.update(overrides)
        return SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=None, **values)

    def make_default_description(self, **overrides):
        values = {
            'top_level': 0.01,
            'bottom_level': 700.0,
            'level_unit': 'Pa',
            'postprocessing_methods': 'time:mean(interval=3 hours)',
        }
        values.update(overrides)
        return FileDescription.objects.create(
            bundle=self.bundle, netcdf_file=None, **values)


class AMAModelResolutionTests(AMATestCaseMixin, TestCase):
    """The default-plus-override resolution that the whole feature rests on."""

    def test_default_row_is_used_when_file_has_no_override(self):
        default = self.make_default_simulation()
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), default)
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_two), default)

    def test_override_row_wins_over_default(self):
        self.make_default_simulation()
        override = SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), override)
        self.assertTrue(SimulationConfiguration.resolve_for_file(self.nc_two).is_default())

    def test_resolution_returns_none_when_nothing_is_filled_in(self):
        self.assertIsNone(SimulationConfiguration.resolve_for_file(self.nc_one))
        self.assertIsNone(FileDescription.resolve_for_file(self.nc_one))

    def test_default_lookup_ignores_per_file_rows(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')
        self.assertIsNone(SimulationConfiguration.default_for_bundle(self.bundle))

    def test_deleting_a_netcdf_file_removes_only_its_override(self):
        default = self.make_default_simulation()
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        self.nc_one.delete()

        self.assertEqual(list(SimulationConfiguration.objects.all()), [default])


class AMAFilledValuesTests(AMATestCaseMixin, TestCase):
    """filled_values() is what decides which elements reach the label."""

    def test_blank_and_null_fields_are_dropped(self):
        record = SimulationConfiguration.objects.create(
            bundle=self.bundle, horizontal_grid_type='lat/lon', vertical_grid_type='   ')
        self.assertEqual(record.filled_values(), {'horizontal_grid_type': 'lat/lon'})

    def test_zero_is_kept_because_it_is_a_real_measurement(self):
        record = SimulationConfiguration.objects.create(
            bundle=self.bundle, northern_boundary=0.0, model_timestep=0.0)
        self.assertEqual(record.filled_values(),
                         {'model_timestep': '0.0', 'northern_boundary': '0.0'})

    def test_values_come_back_in_ldd_sequence_order(self):
        record = SimulationConfiguration.objects.create(
            bundle=self.bundle, description='last in the sequence', time_unit='sols',
            horizontal_grid_type='lat/lon')
        self.assertEqual(list(record.filled_values().keys()),
                         ['horizontal_grid_type', 'time_unit', 'description'])

    def test_model_metadata_drops_blanks_too(self):
        record = ModelMetadata.objects.create(
            bundle=self.bundle, name='MarsWRF', type='', version='  ', institution='NMSU')
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
        """Guards the premise of the fix: every AMA attribute has minLength=1 in the LDD, so the
        empty elements the base template carries are invalid until something clears them."""
        root = self.parsed_template()
        metadata = self.ama_element(root, 'ama:Model_Metadata')
        self.assertEqual(ama_child_names(metadata), ['type', 'name', 'version', 'institution'])
        self.assertTrue(all((child.text or '').strip() == '' for child in metadata))

    def test_unset_classes_leave_no_empty_elements_behind(self):
        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        for path in ('ama:Model_Metadata', 'ama:Simulation_Configuration',
                     'ama:Model_Output/ama:File_Description'):
            container = self.ama_element(root, path)
            self.assertEqual(len(container), 0,
                             '{} should be emptied, not left with blank elements'.format(path))

    def test_containers_are_preserved_even_when_empty(self):
        """All three are minOccurs=1 inside ama:AMA, so they must survive an empty write."""
        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        ama = root.find('.//pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
        self.assertEqual(ama_child_names(ama),
                         ['Model_Metadata', 'Simulation_Configuration', 'Model_Output'])

    def test_filled_values_are_written_and_blanks_omitted(self):
        ModelMetadata.objects.create(
            bundle=self.bundle, type='GCM', name='MarsWRF', institution='NMSU')

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        metadata = self.ama_element(root, 'ama:Model_Metadata')
        self.assertEqual(ama_child_names(metadata), ['type', 'name', 'institution'])
        self.assertEqual(metadata.find('ama:name', namespaces=NS).text, 'MarsWRF')

    def test_compass_boundaries_carry_the_required_unit_attribute(self):
        self.make_default_simulation(northern_boundary=45.5, eastern_boundary=180.0)

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        simulation = self.ama_element(root, 'ama:Simulation_Configuration')
        northern = simulation.find('ama:northern_boundary', namespaces=NS)
        self.assertEqual(northern.get('unit'), 'deg')
        self.assertEqual(northern.text, '45.5')
        # Other attributes must NOT get a unit attribute - it is not in their LDD definition.
        self.assertIsNone(simulation.find('ama:model_resolution', namespaces=NS).get('unit'))

    def test_simulation_elements_follow_ldd_sequence(self):
        self.make_default_simulation(
            time_unit='sols', model_timestep=30.0, horizontal_grid_type='lat/lon')

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)

        names = ama_child_names(self.ama_element(root, 'ama:Simulation_Configuration'))
        self.assertEqual(names, [
            'horizontal_grid_type', 'model_resolution', 'model_timestep',
            'northern_boundary', 'southern_boundary', 'time_unit', 'description'])

    def test_per_file_override_beats_the_bundle_default_in_the_label(self):
        self.make_default_simulation(horizontal_grid_type='lat/lon')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        root_one = self.parsed_template()
        views.write_ama_user_classes(root_one, NS, self.bundle, netcdf_obj=self.nc_one)
        root_two = self.parsed_template()
        views.write_ama_user_classes(root_two, NS, self.bundle, netcdf_obj=self.nc_two)

        self.assertEqual(
            self.ama_element(root_one, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text,
            'cubed sphere')
        self.assertEqual(
            self.ama_element(root_two, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text,
            'lat/lon')

    def test_file_description_stays_first_child_of_model_output(self):
        """The LDD sequence is File_Description, Variable*, Coordinate*. The harvest appends
        Variables and Coordinates before this writer runs, so filling in place is what keeps the
        label valid."""
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

    def test_bundle_defaults_are_used_when_no_file_is_given(self):
        self.make_default_simulation(horizontal_grid_type='lat/lon')

        root = self.parsed_template()
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=None)

        self.assertEqual(
            self.ama_element(root, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text,
            'lat/lon')

    def test_label_without_an_ama_area_is_left_alone(self):
        root = ET.fromstring('<Product_External xmlns="{}"><Identification_Area/></Product_External>'.format(PDS))
        views.write_ama_user_classes(root, NS, self.bundle, netcdf_obj=self.nc_one)
        self.assertEqual(len(root), 1)


class AMAFormTests(AMATestCaseMixin, TestCase):

    def test_every_field_is_optional_because_the_ldd_makes_them_optional(self):
        form = SimulationConfigurationForm(data={}, scope='default')
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_latitude_range_from_the_ldd_is_enforced(self):
        form = SimulationConfigurationForm(data={'northern_boundary': '120'}, scope='default')
        self.assertFalse(form.is_valid())
        self.assertIn('northern_boundary', form.errors)

    def test_longitude_range_from_the_ldd_is_enforced(self):
        self.assertTrue(
            SimulationConfigurationForm(data={'eastern_boundary': '360'}, scope='default').is_valid())
        self.assertFalse(
            SimulationConfigurationForm(data={'eastern_boundary': '361'}, scope='default').is_valid())

    def test_apply_to_all_is_only_offered_on_the_bundle_default_form(self):
        self.assertIn('apply_to_all', SimulationConfigurationForm(scope='default').fields)
        self.assertNotIn('apply_to_all', SimulationConfigurationForm(scope='file').fields)
        self.assertIn('apply_to_all', FileDescriptionForm(scope='default').fields)
        self.assertNotIn('apply_to_all', FileDescriptionForm(scope='file').fields)

    def test_start_time_accepts_free_text_model_time(self):
        form = SimulationConfigurationForm(data={'start_time': 'sol 120'}, scope='default')
        self.assertTrue(form.is_valid(), form.errors.as_text())
        self.assertEqual(form.cleaned_data['start_time'], 'sol 120')

    def test_model_metadata_form_saves_without_any_input(self):
        form = ModelMetadataForm(data={})
        self.assertTrue(form.is_valid(), form.errors.as_text())

    def test_field_groups_cover_every_editable_field_exactly_once(self):
        """A field missing from FIELD_GROUPS would silently vanish from both the modal and the
        per-file page, since they render groups rather than iterating the form."""
        cases = [
            (SimulationConfigurationForm(scope='file'), SimulationConfiguration),
            (FileDescriptionForm(scope='file'), FileDescription),
            (ModelMetadataForm(), ModelMetadata),
        ]
        for form, model_class in cases:
            with self.subTest(form=type(form).__name__):
                grouped = [field.name for group in form.groups() for field in group['fields']]
                self.assertEqual(sorted(grouped), sorted(model_class.ELEMENT_ORDER))
                self.assertEqual(len(grouped), len(set(grouped)), 'a field is grouped twice')

    def test_apply_to_all_is_not_rendered_by_the_shared_field_partial(self):
        """It is a scope control, not AMA content, so the modal positions it separately."""
        form = SimulationConfigurationForm(scope='default')
        grouped = [field.name for group in form.groups() for field in group['fields']]
        self.assertIn('apply_to_all', form.fields)
        self.assertNotIn('apply_to_all', grouped)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AMAViewTests(AMATestCaseMixin, TestCase):
    """The views, with label regeneration stubbed out so these stay independent of PDS and xarray."""

    def setUp(self):
        super().setUp()
        self.regenerated = []
        self.real_regenerate = views.regenerate_netcdf_labels

        def fake_regenerate(bundle, netcdf_objs=None):
            self.regenerated.append(
                (bundle, None if netcdf_objs is None else [n.pk for n in netcdf_objs]))
            return []

        views.regenerate_netcdf_labels = fake_regenerate
        self.addCleanup(setattr, views, 'regenerate_netcdf_labels', self.real_regenerate)

    def url(self, name, **kwargs):
        kwargs.setdefault('pk_bundle', self.bundle.pk)
        return reverse('build:' + name, kwargs=kwargs)

    # --- authorization ------------------------------------------------------------------------

    def test_all_ama_views_reject_a_different_user(self):
        self.client.login(username='ama_intruder', password='pw-for-tests')

        targets = [
            (self.url('ama_model_metadata'), {}),
            (self.url('ama_simulation_configuration'), {}),
            (self.url('ama_file_description'), {}),
            (self.url('netcdf_ama', pk_netcdf=self.nc_one.pk), {}),
            (self.url('netcdf_ama_reset', pk_netcdf=self.nc_one.pk), {}),
        ]
        for url, payload in targets:
            with self.subTest(url=url):
                response = self.client.post(url, payload)
                self.assertRedirects(response, reverse('main:restricted_access'),
                                     fetch_redirect_response=False)
        self.assertEqual(ModelMetadata.objects.count(), 0)
        self.assertEqual(SimulationConfiguration.objects.count(), 0)

    def test_a_file_from_another_bundle_is_not_reachable(self):
        other_bundle = Bundle.objects.create(
            name='someone_elses_bundle', user=self.user, version='1800')
        stray = NetCDFFile.objects.create(title='stray.nc', file='stray.nc', bundle=other_bundle)

        self.login()
        response = self.client.get(self.url('netcdf_ama', pk_netcdf=stray.pk))
        self.assertEqual(response.status_code, 404)

    # --- bundle-wide defaults -----------------------------------------------------------------

    def test_saving_model_metadata_stores_values_and_refreshes_labels(self):
        self.login()
        response = self.client.post(self.url('ama_model_metadata'), {
            'model-type': 'GCM', 'model-name': 'MarsWRF',
            'model-version': '3.2', 'model-institution': 'NMSU'})

        self.assertEqual(response.status_code, 302)
        metadata = ModelMetadata.objects.get(bundle=self.bundle)
        self.assertEqual(metadata.name, 'MarsWRF')
        self.assertEqual(self.regenerated, [(self.bundle, None)])

    def test_saving_model_metadata_twice_updates_rather_than_duplicates(self):
        self.login()
        self.client.post(self.url('ama_model_metadata'), {'model-name': 'MarsWRF'})
        self.client.post(self.url('ama_model_metadata'),
                         {'model-name': 'MarsWRF', 'model-version': '4.0'})

        self.assertEqual(ModelMetadata.objects.filter(bundle=self.bundle).count(), 1)
        self.assertEqual(ModelMetadata.objects.get(bundle=self.bundle).version, '4.0')

    def test_saving_simulation_default_creates_exactly_one_default_row(self):
        self.login()
        self.client.post(self.url('ama_simulation_configuration'), {'sim-horizontal_grid_type': 'lat/lon'})
        self.client.post(self.url('ama_simulation_configuration'),
                         {'sim-horizontal_grid_type': 'cubed sphere'})

        defaults = SimulationConfiguration.objects.filter(
            bundle=self.bundle, netcdf_file__isnull=True)
        self.assertEqual(defaults.count(), 1)
        self.assertEqual(defaults.first().horizontal_grid_type, 'cubed sphere')

    def test_invalid_boundary_is_rejected_and_nothing_is_saved(self):
        self.login()
        response = self.client.post(
            self.url('ama_simulation_configuration'), {'sim-northern_boundary': '120'}, follow=False)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(SimulationConfiguration.objects.count(), 0)
        self.assertEqual(self.regenerated, [], 'labels must not be rewritten after a failed save')

    def test_apply_to_all_clears_per_file_overrides(self):
        self.make_default_simulation()
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_two, horizontal_grid_type='icosahedral')

        self.login()
        self.client.post(self.url('ama_simulation_configuration'),
                         {'sim-horizontal_grid_type': 'lat/lon', 'sim-apply_to_all': 'on'})

        self.assertEqual(
            SimulationConfiguration.objects.filter(netcdf_file__isnull=False).count(), 0)
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one).horizontal_grid_type,
                         'lat/lon')

    def test_saving_without_apply_to_all_leaves_overrides_alone(self):
        self.make_default_simulation()
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        self.login()
        self.client.post(self.url('ama_simulation_configuration'), {'sim-horizontal_grid_type': 'lat/lon'})

        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one).horizontal_grid_type,
                         'cubed sphere')

    def test_apply_to_all_on_file_description_does_not_touch_simulation_overrides(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')
        FileDescription.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, level_unit='hPa')

        self.login()
        self.client.post(self.url('ama_file_description'),
                         {'desc-level_unit': 'Pa', 'desc-apply_to_all': 'on'})

        self.assertEqual(FileDescription.objects.filter(netcdf_file__isnull=False).count(), 0)
        self.assertEqual(SimulationConfiguration.objects.filter(netcdf_file__isnull=False).count(), 1)

    # --- per-file page ------------------------------------------------------------------------

    def test_per_file_page_renders_and_prefills_from_the_bundle_default(self):
        self.make_default_simulation(horizontal_grid_type='lat/lon')
        self.login()

        response = self.client.get(self.url('netcdf_ama', pk_netcdf=self.nc_one.pk))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'build/bundle/netcdf_ama.html')
        self.assertFalse(response.context['has_simulation_override'])
        self.assertEqual(
            response.context['form_simulation']['horizontal_grid_type'].value(), 'lat/lon')

    def test_saving_a_per_file_form_creates_an_override_and_refreshes_only_that_file(self):
        self.make_default_simulation(horizontal_grid_type='lat/lon')
        self.login()

        response = self.client.post(self.url('netcdf_ama', pk_netcdf=self.nc_one.pk), {
            'sim-horizontal_grid_type': 'cubed sphere',
            'desc-level_unit': 'Pa',
        })

        self.assertEqual(response.status_code, 302)
        override = SimulationConfiguration.objects.get(netcdf_file=self.nc_one)
        self.assertEqual(override.horizontal_grid_type, 'cubed sphere')
        self.assertEqual(FileDescription.objects.get(netcdf_file=self.nc_one).level_unit, 'Pa')
        self.assertEqual(self.regenerated, [(self.bundle, [self.nc_one.pk])])
        # The other file is untouched and still resolves to the default.
        self.assertTrue(SimulationConfiguration.resolve_for_file(self.nc_two).is_default())

    def test_saving_a_per_file_form_twice_updates_the_same_override(self):
        self.login()
        url = self.url('netcdf_ama', pk_netcdf=self.nc_one.pk)
        self.client.post(url, {'sim-horizontal_grid_type': 'cubed sphere'})
        self.client.post(url, {'sim-horizontal_grid_type': 'icosahedral'})

        overrides = SimulationConfiguration.objects.filter(netcdf_file=self.nc_one)
        self.assertEqual(overrides.count(), 1)
        self.assertEqual(overrides.first().horizontal_grid_type, 'icosahedral')

    def test_clearing_a_per_file_form_falls_back_to_the_bundle_default(self):
        """An empty override would outrank the default forever, so clearing must delete it."""
        default = self.make_default_simulation(horizontal_grid_type='lat/lon')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        self.login()
        self.client.post(self.url('netcdf_ama', pk_netcdf=self.nc_one.pk), {
            'sim-horizontal_grid_type': '', 'desc-level_unit': ''})

        self.assertEqual(SimulationConfiguration.objects.filter(netcdf_file=self.nc_one).count(), 0)
        self.assertEqual(SimulationConfiguration.resolve_for_file(self.nc_one), default)

    def test_an_all_blank_first_save_does_not_create_a_shadowing_row(self):
        self.login()
        self.client.post(self.url('netcdf_ama', pk_netcdf=self.nc_one.pk), {})

        self.assertEqual(SimulationConfiguration.objects.count(), 0)
        self.assertEqual(FileDescription.objects.count(), 0)

    def test_invalid_per_file_input_re_renders_with_errors_and_saves_nothing(self):
        self.login()
        response = self.client.post(self.url('netcdf_ama', pk_netcdf=self.nc_one.pk),
                                    {'sim-northern_boundary': '120'})

        self.assertEqual(response.status_code, 200)
        self.assertIn('northern_boundary', response.context['form_simulation'].errors)
        self.assertEqual(SimulationConfiguration.objects.count(), 0)
        self.assertEqual(self.regenerated, [])

    def test_reset_drops_both_overrides_for_the_file(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')
        FileDescription.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, level_unit='hPa')
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_two, horizontal_grid_type='icosahedral')

        self.login()
        self.client.post(self.url('netcdf_ama_reset', pk_netcdf=self.nc_one.pk))

        self.assertEqual(SimulationConfiguration.objects.filter(netcdf_file=self.nc_one).count(), 0)
        self.assertEqual(FileDescription.objects.filter(netcdf_file=self.nc_one).count(), 0)
        self.assertEqual(SimulationConfiguration.objects.filter(netcdf_file=self.nc_two).count(), 1)

    def test_override_badge_flag_is_computed_for_the_file_list(self):
        FileDescription.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_two, level_unit='hPa')

        flags = {f.pk: f.has_ama_override for f in views._netcdf_files_with_ama_flags(self.bundle)}

        self.assertFalse(flags[self.nc_one.pk])
        self.assertTrue(flags[self.nc_two.pk])


class AMARegenerationTests(AMATestCaseMixin, TestCase):
    """regenerate_netcdf_labels, with the harvest itself stubbed out."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.processed = []
        self.real_process = views._process_single_netcdf

        def fake_process(bundle, nc_path, ns, allowed_variable_fields, allowed_coord_fields,
                         netcdf_obj=None):
            self.processed.append((nc_path, netcdf_obj))

        views._process_single_netcdf = fake_process
        self.addCleanup(setattr, views, '_process_single_netcdf', self.real_process)

        # Keep the LDD fetch off the network: the cache key is what _fetch_ldd_content reads.
        with open(TEMPLATE_PE, 'rb'):
            pass
        cache.set('ldd_schema:' + views.AMA_LDD_URL, MINIMAL_LDD.encode('utf-8'), 300)
        self.addCleanup(cache.clear)

    def touch_on_disk(self, netcdf_file):
        path = os.path.join(self.bundle.directory(), os.path.basename(netcdf_file.file.name))
        with open(path, 'wb') as handle:
            handle.write(b'CDF\x01')
        return path

    def test_files_present_on_disk_are_regenerated_with_their_record(self):
        path_one = self.touch_on_disk(self.nc_one)
        self.touch_on_disk(self.nc_two)

        errors = views.regenerate_netcdf_labels(self.bundle)

        self.assertEqual(errors, [])
        self.assertEqual(len(self.processed), 2)
        paths = [entry[0] for entry in self.processed]
        records = [entry[1] for entry in self.processed]
        self.assertIn(path_one, paths)
        self.assertIn(self.nc_one, records,
                      'the NetCDFFile must be passed through so per-file overrides resolve')

    def test_files_missing_from_disk_are_skipped_not_failed(self):
        self.touch_on_disk(self.nc_one)

        errors = views.regenerate_netcdf_labels(self.bundle)

        self.assertEqual(errors, [])
        self.assertEqual(len(self.processed), 1)
        self.nc_two.refresh_from_db()
        self.assertTrue(self.nc_two.processed, 'a skipped file must not be marked as failed')

    def test_a_failing_file_is_reported_without_stopping_the_others(self):
        self.touch_on_disk(self.nc_one)
        self.touch_on_disk(self.nc_two)

        def explode(bundle, nc_path, ns, allowed_variable_fields, allowed_coord_fields,
                    netcdf_obj=None):
            if netcdf_obj.pk == self.nc_one.pk:
                raise ValueError('bad file')
            self.processed.append((nc_path, netcdf_obj))

        views._process_single_netcdf = explode

        errors = views.regenerate_netcdf_labels(self.bundle)

        self.assertEqual(len(errors), 1)
        self.assertIn('bad file', errors[0])
        self.assertEqual(len(self.processed), 1)
        self.nc_one.refresh_from_db()
        self.assertFalse(self.nc_one.processed)
        self.assertIn('bad file', self.nc_one.processing_error)

    def test_an_unreachable_ldd_reports_an_error_instead_of_raising(self):
        cache.clear()
        self.touch_on_disk(self.nc_one)

        real_fetch = views._fetch_ldd_content

        def unreachable(url):
            raise IOError('PDS is down')

        views._fetch_ldd_content = unreachable
        self.addCleanup(setattr, views, '_fetch_ldd_content', real_fetch)

        errors = views.regenerate_netcdf_labels(self.bundle)

        self.assertEqual(len(errors), 1)
        self.assertIn('data dictionary', errors[0])
        self.assertEqual(self.processed, [])

    def test_a_bundle_with_no_files_does_no_work(self):
        NetCDFFile.objects.all().delete()
        self.assertEqual(views.regenerate_netcdf_labels(self.bundle), [])
        self.assertEqual(self.processed, [])


class AMAEndToEndLabelTests(AMATestCaseMixin, TestCase):
    """Runs the real harvest over a real NetCDF file and checks the label written to disk.

    This is the test that proves the feature works: it exercises xarray extraction, the AMA writer
    and the version stamp together, rather than any one of them in isolation.
    """

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

        self.nc_path = self.write_netcdf(self.nc_one)

    def write_netcdf(self, netcdf_file):
        import numpy as np
        import xarray as xr

        dataset = xr.Dataset(
            {'temp': (('lat', 'lon'), np.zeros((2, 3), dtype='float32'),
                      {'units': 'K', 'long_name': 'temperature'})},
            coords={'lat': ('lat', np.array([-45.0, 45.0]), {'units': 'degrees_north'}),
                    'lon': ('lon', np.array([0.0, 120.0, 240.0]), {'units': 'degrees_east'})})

        path = os.path.join(self.bundle.directory(), os.path.basename(netcdf_file.file.name))
        dataset.to_netcdf(path)
        dataset.close()
        return path

    def label_path(self):
        return os.path.join(self.bundle.directory(), '00000.atmos_average.xml')

    def read_label(self):
        return ET.parse(self.label_path()).getroot()

    def ama_element(self, root, path):
        return root.find('.//pds:Context_Area/pds:Discipline_Area/ama:AMA/' + path, namespaces=NS)

    def test_user_values_reach_the_label_alongside_the_harvested_variables(self):
        ModelMetadata.objects.create(bundle=self.bundle, name='MarsWRF', institution='NMSU')
        self.make_default_simulation(horizontal_grid_type='lat/lon', northern_boundary=45.0)
        self.make_default_description(level_unit='Pa')

        errors = views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        self.assertEqual(errors, [])
        self.assertTrue(os.path.exists(self.label_path()))

        root = self.read_label()

        # User-entered content
        metadata = self.ama_element(root, 'ama:Model_Metadata')
        self.assertEqual(ama_child_names(metadata), ['name', 'institution'])
        simulation = self.ama_element(root, 'ama:Simulation_Configuration')
        self.assertEqual(simulation.find('ama:horizontal_grid_type', namespaces=NS).text, 'lat/lon')
        self.assertEqual(simulation.find('ama:northern_boundary', namespaces=NS).get('unit'), 'deg')

        # Harvested content still present and in the right place
        model_output = self.ama_element(root, 'ama:Model_Output')
        child_names = ama_child_names(model_output)
        self.assertEqual(child_names[0], 'File_Description')
        self.assertIn('Variable', child_names)
        self.assertIn('Coordinate', child_names)
        variable_names = [v.find('ama:variable_name', namespaces=NS).text
                          for v in model_output.findall('ama:Variable', namespaces=NS)]
        self.assertIn('temp', variable_names)

    def test_editing_after_upload_updates_the_label_on_disk(self):
        """The bug this feature had to solve: labels were only ever written at upload time."""
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        before = self.ama_element(self.read_label(), 'ama:Simulation_Configuration')
        self.assertEqual(len(before), 0)

        self.make_default_simulation(horizontal_grid_type='lat/lon')
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])

        after = self.ama_element(self.read_label(), 'ama:Simulation_Configuration')
        self.assertEqual(after.find('ama:horizontal_grid_type', namespaces=NS).text, 'lat/lon')

    def test_regeneration_is_idempotent(self):
        """Rebuilding from the template each time is what stops the logical_identifier from
        growing a new suffix on every save."""
        self.make_default_simulation(horizontal_grid_type='lat/lon')

        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        first = open(self.label_path(), encoding='utf-8').read()
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])
        second = open(self.label_path(), encoding='utf-8').read()

        self.assertEqual(first, second)

        lid = self.read_label().find(
            './/pds:Identification_Area/pds:logical_identifier', namespaces=NS).text
        self.assertEqual(lid.count('00000.atmos_average.nc'), 1)

    def test_no_empty_ama_elements_are_written(self):
        """Every AMA attribute is minLength=1, so an empty element would fail schema validation."""
        self.make_default_simulation(horizontal_grid_type='lat/lon')
        views.regenerate_netcdf_labels(self.bundle, [self.nc_one])

        root = self.read_label()
        for path in ('ama:Model_Metadata', 'ama:Simulation_Configuration',
                     'ama:Model_Output/ama:File_Description'):
            container = self.ama_element(root, path)
            for child in container:
                with self.subTest(element=child.tag):
                    self.assertTrue((child.text or '').strip(),
                                    '{} was written with empty text'.format(child.tag))

    def test_per_file_override_produces_a_different_label_than_the_default(self):
        self.make_default_simulation(horizontal_grid_type='lat/lon')
        self.write_netcdf(self.nc_two)
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        self.assertEqual(views.regenerate_netcdf_labels(self.bundle), [])

        root_one = self.read_label()
        root_two = ET.parse(
            os.path.join(self.bundle.directory(), '00001.atmos_average.xml')).getroot()

        self.assertEqual(
            self.ama_element(root_one, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text, 'cubed sphere')
        self.assertEqual(
            self.ama_element(root_two, 'ama:Simulation_Configuration').find(
                'ama:horizontal_grid_type', namespaces=NS).text, 'lat/lon')

    def test_generated_ama_area_validates_against_the_real_ldd_schema(self):
        """Validates the AMA subtree of a generated label against PDS4_AMA_1O00_1300.xsd itself.

        Skipped when pds.nasa.gov is unreachable - the schema and the PDS common schema it imports
        are fetched at run time rather than vendored into the repo. The same check also
        demonstrates why blank fields must be omitted: the base template's empty elements violate
        minLength=1, so labels built before this change did not validate.
        """
        try:
            import urllib.request

            from lxml import etree as lxml_etree
        except ImportError as exc:  # pragma: no cover - depends on the environment
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

        ModelMetadata.objects.create(bundle=self.bundle, name='MarsWRF', institution='NMSU')
        self.make_default_simulation(northern_boundary=45.0, eastern_boundary=180.0)
        self.make_default_description()
        self.assertEqual(views.regenerate_netcdf_labels(self.bundle, [self.nc_one]), [])

        generated = lxml_etree.parse(self.label_path()).getroot().find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
        self.assertTrue(
            schema.validate(lxml_etree.ElementTree(generated)),
            'generated AMA area failed schema validation: {}'.format(schema.error_log))

        # And the premise: the untouched template does not validate.
        template_ama = lxml_etree.parse(TEMPLATE_PE).getroot().find(
            './/pds:Context_Area/pds:Discipline_Area/ama:AMA', namespaces=NS)
        self.assertFalse(
            schema.validate(lxml_etree.ElementTree(template_ama)),
            'the base template was expected to fail minLength=1 on its empty elements')

    def test_saving_the_form_through_the_view_rewrites_the_label(self):
        """End to end from an HTTP POST, with nothing stubbed."""
        self.login()
        response = self.client.post(
            reverse('build:ama_model_metadata', kwargs={'pk_bundle': self.bundle.pk}),
            {'model-name': 'MarsWRF', 'model-institution': 'NMSU'})

        self.assertEqual(response.status_code, 302)
        metadata = self.ama_element(self.read_label(), 'ama:Model_Metadata')
        self.assertEqual(metadata.find('ama:name', namespaces=NS).text, 'MarsWRF')


class AMATemplateTests(AMATestCaseMixin, TestCase):
    """Renders the touched templates rather than only loading them, so a bad {% url %} or a
    context key the view forgot to pass shows up as a failure."""

    def setUp(self):
        super().setUp()
        Product_Bundle.objects.create(bundle=self.bundle)
        # The NetCDF card - and therefore the AMA controls inside it - only renders inside the
        # additional-collections loop, so the bundle needs one collection to exercise that markup.
        AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name='data_collection', collection_type='External')

    def test_templates_load(self):
        for name in ('build/bundle/bundle.html', 'build/bundle/netcdf_ama.html'):
            with self.subTest(template=name):
                self.assertIsNotNone(get_template(name))

    def test_bundle_page_renders_the_ama_controls(self):
        self.login()
        response = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}))

        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        for modal_id in ('amaModelMetadataModal', 'amaSimulationModal', 'amaFileDescriptionModal'):
            self.assertIn(modal_id, content)
        self.assertIn(
            reverse('build:ama_model_metadata', kwargs={'pk_bundle': self.bundle.pk}), content)
        self.assertIn(
            reverse('build:netcdf_ama',
                    kwargs={'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_one.pk}),
            content)

    def test_custom_ama_badge_only_shows_for_files_with_an_override(self):
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')

        self.login()
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertEqual(content.count('Custom AMA'), 1)

    def test_status_cards_report_how_much_is_filled_in(self):
        ModelMetadata.objects.create(bundle=self.bundle, name='MarsWRF', institution='NMSU')
        self.make_default_simulation(horizontal_grid_type='lat/lon')

        self.login()
        response = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}))
        status = response.context['ama_status']

        self.assertEqual(status['model_metadata'], {'filled': 2, 'total': 4, 'is_set': True})
        self.assertEqual(status['model_name'], 'MarsWRF')
        self.assertTrue(status['simulation']['is_set'])
        self.assertFalse(status['file_description']['is_set'])
        self.assertIn('2 of 4', response.content.decode('utf-8'))

    def test_status_cards_show_not_set_before_anything_is_entered(self):
        self.login()
        response = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}))
        status = response.context['ama_status']

        for key in ('model_metadata', 'simulation', 'file_description'):
            with self.subTest(section=key):
                self.assertFalse(status[key]['is_set'])
        self.assertEqual(status['override_count'], 0)

    def test_override_count_counts_each_file_once(self):
        """A file with both a Simulation and a File Description override is still one file."""
        SimulationConfiguration.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, horizontal_grid_type='cubed sphere')
        FileDescription.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_one, level_unit='hPa')
        FileDescription.objects.create(
            bundle=self.bundle, netcdf_file=self.nc_two, level_unit='Pa')

        self.login()
        response = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}))

        self.assertEqual(response.context['ama_status']['override_count'], 2)

    def test_modals_render_every_field_through_the_shared_partial(self):
        self.login()
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        for field_name in SimulationConfiguration.ELEMENT_ORDER:
            with self.subTest(field=field_name):
                self.assertIn('name="sim-{}"'.format(field_name), content)
        for field_name in FileDescription.ELEMENT_ORDER:
            with self.subTest(field=field_name):
                self.assertIn('name="desc-{}"'.format(field_name), content)
        # Group headings from FIELD_GROUPS should be present too.
        for heading in ('Grid and Resolution', 'Timing', 'Spatial Extent'):
            self.assertIn(heading, content)

    def test_the_two_modals_do_not_collide_on_element_ids(self):
        """Simulation Configuration and File Description share start_time, end_time and time_unit,
        and both modals are on this page. Without form prefixes the ids collide and a <label for>
        click in one modal focuses the other modal's input."""
        self.login()
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        ids = re.findall(r'\sid="(id_[^"]+)"', content)
        duplicates = sorted({value for value in ids if ids.count(value) > 1})
        self.assertEqual(duplicates, [], 'duplicate form element ids on the bundle page')

        for shared in ('start_time', 'end_time', 'time_unit'):
            with self.subTest(field=shared):
                self.assertIn('id="id_sim-{}"'.format(shared), content)
                self.assertIn('id="id_desc-{}"'.format(shared), content)

    def test_scrollable_modal_bodies_have_the_flex_fix(self):
        """Without this rule the form between .modal-content and .modal-body breaks Bootstrap's
        height chain and the modal body clips instead of scrolling."""
        self.login()
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertIn('.ama-modal .modal-content > form', content)
        self.assertEqual(content.count('modal fade ama-modal'), 3)

    def test_per_file_page_uses_the_same_grouped_layout(self):
        self.login()
        content = self.client.get(
            reverse('build:netcdf_ama',
                    kwargs={'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_one.pk})
        ).content.decode('utf-8')

        for heading in ('Grid and Resolution', 'Timing', 'Spatial Extent', 'Vertical Extent'):
            with self.subTest(heading=heading):
                self.assertIn(heading, content)
        self.assertNotIn('name="apply_to_all"', content,
                         'the per-file page must not offer the bundle-wide toggle')

    def test_upload_cancel_button_is_present_and_wired(self):
        self.login()
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        self.assertIn('id="uploadCancelBtn"', content)
        self.assertIn('_netcdfUploadXhr', content)
        self.assertIn(".abort()", content)

    def test_ama_modals_are_not_nested_inside_the_bulk_delete_form(self):
        """Nested forms are invalid HTML and silently break submission - the file list already
        lives inside the bulk-delete form, so the AMA markup must stay clear of it."""
        self.login()
        content = self.client.get(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk})).content.decode('utf-8')

        bulk_form_start = content.index('id="bulkDeleteNetCDFForm"')
        bulk_form_end = content.index('</form>', bulk_form_start)
        file_list_markup = content[bulk_form_start:bulk_form_end]

        per_file_url = reverse(
            'build:netcdf_ama',
            kwargs={'pk_bundle': self.bundle.pk, 'pk_netcdf': self.nc_one.pk})
        self.assertIn(per_file_url, file_list_markup,
                      'the per-file AMA link should be in the list')
        self.assertNotIn('<form', file_list_markup, 'no form may be nested inside the list form')


# A cut-down stand-in for PDS4_AMA_1O00_1300.xsd: enough Variable/Coordinate sequence for
# regenerate_netcdf_labels to get past the LDD parse without reaching out to pds.nasa.gov.
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

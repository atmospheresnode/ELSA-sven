"""The repair command for labels written before the generator was fixed.

This one rewrites archived files, some belonging to bundles already submitted for
review, so the property that matters most is that it writes nothing unless asked.
"""
import os
import shutil
import tempfile
from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from build.management.commands.rebuild_netcdf_labels import PLACEHOLDER, stale_labels
from build.models import Bundle


class RebuildNetCDFLabelsTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-rebuild-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('rebuilder', password='pw')
        self.bundle = Bundle.objects.create(
            name='rebuild bundle', user=self.user, version='1O00', bundle_type='External')
        os.makedirs(self.bundle.directory(), exist_ok=True)

    def write_label(self, name, lid):
        path = os.path.join(self.bundle.directory(), name)
        with open(path, 'w', encoding='utf-8') as label:
            label.write(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<Product_External xmlns="http://pds.nasa.gov/pds4/pds/v1">\n'
                '  <Identification_Area>\n'
                '    <logical_identifier>{}</logical_identifier>\n'
                '  </Identification_Area>\n'
                '</Product_External>\n'.format(lid))
        return path

    def run_command(self, *args):
        out = StringIO()
        call_command('rebuild_netcdf_labels', *args, stdout=out, stderr=out)
        return out.getvalue()

    # -- detection ---------------------------------------------------------------

    def test_a_stale_label_is_found(self):
        self.write_label('stale.xml', 'urn:nasa:pds-ama:sample_bundle:data:a.nc')
        self.assertEqual(len(stale_labels(self.bundle)), 1)

    def test_a_correct_label_is_not_flagged(self):
        self.write_label('fine.xml', 'urn:nasa:pds-ama:rebuild_bundle:data:a.nc')
        self.assertEqual(stale_labels(self.bundle), [])

    def test_a_bundle_with_no_directory_is_skipped_quietly(self):
        gone = Bundle.objects.create(
            name='no directory', user=self.user, version='1O00')
        self.assertEqual(stale_labels(gone), [])

    # -- the safety property -----------------------------------------------------

    def test_reporting_writes_nothing(self):
        """The whole reason the write is opt-in: these are archived files."""
        path = self.write_label('stale.xml', 'urn:nasa:pds-ama:sample_bundle:data:a.nc')
        before = open(path, encoding='utf-8').read()
        output = self.run_command()
        self.assertEqual(open(path, encoding='utf-8').read(), before)
        self.assertIn('Nothing was written', output)

    def test_reporting_names_the_affected_bundles(self):
        self.write_label('stale.xml', 'urn:nasa:pds-ama:sample_bundle:data:a.nc')
        output = self.run_command()
        self.assertIn('rebuild bundle', output)
        self.assertIn(PLACEHOLDER, output)

    def test_a_clean_archive_says_so(self):
        self.write_label('fine.xml', 'urn:nasa:pds-ama:rebuild_bundle:data:a.nc')
        self.assertIn('No labels carry', self.run_command())

    # -- scoping -----------------------------------------------------------------

    def test_another_bundle_can_be_left_alone(self):
        self.write_label('stale.xml', 'urn:nasa:pds-ama:sample_bundle:data:a.nc')
        other = Bundle.objects.create(
            name='other bundle', user=self.user, version='1O00', bundle_type='External')
        os.makedirs(other.directory(), exist_ok=True)
        with open(os.path.join(other.directory(), 'x.xml'), 'w') as label:
            label.write('<a>urn:nasa:pds-ama:sample_bundle:d:b.nc</a>')

        output = self.run_command('--bundle', str(self.bundle.pk))
        self.assertIn('rebuild bundle', output)
        self.assertNotIn('other bundle', output)

    def test_a_bundle_with_no_netcdf_rows_is_reported_not_crashed(self):
        """The label is on disk but the row it came from is gone."""
        self.write_label('stale.xml', 'urn:nasa:pds-ama:sample_bundle:data:a.nc')
        output = self.run_command('--apply')
        self.assertIn('cannot rebuild', output)
        self.assertNotIn('Traceback', output)

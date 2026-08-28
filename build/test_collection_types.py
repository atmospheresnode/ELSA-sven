# -*- coding: utf-8 -*-
"""End-to-end tests for the PDS4 collection type ELSA writes into generated labels.

These drive the real bundle-creation view and then read the XML that lands on disk, rather than
asserting on model methods: the bug being guarded against was a value that was correct in the
model and wrong in the label.

Two rules are under test:

* <collection_type> must hold a value from the PDS4 Collection/type enumeration. "XML_Schema" is
  not one; "XML Schema" is.
* Every collection in an External (AMA) bundle is of type External, the document collection
  included, because its members carry urn:nasa:pds-ama LIDs that a Document collection does not
  admit. The directory is still `document` and the LID still ends in `:document`.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

from lxml import etree

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import (AdditionalCollections, Bundle, Investigation, Product_Bundle,
                          Product_Collection)

PDS = 'http://pds.nasa.gov/pds4/pds/v1'
NS = {'pds': PDS}

# Product_Collection/Collection/type, IM v1.24.0.0 (1O00). Anything ELSA writes must be in here.
# (The IM spells the SPICE entry "SPICE Kernel"; ELSA never emits it, so nothing depends on it.)
PDS4_COLLECTION_TYPES = frozenset([
    'Browse', 'Calibration', 'Context', 'Data', 'Document',
    'External', 'Geometry', 'Miscellaneous', 'SPICE Kernel', 'XML Schema',
])


def make_ama_investigation():
    """The context product the External branch of the build view writes into the bundle label."""
    return Investigation.objects.create(
        name='Atmospheric Modeling Annex', type_of='Individual Investigation',
        lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
        file_ref='')


class CollectionTypeE2ETests(TestCase):

    def setUp(self):
        self.archive_dir = tempfile.mkdtemp(prefix='elsa-coltype-')
        self.addCleanup(shutil.rmtree, self.archive_dir, True)
        self.media_root = tempfile.mkdtemp(prefix='elsa-coltype-media-')
        self.addCleanup(shutil.rmtree, self.media_root, True)

        patcher = override_settings(ARCHIVE_DIR=self.archive_dir, MEDIA_ROOT=self.media_root)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user(
            username='coltype_tester', password='pw-for-tests', email='coltype@example.com')
        self.client.login(username='coltype_tester', password='pw-for-tests')

        make_ama_investigation()

    # -- helpers ---------------------------------------------------------------------------------

    def build_bundle(self, name, bundle_type):
        """Create a bundle the way a user does: a POST to the build view."""
        response = self.client.post(reverse('build:build'), {
            'name': name, 'bundle_type': bundle_type, 'version': '1O00', 'bundleID': ''})
        self.assertIn(response.status_code, (200, 302))
        bundle = Bundle.objects.get(name=name, user=self.user)
        self.assertTrue(os.path.isdir(bundle.directory()),
                        'bundle directory was not created: {}'.format(bundle.directory()))
        return bundle

    def collection_types(self, bundle):
        """{directory name: <collection_type>} read out of the labels actually on disk."""
        found = {}
        for dirpath, _dirnames, filenames in os.walk(bundle.directory()):
            for filename in filenames:
                if not filename.startswith('collection_') or not filename.endswith('.xml'):
                    continue
                root = ET.parse(os.path.join(dirpath, filename)).getroot()
                element = root.find('pds:Collection/pds:collection_type', NS)
                self.assertIsNotNone(
                    element, 'no <collection_type> in {}'.format(filename))
                found[os.path.basename(dirpath)] = (element.text or '').strip()
        return found

    def collection_lids(self, bundle):
        found = {}
        for dirpath, _dirnames, filenames in os.walk(bundle.directory()):
            for filename in filenames:
                if not filename.startswith('collection_') or not filename.endswith('.xml'):
                    continue
                root = ET.parse(os.path.join(dirpath, filename)).getroot()
                lid = root.find('pds:Identification_Area/pds:logical_identifier', NS)
                found[os.path.basename(dirpath)] = (lid.text or '').strip()
        return found

    def member_entries(self, bundle):
        """{last LID segment: reference_type} from the bundle label's Bundle_Member_Entry list."""
        path = Product_Bundle.objects.get(bundle=bundle).label()
        self.assertTrue(os.path.isfile(path), 'bundle label missing: {}'.format(path))
        root = ET.parse(path).getroot()
        found = {}
        for entry in root.findall('pds:Bundle_Member_Entry', NS):
            lid = entry.find('pds:lid_reference', NS).text.strip()
            found[lid.rsplit(':', 1)[-1]] = entry.find('pds:reference_type', NS).text.strip()
        return found

    # -- Archive bundles: unchanged apart from the XML Schema spelling -----------------------------

    def test_archive_collection_types(self):
        bundle = self.build_bundle('coltype archive', 'Archive')
        self.assertEqual(self.collection_types(bundle), {
            'document': 'Document',
            'context': 'Context',
            'xml_schema': 'XML Schema',
        })

    def test_archive_member_entries(self):
        bundle = self.build_bundle('coltype archive refs', 'Archive')
        self.assertEqual(self.member_entries(bundle), {
            'document': 'bundle_has_document_collection',
            'context': 'bundle_has_context_collection',
            'xml_schema': 'bundle_has_schema_collection',
        })

    # -- External bundles: every collection is External --------------------------------------------

    def test_external_document_collection_is_typed_external(self):
        bundle = self.build_bundle('coltype external', 'External')
        self.assertEqual(self.collection_types(bundle), {'document': 'External'})

    def test_external_member_entry_matches_the_collection_type(self):
        bundle = self.build_bundle('coltype external refs', 'External')
        self.assertEqual(self.member_entries(bundle),
                         {'document': 'bundle_has_external_collection'})

    def test_external_document_keeps_its_name_and_lid(self):
        """Only the declared type changes: the directory and the LID stay `document`."""
        bundle = self.build_bundle('coltype external lid', 'External')

        self.assertTrue(os.path.isdir(os.path.join(bundle.directory(), 'document')))
        lids = self.collection_lids(bundle)
        self.assertEqual(list(lids), ['document'])
        self.assertTrue(lids['document'].startswith('urn:nasa:pds-ama:'), lids['document'])
        self.assertTrue(lids['document'].endswith(':document'), lids['document'])

    def test_user_added_external_collection_is_still_external(self):
        """A collection the user adds to an AMA bundle agrees with the document collection."""
        bundle = self.build_bundle('coltype external added', 'External')
        self.client.post(reverse('build:bundle', kwargs={'pk_bundle': bundle.pk}),
                         {'collection_name': 'mydata', 'collection_type': 'External'})

        collection = AdditionalCollections.objects.filter(bundle=bundle).first()
        self.assertIsNotNone(collection, 'the collection was not created')
        self.assertEqual(self.collection_types(bundle)['mydata'], 'External')

    # -- the guard that catches any future drift ---------------------------------------------------

    def test_no_bundle_emits_a_value_outside_the_enumeration(self):
        for name, bundle_type in (('enum archive', 'Archive'), ('enum external', 'External')):
            bundle = self.build_bundle(name, bundle_type)
            for directory, value in self.collection_types(bundle).items():
                self.assertIn(value, PDS4_COLLECTION_TYPES,
                              '{} collection of the {} bundle emitted {!r}, which is not a PDS4 '
                              'Collection/type value'.format(directory, bundle_type, value))

    def test_the_underscored_spelling_appears_nowhere_in_a_label(self):
        bundle = self.build_bundle('enum underscore', 'Archive')
        for dirpath, _dirnames, filenames in os.walk(bundle.directory()):
            for filename in filenames:
                if not filename.endswith('.xml'):
                    continue
                path = os.path.join(dirpath, filename)
                with open(path, encoding='utf-8') as label:
                    self.assertNotIn('XML_Schema', label.read(),
                                     'XML_Schema still written into {}'.format(path))


class CollectionTypeRepairMigrationTests(TestCase):
    """The 0073 repair, run against labels holding the values the old code wrote."""

    def setUp(self):
        self.archive_dir = tempfile.mkdtemp(prefix='elsa-repair-')
        self.addCleanup(shutil.rmtree, self.archive_dir, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive_dir)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user(
            username='repair_tester', password='pw-for-tests', email='repair@example.com')
        self.client.login(username='repair_tester', password='pw-for-tests')

        make_ama_investigation()

    def migration(self):
        import importlib
        return importlib.import_module('build.migrations.0073_repair_collection_types')

    def build_bundle(self, name, bundle_type):
        self.client.post(reverse('build:build'), {
            'name': name, 'bundle_type': bundle_type, 'version': '1O00', 'bundleID': ''})
        return Bundle.objects.get(name=name, user=self.user)

    def set_text(self, path, xpath, value):
        """Write a value straight into a label, standing in for what the old code produced.

        lxml rather than the stdlib writer: the stdlib one cannot round-trip a document whose
        elements sit in a default namespace, and these labels all do.
        """
        tree = etree.parse(path)
        element = tree.getroot().find(xpath, NS)
        element.text = value
        tree.write(path, encoding='utf-8', xml_declaration=True)

    def read_text(self, path, xpath):
        return (ET.parse(path).getroot().find(xpath, NS).text or '').strip()

    def test_it_repairs_an_external_document_collection(self):
        migration = self.migration()
        bundle = self.build_bundle('repair external', 'External')
        collection = Product_Collection.objects.get(bundle=bundle, collection='Document')
        path = collection.label()

        # Put the old, invalid value back.
        self.set_text(path, 'pds:Collection/pds:collection_type', 'Document')
        self.assertEqual(self.read_text(path, 'pds:Collection/pds:collection_type'), 'Document')

        self.assertTrue(migration.repair_collection_label(path, 'External'))
        self.assertEqual(self.read_text(path, 'pds:Collection/pds:collection_type'), 'External')

        # Running it twice must not touch the file again.
        self.assertFalse(migration.repair_collection_label(path, 'External'))

    def test_it_repairs_an_underscored_schema_collection(self):
        migration = self.migration()
        bundle = self.build_bundle('repair archive', 'Archive')
        collection = Product_Collection.objects.get(bundle=bundle, collection='XML_Schema')
        path = collection.label()

        self.set_text(path, 'pds:Collection/pds:collection_type', 'XML_Schema')
        self.assertTrue(migration.repair_collection_label(path, 'XML Schema'))
        self.assertEqual(self.read_text(path, 'pds:Collection/pds:collection_type'), 'XML Schema')
        self.assertFalse(migration.repair_collection_label(path, 'XML Schema'))

    def test_it_rebuilds_the_paths_the_models_use(self):
        """The migration reconstructs label paths by hand; they must match the model methods."""
        migration = self.migration()
        for name, bundle_type in (('path archive', 'Archive'), ('path external', 'External')):
            bundle = self.build_bundle(name, bundle_type)

            self.assertEqual(migration.bundle_label(bundle),
                             Product_Bundle.objects.get(bundle=bundle).label())
            for collection in Product_Collection.objects.filter(bundle=bundle):
                self.assertEqual(migration.collection_label(bundle, collection),
                                 collection.label())
                self.assertTrue(os.path.isfile(collection.label()))

    def test_it_leaves_a_correct_label_alone(self):
        migration = self.migration()
        bundle = self.build_bundle('already correct', 'Archive')
        for collection in Product_Collection.objects.filter(bundle=bundle):
            wanted = collection.collection_type()
            self.assertFalse(migration.repair_collection_label(collection.label(), wanted),
                             '{} was rewritten despite already being correct'.format(wanted))

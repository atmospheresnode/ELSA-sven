# -*- coding: utf-8 -*-
"""Tests for the label defects repaired in the Phase 0 generator work.

Each of these guards a specific thing PDS validate reported against a real bundle,
where the value was wrong in the XML that lands on disk rather than in the model:

* Empty optional containers. An element with no text is not a valid value in PDS4,
  so a container ELSA never fills has to be left out rather than shipped blank.
  Time_Coordinates and Reference_List are optional in Context_Area and on
  Product_Bundle/Product_Collection; the same element is *required* inside
  Observation_Area, so the observational templates must keep it.
* build_internal_reference building elements outside the PDS namespace, which made
  two documents collapse into one mangled reference.
* Collection inventories, which ELSA never wrote at all even though
  File_Area_Inventory is required on every Product_Collection.
* The AMA context LID, which named an investigation PDS does not publish.

The tests that only read templates or call pure helpers need no database. The ones
that touch Investigation do.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile

from lxml import etree

from django.test import TestCase, SimpleTestCase

from build.chocolate import write_collection_inventory
from build.models import Investigation, Product_Bundle, Version

PDS = 'http://pds.nasa.gov/pds4/pds/v1'
NS = {'pds': PDS}

TEMPLATE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'templates', 'pds4_labels')

# Bundle and collection labels: Context_Area makes these optional.
CONTAINER_TEMPLATES = [
    os.path.join(TEMPLATE_DIR, 'base_case', 'product_bundle.xml'),
    os.path.join(TEMPLATE_DIR, 'base_case', 'product_collection.xml'),
    os.path.join(TEMPLATE_DIR, 'base_templates', 'product_external_bundle.xml'),
    os.path.join(TEMPLATE_DIR, 'base_templates', 'product_external_collection.xml'),
]

# Observational labels: Observation_Area requires Time_Coordinates, so these keep it.
OBSERVATIONAL_TEMPLATES = [
    os.path.join(TEMPLATE_DIR, 'base_case', 'product_observational.xml'),
    os.path.join(TEMPLATE_DIR, 'base_templates', 'data_table_binary.xml'),
    os.path.join(TEMPLATE_DIR, 'base_templates', 'data_table_character.xml'),
    os.path.join(TEMPLATE_DIR, 'base_templates', 'data_table_delimited.xml'),
]


def parse(path):
    return etree.parse(path).getroot()


class EmptyContainerTests(SimpleTestCase):
    """No bundle or collection template may ship a container it never fills."""

    def test_no_time_coordinates_in_bundle_or_collection_templates(self):
        for path in CONTAINER_TEMPLATES:
            with self.subTest(template=os.path.basename(path)):
                root = parse(path)
                self.assertIsNone(
                    root.find('.//pds:Time_Coordinates', NS),
                    'Time_Coordinates is optional here and ELSA never fills it, so it '
                    'must not be in the template.')

    def test_observational_templates_keep_time_coordinates(self):
        """The counterpart guard: removing it from these would break them."""
        for path in OBSERVATIONAL_TEMPLATES:
            with self.subTest(template=os.path.basename(path)):
                root = parse(path)
                self.assertIsNotNone(
                    root.find('.//pds:Time_Coordinates', NS),
                    'Observation_Area requires Time_Coordinates (minOccurs=1).')

    def test_reference_list_has_no_placeholder_internal_reference(self):
        for path in CONTAINER_TEMPLATES:
            with self.subTest(template=os.path.basename(path)):
                root = parse(path)
                reference_list = root.find('pds:Reference_List', NS)
                if reference_list is None:
                    continue
                self.assertEqual(
                    reference_list.findall('pds:Internal_Reference', NS), [],
                    'build_internal_reference creates its own references now; a '
                    'placeholder here ships as an empty reference when unused.')

    def test_context_area_never_left_empty(self):
        """Removing Time_Coordinates must not strand an empty Context_Area.

        fill_label falls back to Observation_Area when Context_Area is absent, and an
        empty one would send it looking for an element that is not there.
        """
        for path in CONTAINER_TEMPLATES:
            with self.subTest(template=os.path.basename(path)):
                context_area = parse(path).find('pds:Context_Area', NS)
                if context_area is not None:
                    self.assertGreater(len(context_area), 0)


class InternalReferenceTests(SimpleTestCase):
    """Two documents must produce two complete references, not one mangled one."""

    class Relation(object):
        def __init__(self, name):
            self.name = name

        def lid(self):
            return 'urn:nasa:pds:demo:bundle:document:{}'.format(self.name)

        def reference_type(self):
            return 'document'

    def build(self, count):
        root = etree.fromstring(
            '<Product_Bundle xmlns="{}"><Reference_List></Reference_List>'
            '</Product_Bundle>'.format(PDS).encode('utf-8'))
        product_bundle = Product_Bundle()
        for index in range(count):
            root = product_bundle.build_internal_reference(
                root, self.Relation('doc{}'.format(index)))
        return root

    def test_each_relation_gets_its_own_reference(self):
        references = self.build(2).findall(
            'pds:Reference_List/pds:Internal_Reference', NS)
        self.assertEqual(len(references), 2)

    def test_references_are_complete_and_namespaced(self):
        for reference in self.build(2).findall(
                'pds:Reference_List/pds:Internal_Reference', NS):
            lid = reference.find('pds:lid_reference', NS)
            reference_type = reference.find('pds:reference_type', NS)
            self.assertIsNotNone(lid, 'lid_reference must be in the PDS namespace')
            self.assertIsNotNone(reference_type)
            self.assertTrue(lid.text)
            self.assertEqual(reference_type.text, 'bundle_to_document')

    def test_no_empty_stray_elements(self):
        """The old code left behind bare elements the namespaced find could not see."""
        root = self.build(2)
        for element in root.iter():
            if isinstance(element.tag, str):
                self.assertTrue(
                    element.tag.startswith('{' + PDS + '}'),
                    'every element must carry the PDS namespace, found {}'.format(
                        element.tag))


class InventoryTests(SimpleTestCase):
    """The inventory table and the label describing it must agree."""

    def setUp(self):
        self.workdir = tempfile.mkdtemp(prefix='elsa-inventory-')
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.label = os.path.join(self.workdir, 'collection_demo_document.xml')
        Version().version_update_old(
            '1O00',
            os.path.join(TEMPLATE_DIR, 'base_templates', 'product_external_collection.xml'),
            self.label)

    def inventory_fields(self):
        root = parse(self.label)
        area = root.find('pds:File_Area_Inventory', NS)
        return area.find('pds:File', NS), area.find('pds:Inventory', NS)

    def test_table_lists_every_member_once(self):
        members = ['urn:nasa:pds-ama:demo:document:readme::1.0',
                   'urn:nasa:pds-ama:demo:document:guide::1.0']
        path = write_collection_inventory(self.label, members)
        with open(path, 'rb') as table:
            rows = table.read().decode('utf-8').split('\r\n')[:-1]
        self.assertEqual(rows, ['P,' + member for member in members])

    def test_rows_end_with_crlf_as_the_label_declares(self):
        write_collection_inventory(self.label, ['urn:nasa:pds:a:b:c::1.0'])
        with open(os.path.join(self.workdir, 'collection_demo_document.csv'), 'rb') as table:
            self.assertTrue(table.read().endswith(b'\r\n'))

    def test_label_points_at_the_file_actually_written(self):
        path = write_collection_inventory(self.label, ['urn:nasa:pds:a:b:c::1.0'])
        file_element, _ = self.inventory_fields()
        self.assertEqual(
            file_element.find('pds:file_name', NS).text, os.path.basename(path))
        self.assertTrue(os.path.exists(path))

    def test_record_count_matches_the_table(self):
        members = ['urn:nasa:pds:a:b:c{}::1.0'.format(n) for n in range(5)]
        write_collection_inventory(self.label, members)
        _, inventory = self.inventory_fields()
        self.assertEqual(inventory.find('pds:records', NS).text, '5')

    def test_maximum_record_length_covers_the_longest_row(self):
        members = ['urn:nasa:pds:a:b:short::1.0',
                   'urn:nasa:pds:a:b:a_considerably_longer_product_name::1.0']
        write_collection_inventory(self.label, members)
        _, inventory = self.inventory_fields()
        declared = int(inventory.find(
            'pds:Record_Delimited/pds:maximum_record_length', NS).text)
        longest = max(len('P,' + member) + 2 for member in members)
        self.assertEqual(declared, longest)

    def test_empty_local_identifiers_are_dropped(self):
        """Optional and unfilled, so shipping them blank would be an error."""
        write_collection_inventory(self.label, ['urn:nasa:pds:a:b:c::1.0'])
        file_element, inventory = self.inventory_fields()
        self.assertIsNone(file_element.find('pds:local_identifier', NS))
        self.assertIsNone(inventory.find('pds:local_identifier', NS))

    def test_no_element_is_left_empty(self):
        write_collection_inventory(self.label, ['urn:nasa:pds:a:b:c::1.0'])
        root = parse(self.label)
        area = root.find('pds:File_Area_Inventory', NS)
        for element in area.iter():
            if len(element) == 0:
                self.assertTrue(
                    (element.text or '').strip(),
                    '{} ships with no value'.format(etree.QName(element).localname))

    def test_rewriting_replaces_rather_than_appends(self):
        """Membership changes call this repeatedly; it must not accumulate."""
        write_collection_inventory(self.label, ['urn:nasa:pds:a:b:one::1.0'])
        write_collection_inventory(self.label, ['urn:nasa:pds:a:b:two::1.0'])
        _, inventory = self.inventory_fields()
        self.assertEqual(inventory.find('pds:records', NS).text, '1')
        with open(os.path.join(self.workdir, 'collection_demo_document.csv')) as table:
            self.assertIn('two', table.read())

    def test_empty_collection_reports_zero_rather_than_inventing_a_count(self):
        """A zero-record inventory does not validate, and that is the honest answer.

        PDS requires records >= 1, so an empty collection cannot be valid. Writing the
        true count surfaces that instead of hiding it behind the template's hardcoded 3.
        """
        write_collection_inventory(self.label, [])
        _, inventory = self.inventory_fields()
        self.assertEqual(inventory.find('pds:records', NS).text, '0')


class AMAContextLidTests(TestCase):
    """The AMA investigation must name a context product PDS actually publishes."""

    REGISTERED_LID = (
        'urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex')

    def setUp(self):
        # Created here rather than read from the database, so the test states what
        # correct looks like instead of agreeing with whatever happens to be stored.
        self.investigation = Investigation.objects.create(
            name='Atmospheric Modeling Annex',
            type_of='Individual Investigation',
            lid=self.REGISTERED_LID,
            file_ref='')

    def build_collection_label(self, investigation):
        workdir = tempfile.mkdtemp(prefix='elsa-ama-lid-')
        self.addCleanup(shutil.rmtree, workdir, True)
        label = os.path.join(workdir, 'collection_demo_document.xml')
        Version().version_update_old(
            '1O00',
            os.path.join(TEMPLATE_DIR, 'base_templates', 'product_external_collection.xml'),
            label)
        tree = etree.parse(label)
        investigation.fill_label(tree.getroot())
        tree.write(label, xml_declaration=True, encoding='utf-8')
        return parse(label).find('pds:Context_Area/pds:Investigation_Area', NS)

    def test_lid_reaches_the_collection_label(self):
        area = self.build_collection_label(self.investigation)
        self.assertEqual(
            area.find('pds:Internal_Reference/pds:lid_reference', NS).text,
            self.REGISTERED_LID)

    def test_investigation_area_is_no_longer_blank(self):
        """C3: name, type and reference_type shipped empty in collection labels."""
        area = self.build_collection_label(self.investigation)
        self.assertEqual(area.find('pds:name', NS).text, 'Atmospheric Modeling Annex')
        self.assertEqual(area.find('pds:type', NS).text, 'Individual Investigation')
        self.assertEqual(
            area.find('pds:Internal_Reference/pds:reference_type', NS).text,
            'collection_to_investigation')

    def test_reference_type_follows_the_label_kind(self):
        """The same call has to say bundle_to_investigation on a bundle label."""
        root = etree.fromstring(
            '<Product_Bundle xmlns="{}"><Context_Area><Investigation_Area>'
            '<name/><type/><Internal_Reference><lid_reference/><reference_type/>'
            '</Internal_Reference></Investigation_Area></Context_Area></Product_Bundle>'
            .format(PDS).encode('utf-8'))
        self.investigation.fill_label(root)
        self.assertEqual(
            root.find('pds:Context_Area/pds:Investigation_Area/pds:Internal_Reference/'
                      'pds:reference_type', NS).text,
            'bundle_to_investigation')

    def test_the_incorrect_lid_is_not_reintroduced(self):
        """Names the exact string migration 0075 repaired, so a regression is obvious."""
        self.assertNotEqual(
            self.investigation.lid,
            'urn:nasa:pds:context:investigation:'
            'individual_investigation.atmospheric-modeling-annex')

    def test_stored_row_matches_if_this_database_has_one(self):
        """Guards the production row itself, where the migration has run."""
        stored = Investigation.objects.filter(
            name='Atmospheric Modeling Annex').exclude(pk=self.investigation.pk).first()
        if stored is None:
            self.skipTest('no pre-existing AMA investigation row in this database')
        self.assertEqual(stored.lid, self.REGISTERED_LID)

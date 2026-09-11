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
from build.models import (Investigation, PDS_TARGET_TYPES, Product_Bundle,
                          Version, insert_in_context_area, pds_target_type,
                          target_reference_type)

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

    def test_no_empty_modification_history_in_templates(self):
        """Modification_History requires at least one Modification_Detail.

        An empty one is therefore an error on every bundle that has not had an entry
        added yet, and it is optional in Identification_Area, so it is created when
        there is something to put in it rather than shipped blank.
        """
        for path in CONTAINER_TEMPLATES + [
                os.path.join(TEMPLATE_DIR, 'base_templates', 'Template_PE.xml'),
                os.path.join(TEMPLATE_DIR, 'base_templates', 'Template_PE_document.xml')]:
            with self.subTest(template=os.path.basename(path)):
                self.assertIsNone(parse(path).find('.//pds:Modification_History', NS))

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

    def test_no_empty_investigation_area_or_observing_system(self):
        """Archive templates shipped both of these with empty children.

        Both are optional in Context_Area and neither is populated until the user adds
        context products, so they are created on demand instead. The External templates
        keep an Investigation_Area because the AMA investigation is written into it at
        build time, so it never ships empty.
        """
        archive_templates = [
            os.path.join(TEMPLATE_DIR, 'base_case', 'product_bundle.xml'),
            os.path.join(TEMPLATE_DIR, 'base_case', 'product_collection.xml'),
        ]
        for path in archive_templates:
            with self.subTest(template=os.path.basename(path)):
                root = parse(path)
                self.assertIsNone(root.find('.//pds:Investigation_Area', NS))
                self.assertIsNone(root.find('.//pds:Observing_System', NS))

    def test_an_empty_context_area_is_tolerated(self):
        """Context_Area can now legitimately be empty, and nothing may assume otherwise.

        fill_label used to fall through to Observation_Area on a falsy Context_Area,
        which an empty element is. That is fixed with an explicit None check, so this
        guards the assumption rather than the emptiness.
        """
        from build.models import Investigation
        root = etree.fromstring(
            ('<Product_Bundle xmlns="{}"><Context_Area></Context_Area>'
             '</Product_Bundle>').format(PDS).encode('utf-8'))
        Investigation(name='Demo Investigation', type_of='Individual Investigation',
                      lid='urn:nasa:pds:context:investigation:individual.demo').fill_label(root)
        area = root.find('pds:Context_Area/pds:Investigation_Area', NS)
        self.assertIsNotNone(area, 'fill_label must build the area it no longer finds')
        self.assertEqual(area.find('pds:name', NS).text, 'Demo Investigation')
        self.assertEqual(
            area.find('pds:Internal_Reference/pds:reference_type', NS).text,
            'bundle_to_investigation')

    def test_removing_an_investigation_takes_the_whole_area_out(self):
        """Blanking the fields would leave a container full of empty elements."""
        from build.models import Investigation
        root = etree.fromstring(
            ('<Product_Bundle xmlns="{}"><Context_Area></Context_Area>'
             '</Product_Bundle>').format(PDS).encode('utf-8'))
        investigation = Investigation(
            name='Demo Investigation', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.demo')
        investigation.fill_label(root)
        investigation.remove_xml(root)
        self.assertIsNone(root.find('pds:Context_Area/pds:Investigation_Area', NS))


class ModificationHistoryTests(SimpleTestCase):
    """fill_label has to create the container the templates no longer carry."""

    def label_root(self, with_container=False):
        inner = '<Modification_History/>' if with_container else ''
        return etree.fromstring(
            ('<Product_Bundle xmlns="{}"><Identification_Area>'
             '<product_class>Product_Bundle</product_class>{}'
             '</Identification_Area></Product_Bundle>').format(PDS, inner).encode('utf-8'))

    def fill(self, root):
        from build.models import Modification_History
        return Modification_History(
            modification_date='2026-09-10', version_id='1.0',
            description='Initial release').fill_label(root)

    def test_container_is_created_when_absent(self):
        root = self.fill(self.label_root(with_container=False))
        self.assertIsNotNone(root.find('pds:Identification_Area/pds:Modification_History', NS))

    def test_detail_carries_the_values(self):
        root = self.fill(self.label_root())
        detail = root.find(
            'pds:Identification_Area/pds:Modification_History/pds:Modification_Detail', NS)
        self.assertEqual(detail.find('pds:modification_date', NS).text, '2026-09-10')
        self.assertEqual(detail.find('pds:description', NS).text, 'Initial release')

    def test_everything_written_is_namespaced(self):
        root = self.fill(self.label_root())
        for element in root.iter():
            if isinstance(element.tag, str):
                self.assertTrue(element.tag.startswith('{' + PDS + '}'), element.tag)

    def test_two_entries_produce_two_details(self):
        root = self.label_root()
        root = self.fill(root)
        root = self.fill(root)
        self.assertEqual(len(root.findall(
            'pds:Identification_Area/pds:Modification_History/pds:Modification_Detail', NS)), 2)


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


class TargetLabelTests(SimpleTestCase):
    """What ELSA writes into a label when someone picks a target off the list.

    Both of these were wrong for every target in the registry: the type was written
    in whatever case the crawler stored ('ASTEROID'), and the reference type was
    'is_target', which is not a PDS4 value at all. Between them they meant that
    selecting a target, the one action the UI invites, added errors to the bundle and
    the panel then reported them as the user's doing.
    """

    def test_a_registry_type_is_written_in_the_spelling_pds_accepts(self):
        self.assertEqual(pds_target_type('ASTEROID'), 'Asteroid')
        self.assertEqual(pds_target_type('TRANS-NEPTUNIAN OBJECT'), 'Trans-Neptunian Object')
        self.assertEqual(pds_target_type('CALIBRATION FIELD'), 'Calibration Field')

    def test_a_type_already_correct_is_left_alone(self):
        for value in ('Planet', 'Centaur', 'Laboratory Analog'):
            self.assertEqual(pds_target_type(value), value)

    def test_every_value_it_produces_for_a_known_type_is_one_pds_lists(self):
        for value in PDS_TARGET_TYPES:
            self.assertIn(pds_target_type(value.upper()), PDS_TARGET_TYPES)
            self.assertIn(pds_target_type(value.lower()), PDS_TARGET_TYPES)

    def test_an_unknown_type_is_passed_through_rather_than_guessed(self):
        """PDS should report a value it does not know, not have ELSA invent one."""
        self.assertEqual(pds_target_type('Some New Kind'), 'Some New Kind')

    def test_a_missing_type_does_not_raise(self):
        self.assertEqual(pds_target_type(''), '')
        self.assertIsNone(pds_target_type(None))

    def test_the_reference_type_depends_on_what_is_referring(self):
        cases = {
            'Product_Bundle': 'bundle_to_target',
            'Product_Collection': 'collection_to_target',
            'Product_Observational': 'data_to_target',
            'Product_Document': 'document_to_target',
            # The class every AMA data product uses. It was missing from the table
            # and fell through to collection_to_target, which PDS rejects.
            'Product_External': 'external_to_target',
        }
        for tag, expected in cases.items():
            root = etree.fromstring(
                '<{0} xmlns="http://pds.nasa.gov/pds4/pds/v1"/>'.format(tag).encode())
            self.assertEqual(target_reference_type(root), expected)

    def test_every_product_class_elsa_writes_is_in_the_table(self):
        """A fallback that happens to be wrong is invisible until PDS says so."""
        from build.models import TARGET_REFERENCE_TYPES
        for tag in ('Product_Bundle', 'Product_Collection', 'Product_Observational',
                    'Product_Document', 'Product_External'):
            self.assertIn(tag, TARGET_REFERENCE_TYPES,
                          '{} falls through to the fallback'.format(tag))

    def test_is_target_is_never_written(self):
        """It was the default for everything that was not a Product_Observational."""
        for tag in ('Product_Bundle', 'Product_Collection', 'Product_Document',
                    'Product_Something_Unexpected'):
            root = etree.fromstring(
                '<{0} xmlns="http://pds.nasa.gov/pds4/pds/v1"/>'.format(tag).encode())
            self.assertNotEqual(target_reference_type(root), 'is_target')
            self.assertTrue(target_reference_type(root).endswith('_to_target'))


class ContextAreaOrderTests(SimpleTestCase):
    """Context_Area is an xs:sequence, so its children have a fixed order.

    Appending a Target_Identification was correct only while nothing that sorts
    after it was present. AMA labels always carry a Discipline_Area, which sorts
    last, so selecting a target on an AMA bundle put the target after it and every
    data product reported "Invalid content was found starting with element
    'Target_Identification'".
    """

    PDS = 'http://pds.nasa.gov/pds4/pds/v1'

    def context_area(self, *children):
        markup = '<Product_External xmlns="{}"><Context_Area>{}</Context_Area>' \
                 '</Product_External>'.format(
                     self.PDS, ''.join('<{}/>'.format(c) for c in children))
        return etree.fromstring(markup.encode())[0]

    def add(self, area, name):
        insert_in_context_area(area, etree.SubElement(area, name))
        return [etree.QName(c).localname for c in area]

    def test_a_target_goes_before_the_discipline_area(self):
        area = self.context_area('Investigation_Area', 'Discipline_Area')
        self.assertEqual(self.add(area, 'Target_Identification'),
                         ['Investigation_Area', 'Target_Identification',
                          'Discipline_Area'])

    def test_a_target_goes_after_the_investigation_area(self):
        area = self.context_area('Investigation_Area')
        self.assertEqual(self.add(area, 'Target_Identification'),
                         ['Investigation_Area', 'Target_Identification'])

    def test_a_target_goes_before_a_mission_area_too(self):
        area = self.context_area('Investigation_Area', 'Mission_Area')
        self.assertEqual(self.add(area, 'Target_Identification'),
                         ['Investigation_Area', 'Target_Identification',
                          'Mission_Area'])

    def test_an_empty_context_area_just_takes_it(self):
        area = self.context_area()
        self.assertEqual(self.add(area, 'Target_Identification'),
                         ['Target_Identification'])

    def test_a_second_target_lands_beside_the_first(self):
        area = self.context_area('Investigation_Area', 'Discipline_Area')
        self.add(area, 'Target_Identification')
        self.assertEqual(self.add(area, 'Target_Identification'),
                         ['Investigation_Area', 'Target_Identification',
                          'Target_Identification', 'Discipline_Area'])

    def test_the_result_is_always_in_schema_order(self):
        """Whatever is already there, the sequence has to come out sorted."""
        from build.models import CONTEXT_AREA_ORDER
        area = self.context_area('Time_Coordinates', 'Investigation_Area',
                                 'Mission_Area', 'Discipline_Area')
        names = self.add(area, 'Target_Identification')
        positions = [CONTEXT_AREA_ORDER.index(n) for n in names]
        self.assertEqual(positions, sorted(positions), names)

# -*- coding: utf-8 -*-
"""Repairing labels written before the generator was fixed.

Measured on a real Archive bundle: 119 findings before, 16 after, and the 16 are
the same set a bundle built today produces -- all of them the user's own. Nothing
attributable to ELSA survives.

The property that matters most is not how much it removes but what it refuses to
touch. Author and editor names live only in the XML, never in the database, so a
pass over these files that is careless with Citation_Information destroys data no
backup of the database would restore.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile

from lxml import etree

from django.test import SimpleTestCase

from build.label_repair import (OPTIONAL_CONTAINERS, PROTECTED, prune_blank_containers,
                                repair_label)

PDS = 'http://pds.nasa.gov/pds4/pds/v1'


def parse(markup):
    return etree.fromstring(markup.encode())


def names(root):
    return [etree.QName(e).localname for e in root.iter()]


class PruningTests(SimpleTestCase):

    def test_an_empty_optional_container_is_removed(self):
        root = parse(
            '<Product_Bundle xmlns="{}"><Context_Area>'
            '<Time_Coordinates><start_date_time/><stop_date_time/></Time_Coordinates>'
            '</Context_Area></Product_Bundle>'.format(PDS))
        prune_blank_containers(root)
        self.assertNotIn('Time_Coordinates', names(root))

    def test_a_container_with_a_value_is_kept(self):
        root = parse(
            '<Product_Bundle xmlns="{}"><Context_Area>'
            '<Time_Coordinates><start_date_time>2026-01-01Z</start_date_time>'
            '</Time_Coordinates></Context_Area></Product_Bundle>'.format(PDS))
        prune_blank_containers(root)
        self.assertIn('Time_Coordinates', names(root))
        self.assertIn('start_date_time', names(root))

    def test_a_container_blank_only_once_its_children_go(self):
        """Observing_System holding one empty component is itself blank."""
        root = parse(
            '<Product_Bundle xmlns="{}"><Context_Area><Observing_System>'
            '<name/><Observing_System_Component><name/><type/>'
            '</Observing_System_Component></Observing_System>'
            '</Context_Area></Product_Bundle>'.format(PDS))
        prune_blank_containers(root)
        self.assertNotIn('Observing_System', names(root))

    def test_one_blank_sibling_goes_and_the_other_stays(self):
        root = parse(
            '<Reference_List xmlns="{}">'
            '<Internal_Reference><lid_reference/><reference_type/></Internal_Reference>'
            '<Internal_Reference><lid_reference>urn:a:b:c</lid_reference>'
            '<reference_type>bundle_to_document</reference_type></Internal_Reference>'
            '</Reference_List>'.format(PDS))
        prune_blank_containers(root)
        self.assertEqual(len(root.findall('{{{}}}Internal_Reference'.format(PDS))), 1)
        self.assertIn('urn:a:b:c', etree.tostring(root).decode())

    def test_a_required_element_is_left_alone_even_when_empty(self):
        """Dropping it trades one error for another and loses information."""
        root = parse(
            '<Product_Bundle xmlns="{}"><Identification_Area>'
            '<logical_identifier/><version_id/><title/>'
            '</Identification_Area></Product_Bundle>'.format(PDS))
        prune_blank_containers(root)
        for required in ('logical_identifier', 'version_id', 'title'):
            self.assertIn(required, names(root), required)

    def test_it_is_idempotent(self):
        markup = ('<Product_Bundle xmlns="{}"><Context_Area>'
                  '<Time_Coordinates><start_date_time/></Time_Coordinates>'
                  '</Context_Area></Product_Bundle>'.format(PDS))
        root = parse(markup)
        first = prune_blank_containers(root)
        second = prune_blank_containers(root)
        self.assertTrue(first)
        self.assertEqual(second, [], 'a second pass changed the label again')

    def test_every_name_it_prunes_is_one_pds4_allows_to_be_absent(self):
        """Checked against the schema when written; this pins it."""
        for name in OPTIONAL_CONTAINERS:
            self.assertNotIn(name, PROTECTED, name)


class ProtectedContentTests(SimpleTestCase):
    """What must survive the pass, whatever it looks like."""

    def test_an_empty_citation_is_never_removed(self):
        """Author names live only in the XML. This pass must not be the thing
        that loses them, and an empty-looking citation is the dangerous case."""
        root = parse(
            '<Product_Bundle xmlns="{}"><Identification_Area>'
            '<Citation_Information><publication_year/><description/>'
            '</Citation_Information></Identification_Area></Product_Bundle>'.format(PDS))
        prune_blank_containers(root)
        self.assertIn('Citation_Information', names(root))

    def test_a_blank_author_inside_a_citation_is_left_alone(self):
        root = parse(
            '<Product_Bundle xmlns="{}"><Identification_Area>'
            '<Citation_Information><List_Author><Person>'
            '<given_name/><family_name/></Person></List_Author>'
            '</Citation_Information></Identification_Area></Product_Bundle>'.format(PDS))
        prune_blank_containers(root)
        self.assertIn('List_Author', names(root))
        self.assertIn('given_name', names(root))

    def test_a_filled_citation_is_untouched(self):
        markup = ('<Product_Bundle xmlns="{}"><Identification_Area>'
                  '<Citation_Information><List_Author><Person>'
                  '<given_name>Ada</given_name><family_name>Lovelace</family_name>'
                  '</Person></List_Author></Citation_Information>'
                  '</Identification_Area></Product_Bundle>'.format(PDS))
        root = parse(markup)
        prune_blank_containers(root)
        self.assertIn('Ada', etree.tostring(root).decode())
        self.assertIn('Lovelace', etree.tostring(root).decode())

    def test_the_ama_discipline_area_is_not_reasoned_about(self):
        """A dictionary of its own; this has no business pruning inside it."""
        root = parse(
            '<Product_External xmlns="{}"><Context_Area><Discipline_Area>'
            '<Model_Metadata xmlns="http://pds.nasa.gov/pds4/ama/v1"><type/>'
            '</Model_Metadata></Discipline_Area></Context_Area>'
            '</Product_External>'.format(PDS))
        prune_blank_containers(root)
        self.assertIn('Discipline_Area', names(root))


class RepairLabelTests(SimpleTestCase):
    """The file-level entry point."""

    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix='elsa-repair-')
        self.addCleanup(shutil.rmtree, self.directory, True)

    def write(self, markup):
        path = os.path.join(self.directory, 'label.xml')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(markup)
        return path

    def test_it_writes_the_repaired_label(self):
        path = self.write(
            '<?xml version="1.0"?>\n<Product_Bundle xmlns="{}"><Context_Area>'
            '<Time_Coordinates><start_date_time/></Time_Coordinates>'
            '</Context_Area></Product_Bundle>'.format(PDS))
        self.assertTrue(repair_label(path))
        self.assertNotIn('Time_Coordinates', open(path, encoding='utf-8').read())

    def test_a_label_with_nothing_to_fix_is_not_rewritten(self):
        markup = ('<?xml version="1.0"?>\n<Product_Bundle xmlns="{}">'
                  '<Context_Area/></Product_Bundle>'.format(PDS))
        path = self.write(markup)
        before = os.path.getmtime(path)
        self.assertEqual(repair_label(path), [])
        self.assertEqual(os.path.getmtime(path), before, 'the file was rewritten')

    def test_a_missing_file_is_not_an_error(self):
        self.assertEqual(repair_label(os.path.join(self.directory, 'nope.xml')), [])

    def test_unparseable_xml_is_left_alone(self):
        path = self.write('<Product_Bundle><oops')
        self.assertEqual(repair_label(path), [])
        self.assertIn('<oops', open(path, encoding='utf-8').read())

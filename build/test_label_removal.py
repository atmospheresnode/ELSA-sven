# -*- coding: utf-8 -*-
"""Tests for removing a component from a label that does not contain it.

Two 500s in production, both on the same page and both the same shape:

    TypeError: 'NoneType' object is not iterable
        build/models.py, in remove_xml
            for modification_detail in Modification_History:

    TypeError: 'NoneType' object is not iterable
        build/models.py, in remove_xml
            for alias in Alias_List:

The cause is an asymmetry between writing and removing. Neither collection
template ships an Alias_List or a Modification_History, so fill_label creates the
container the first time there is something to put in it. remove_xml assumed the
container was always there. A collection added *after* an alias was saved has a
label with no Alias_List at all, and remove_from_label walks the bundle label and
every collection label, so deleting that alias reached the new collection and
raised. The same holds for Context_Area on the context products.

A second defect sat behind the first, on the entries themselves. Both loops picked
the value out by position, and in both cases the element they indexed is optional:

    modification_detail[2]      # description, unless version_id is blank
    alias[0]                    # alternate_id, unless it is blank

fill_label only writes version_id when the user gave one, so every modification
history saved without a version put description at index 1 and made index 2 an
IndexError. Both loops now look the child up by tag.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile

from lxml import etree

from django.test import TestCase, SimpleTestCase, override_settings

from build.models import (Alias, Citation_Information, Facility, Instrument,
                          Instrument_Host, Investigation, Modification_History)

PDS = 'http://pds.nasa.gov/pds4/pds/v1'
NS = {'pds': PDS}


def label(body):
    return etree.fromstring(
        '<Product_Collection xmlns="{}">{}</Product_Collection>'.format(PDS, body))


# A collection label exactly as the base template writes it: an Identification_Area
# with no Alias_List and no Modification_History, and no Context_Area at all.
BARE = """
  <Identification_Area>
    <logical_identifier>urn:nasa:pds:x:y</logical_identifier>
    <version_id>1.0</version_id>
    <title>A collection</title>
    <information_model_version>1.24.0.0</information_model_version>
    <product_class>Product_Collection</product_class>
  </Identification_Area>
"""


class MissingContainerTests(SimpleTestCase):
    """Removing something from a label that never had it is a no-op, not a crash."""

    def test_an_alias_can_be_removed_from_a_label_with_no_alias_list(self):
        root = label(BARE)
        alias = Alias(alternate_id='ALT-1', alternate_title='A title')

        returned = alias.remove_xml(root)

        self.assertIs(returned, root)
        self.assertEqual(returned.findall('.//pds:Alias_List', NS), [])

    def test_a_modification_history_can_be_removed_from_a_label_without_one(self):
        root = label(BARE)
        entry = Modification_History(description='Initial delivery')

        returned = entry.remove_xml(root)

        self.assertIs(returned, root)
        self.assertEqual(returned.findall('.//pds:Modification_History', NS), [])

    def test_citation_information_can_be_removed_from_a_label_without_one(self):
        root = label(BARE)
        self.assertIsNotNone(Citation_Information().remove_xml(root))

    def test_context_products_can_be_removed_from_a_label_with_no_context_area(self):
        # These four share the shape: open Context_Area, then dereference it.
        # (Telescope has no remove_xml at all, so it is not in this list.)
        for model in (Investigation, Instrument_Host, Instrument, Facility):
            root = label(BARE)
            product = model(name='Something')
            self.assertIs(product.remove_xml(root), root,
                          '{} crashed on a label with no Context_Area'.format(
                              model.__name__))

    def test_removing_from_a_label_with_no_identification_area_is_a_no_op(self):
        root = label('')
        self.assertIs(Alias(alternate_id='A').remove_xml(root), root)
        self.assertIs(Modification_History(description='D').remove_xml(root), root)


class RemovesTheRightEntryTests(SimpleTestCase):
    """The container is there and holds several entries; take out exactly one."""

    def modification_history(self, *details):
        entries = ''.join(details)
        return label("""
          <Identification_Area>
            <logical_identifier>urn:nasa:pds:x:y</logical_identifier>
            <Modification_History>{}</Modification_History>
          </Identification_Area>
        """.format(entries))

    def detail(self, description, version_id=None):
        version = ('<version_id>{}</version_id>'.format(version_id)
                   if version_id else '')
        return ("""<Modification_Detail>
                     <modification_date>2026-09-17</modification_date>
                     {}
                     <description>{}</description>
                   </Modification_Detail>""".format(version, description))

    def descriptions(self, root):
        return [e.text for e in root.findall(
            './/pds:Modification_Detail/pds:description', NS)]

    def test_an_entry_saved_without_a_version_can_still_be_deleted(self):
        # The IndexError case: no version_id, so description is the second child
        # and the old code indexed past the end of the element.
        root = self.modification_history(
            self.detail('Initial delivery'),
            self.detail('Second delivery', version_id='2.0'))

        Modification_History(description='Initial delivery').remove_xml(root)

        self.assertEqual(self.descriptions(root), ['Second delivery'])

    def test_the_other_entries_are_left_alone(self):
        root = self.modification_history(
            self.detail('One', version_id='1.0'),
            self.detail('Two', version_id='2.0'),
            self.detail('Three', version_id='3.0'))

        Modification_History(description='Two').remove_xml(root)

        self.assertEqual(self.descriptions(root), ['One', 'Three'])

    def test_the_container_goes_when_the_last_entry_does(self):
        # An empty Modification_History is not valid PDS4, so removing the only
        # entry has to take the container with it.
        root = self.modification_history(self.detail('Only one'))

        Modification_History(description='Only one').remove_xml(root)

        self.assertEqual(root.findall('.//pds:Modification_History', NS), [])

    def test_an_alias_with_no_alternate_id_does_not_shift_the_match(self):
        # alternate_id is optional, so it is not reliably the first child. The
        # first alias here starts with alternate_title.
        root = label("""
          <Identification_Area>
            <Alias_List>
              <Alias><alternate_title>No id here</alternate_title></Alias>
              <Alias>
                <alternate_id>ALT-1</alternate_id>
                <alternate_title>The one to remove</alternate_title>
              </Alias>
            </Alias_List>
          </Identification_Area>
        """)

        Alias(alternate_id='ALT-1').remove_xml(root)

        titles = [e.text for e in root.findall('.//pds:alternate_title', NS)]
        self.assertEqual(titles, ['No id here'])

    def test_the_alias_list_goes_when_the_last_alias_does(self):
        root = label("""
          <Identification_Area>
            <Alias_List>
              <Alias><alternate_id>ALT-1</alternate_id></Alias>
            </Alias_List>
          </Identification_Area>
        """)

        Alias(alternate_id='ALT-1').remove_xml(root)

        self.assertEqual(root.findall('.//pds:Alias_List', NS), [])


class DeleteThroughTheViewTests(TestCase):
    """The production reproduction, end to end through the real views.

    Commit cccbfb13 started carrying the bundle's metadata into every collection
    label as it is created, so a collection made today gets an Alias_List and a
    Modification_History. Collections made before it did not, and those labels are
    still on disk: bundle 834's "new" collection has neither container, which is
    the label that raised in production. So the setup builds the bundle through the
    real views and then strips the containers from one collection label, which is
    exactly the state a pre-cccbfb13 collection is in.
    """

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-removal-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-removal-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive,
                                    MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        from build.corpus.builder import BundleBuilder
        self.builder = (BundleBuilder(username='remover')
                        .create('removal bundle')
                        .add_alias()
                        .add_modification_history()
                        .add_collection('later'))
        self.bundle = self.builder.bundle
        self.client = self.builder.client
        self.strip_containers(self.collection_label())

    def collection_label(self):
        for root, _, files in os.walk(self.bundle.directory()):
            for name in files:
                if name.startswith('collection_') and 'later' in name:
                    return os.path.join(root, name)
        return None

    def strip_containers(self, path):
        """Put one collection label back into its pre-cccbfb13 state."""
        self.assertIsNotNone(path, 'the later collection label was not written')
        tree = etree.parse(path)
        root = tree.getroot()
        removed = 0
        for tag in ('Alias_List', 'Modification_History'):
            for element in root.findall('.//pds:{}'.format(tag), NS):
                element.getparent().remove(element)
                removed += 1
        self.assertTrue(removed, 'nothing to strip; this test needs rethinking')
        tree.write(path, xml_declaration=True, encoding='utf-8')

    def test_the_stripped_collection_label_has_neither_container(self):
        root = etree.parse(self.collection_label()).getroot()
        self.assertEqual(root.findall('.//pds:Alias_List', NS), [])
        self.assertEqual(root.findall('.//pds:Modification_History', NS), [])

    def test_deleting_an_alias_does_not_500(self):
        alias = Alias.objects.filter(bundle=self.bundle).first()
        self.assertIsNotNone(alias)

        response = self.client.get('/build/{}/{}/alias_delete/'.format(
            self.bundle.pk, alias.pk))

        self.assertIn(response.status_code, (200, 302))
        self.assertFalse(Alias.objects.filter(pk=alias.pk).exists())

    def test_deleting_a_modification_history_does_not_500(self):
        entry = Modification_History.objects.filter(bundle=self.bundle).first()
        self.assertIsNotNone(entry)

        response = self.client.get('/build/{}/{}/modification_history/'.format(
            self.bundle.pk, entry.pk))

        self.assertIn(response.status_code, (200, 302))
        self.assertFalse(Modification_History.objects.filter(pk=entry.pk).exists())

    def test_the_bundle_label_still_loses_the_entry_it_was_asked_to_lose(self):
        # Tolerating a missing container must not turn the delete into a no-op
        # on the labels that do have one.
        entry = Modification_History.objects.filter(bundle=self.bundle).first()
        self.client.get('/build/{}/{}/modification_history/'.format(
            self.bundle.pk, entry.pk))

        root = etree.parse(self.bundle.product_bundle.label()).getroot()
        descriptions = [e.text for e in root.findall(
            './/pds:Modification_Detail/pds:description', NS)]
        self.assertNotIn('Initial delivery', descriptions)

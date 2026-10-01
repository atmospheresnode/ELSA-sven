# -*- coding: utf-8 -*-
"""The bundle label's member list follows the collections that actually exist.

Reported as: "Two products in a collection have the same identifier" kept appearing
after every file with the same name had been deleted. The finding was not about
files. It was the bundle label listing the collection "new" three times, and two
collections deleted long before still listed as members.

Two defects behind it. Deleting a collection called remove_xml on it, which
AdditionalCollections did not have; the AttributeError was caught and printed, so
the entry stayed. And creating a collection appended an entry without checking for
one already there, so a collection deleted and created again under the same name was
listed once more each time.

Covered here through the real views, against the real validator where it is
available, plus the repair for labels written before the fix.
"""
from __future__ import unicode_literals

import os
import shutil
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse
from lxml import etree

from build import validate_rules
from build.corpus import runner
from build.corpus.builder import BundleBuilder
from build.label_repair import repair_bundle_members, stale_bundle_members
from build.models import (AdditionalCollections, Product_Bundle, bundle_member_entries,
                          remove_bundle_member_entries)

PDS = '{http://pds.nasa.gov/pds4/pds/v1}'


class MemberEntryHelpersTests(SimpleTestCase):

    LABEL = ('<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1"><Bundle/>'
             '<Bundle_Member_Entry><lid_reference>urn:x:a</lid_reference></Bundle_Member_Entry>'
             '<Bundle_Member_Entry><lid_reference>urn:x:b</lid_reference></Bundle_Member_Entry>'
             '<Bundle_Member_Entry><lid_reference>urn:x:a</lid_reference></Bundle_Member_Entry>'
             '</Product_Bundle>')

    def test_entries_read_back_from_disk_are_found(self):
        root = etree.fromstring(self.LABEL)
        self.assertEqual([lid for _e, lid in bundle_member_entries(root)],
                         ['urn:x:a', 'urn:x:b', 'urn:x:a'])

    def test_entries_added_in_the_same_request_are_found_too(self):
        """Built bare, without the PDS namespace, before the label is written."""
        root = etree.fromstring(self.LABEL)
        entry = etree.SubElement(root, 'Bundle_Member_Entry')
        etree.SubElement(entry, 'lid_reference').text = 'urn:x:c'
        self.assertIn('urn:x:c', [lid for _e, lid in bundle_member_entries(root)])

    def test_removing_a_lid_removes_every_copy_and_nothing_else(self):
        root = etree.fromstring(self.LABEL)
        self.assertEqual(remove_bundle_member_entries(root, 'urn:x:a'), 2)
        self.assertEqual([lid for _e, lid in bundle_member_entries(root)], ['urn:x:b'])


class RuleTests(SimpleTestCase):

    def finding(self, label, type_, message):
        return {'severity': 'ERROR', 'type': type_, 'message': message, 'label': label,
                'label_path': '/b/' + label, 'line': None, 'element_path': ''}

    def test_a_duplicate_in_the_bundle_label_is_elsas(self):
        f = self.finding('bundle_x.xml', 'error.inventory.duplicate_lidvid',
                         'Inventory contains 3 instances of LIDVID urn:x:new')
        self.assertEqual(validate_rules.classify(f).key, 'elsa-bundle-members')

    def test_a_missing_member_of_the_bundle_is_elsas(self):
        f = self.finding('bundle_x.xml', 'error.integrity.member_not_found',
                         "The member 'urn:x:gone' could not be found")
        self.assertEqual(validate_rules.classify(f).key, 'elsa-bundle-members')

    def test_two_documents_with_one_name_are_still_the_users(self):
        f = self.finding('collection_x_document.xml', 'error.inventory.duplicate_lidvid',
                         'Inventory contains 2 instances of LIDVID urn:x:document:guide')
        self.assertEqual(validate_rules.classify(f).key, 'duplicate-member')


@override_settings(VALIDATE_AUTO_CHECK=False)
class BundleMembersThroughTheViewsTests(TransactionTestCase):

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='elsa-members-')
        self.addCleanup(shutil.rmtree, self.work, True)
        patcher = override_settings(ARCHIVE_DIR=os.path.join(self.work, 'archive'),
                                    MEDIA_ROOT=os.path.join(self.work, 'media'))
        patcher.enable()
        self.addCleanup(patcher.disable)

    def build(self, name, bundle_type='External'):
        b = BundleBuilder(username='members')
        b.create(name, bundle_type).add_citation().fill_authors().add_modification_history()
        return b

    def delete(self, b, collection_name):
        collection = AdditionalCollections.objects.get(bundle=b.bundle,
                                                       collection_name=collection_name)
        b.client.post(reverse('build:delete_collection', args=[b.bundle.pk, collection.pk]))
        self.assertFalse(AdditionalCollections.objects.filter(pk=collection.pk).exists())

    def members(self, b):
        root = etree.parse(Product_Bundle.objects.get(bundle=b.bundle).label()).getroot()
        return [lid.rsplit(':', 1)[-1] for _e, lid in bundle_member_entries(root)]

    def test_deleting_a_collection_takes_it_out_of_the_bundle_label(self):
        b = self.build('members delete').add_collection('another')
        self.assertIn('another', self.members(b))
        self.delete(b, 'another')
        self.assertNotIn('another', self.members(b))

    def test_recreating_a_collection_lists_it_once(self):
        """The reported sequence: new, deleted, new again, and so on."""
        b = self.build('members recreate')
        for _round in range(3):
            b.add_collection('new')
            self.delete(b, 'new')
        b.add_collection('new')
        self.assertEqual(self.members(b).count('new'), 1)

    def test_adding_the_same_collection_twice_lists_it_once(self):
        b = self.build('members twice').add_collection('sims')
        b.add_collection('sims')
        self.assertEqual(self.members(b).count('sims'), 1)

    def test_the_other_members_survive(self):
        b = self.build('members survive').add_collection('keep').add_collection('drop')
        self.delete(b, 'drop')
        self.assertEqual(sorted(self.members(b)), ['document', 'keep'])

    def test_the_validator_agrees(self):
        if not runner.validate_available():
            self.skipTest('the PDS validate tool is not configured on this machine')
        b = self.build('members validate').add_document('readme')
        for name in ('new', 'another', 'n_another'):
            b.add_collection(name)
        self.delete(b, 'another')
        self.delete(b, 'n_another')
        self.delete(b, 'new')
        b.add_collection('new')
        findings = runner.run_validate(b.directory(), os.path.join(self.work, 'r.json'))
        types = {f['type'].split('.')[-1] for f in findings}
        self.assertNotIn('duplicate_lidvid', types, findings)
        self.assertNotIn('member_not_found', types, findings)

    # -- the repair, for labels written before the fix --------------------------

    def corrupt(self, b, *lids):
        """Append member entries the way the old code left them behind."""
        path = Product_Bundle.objects.get(bundle=b.bundle).label()
        tree = etree.parse(path)
        for lid in lids:
            entry = etree.SubElement(tree.getroot(), PDS + 'Bundle_Member_Entry')
            etree.SubElement(entry, PDS + 'lid_reference').text = '{}:{}'.format(
                b.bundle.lid(), lid)
            etree.SubElement(entry, PDS + 'member_status').text = 'Primary'
            etree.SubElement(entry, PDS + 'reference_type').text = 'bundle_has_external_collection'
        tree.write(path, xml_declaration=True, encoding='utf-8')

    def test_the_repair_removes_exactly_the_stale_entries(self):
        b = self.build('members repair').add_collection('new')
        self.corrupt(b, 'another', 'new', 'n_another', 'new')
        stale = stale_bundle_members(b.bundle)
        self.assertEqual(sorted((lid.rsplit(':', 1)[-1], why) for lid, why in stale), [
            ('another', 'no such collection'), ('n_another', 'no such collection'),
            ('new', 'listed more than once'), ('new', 'listed more than once')])

        repair_bundle_members(b.bundle)
        self.assertEqual(sorted(self.members(b)), ['document', 'new'])
        self.assertEqual(stale_bundle_members(b.bundle), [], 'the repair is not idempotent')
        self.assertEqual(repair_bundle_members(b.bundle), [])

    def test_the_repair_never_drops_a_collection_that_exists(self):
        """Archive bundles carry context and xml_schema collections as well."""
        b = self.build('members archive', 'Archive').add_collection('raw', 'Data')
        before = sorted(self.members(b))
        self.assertTrue({'document', 'context', 'xml_schema', 'raw'} <= set(before), before)
        self.assertEqual(stale_bundle_members(b.bundle), [])
        repair_bundle_members(b.bundle)
        self.assertEqual(sorted(self.members(b)), before)

    def test_the_command_reports_before_it_writes(self):
        b = self.build('members command').add_collection('new')
        self.corrupt(b, 'gone', 'new')
        label = Product_Bundle.objects.get(bundle=b.bundle).label()
        before = open(label).read()

        out = StringIO()
        call_command('repair_labels', bundle=b.bundle.pk, stdout=out)
        self.assertIn('no such collection', out.getvalue())
        self.assertIn('Nothing was written', out.getvalue())
        self.assertEqual(open(label).read(), before)

        call_command('repair_labels', bundle=b.bundle.pk, apply=True, stdout=StringIO())
        self.assertEqual(sorted(self.members(b)), ['document', 'new'])

    def test_the_repaired_bundle_passes_the_validator_on_membership(self):
        if not runner.validate_available():
            self.skipTest('the PDS validate tool is not configured on this machine')
        b = self.build('members repaired validate').add_document('readme').add_collection('new')
        self.corrupt(b, 'another', 'new', 'new')
        findings = runner.run_validate(b.directory(), os.path.join(self.work, 'before.json'))
        before = {f['type'].split('.')[-1] for f in findings}
        self.assertIn('duplicate_lidvid', before, 'the corruption no longer reproduces the report')
        self.assertIn('member_not_found', before)

        repair_bundle_members(b.bundle)
        findings = runner.run_validate(b.directory(), os.path.join(self.work, 'after.json'))
        after = {f['type'].split('.')[-1] for f in findings}
        self.assertNotIn('duplicate_lidvid', after, findings)
        self.assertNotIn('member_not_found', after, findings)


@override_settings(VALIDATE_AUTO_CHECK=False)
class LegacyLabelsAreNeverStrippedTests(TransactionTestCase):
    """Production carries bundles from 2022 whose collection label LIDs differ in case
    from the bundle's entries (":Document" against ":document"). Those collections
    exist. A repair that matched LIDs exactly would have removed 159 of them."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='elsa-members-legacy-')
        self.addCleanup(shutil.rmtree, self.work, True)
        patcher = override_settings(ARCHIVE_DIR=os.path.join(self.work, 'archive'),
                                    MEDIA_ROOT=os.path.join(self.work, 'media'))
        patcher.enable()
        self.addCleanup(patcher.disable)

    def test_a_collection_whose_label_lid_differs_in_case_keeps_its_entry(self):
        b = BundleBuilder(username='legacy')
        b.create('legacy case', 'External').add_citation().fill_authors()
        document_label = [os.path.join(dp, f) for dp, _, fs in os.walk(b.directory())
                          for f in fs if f.startswith('collection_') and 'document' in f][0]
        tree = etree.parse(document_label)
        lid = tree.getroot().find('{0}Identification_Area/{0}logical_identifier'.format(PDS))
        lid.text = lid.text.rsplit(':', 1)[0] + ':Document'
        tree.write(document_label, xml_declaration=True, encoding='utf-8')

        self.assertEqual(stale_bundle_members(b.bundle), [])

    def test_a_collection_directory_alone_is_enough_to_keep_its_entry(self):
        b = BundleBuilder(username='legacy')
        b.create('legacy dir', 'External').add_citation().fill_authors().add_collection('keep')
        for dp, _, fs in os.walk(os.path.join(b.directory(), 'keep')):
            for f in fs:
                if f.endswith('.xml'):
                    os.remove(os.path.join(dp, f))          # label gone, directory stays
        AdditionalCollections.objects.filter(bundle=b.bundle).delete()
        self.assertEqual(stale_bundle_members(b.bundle), [])

    def test_an_archive_data_collection_known_only_to_the_database_keeps_its_entry(self):
        """The build view lists an Archive bundle's "data" collection in the bundle label
        but never writes its label or directory; 77 production bundles look like this."""
        from build.models import Product_Collection
        b = BundleBuilder(username='legacy')
        b.create('legacy data', 'Archive').add_citation().fill_authors()
        if not Product_Collection.objects.filter(bundle=b.bundle, collection__iexact='data').exists():
            Product_Collection.objects.create(bundle=b.bundle, collection='Data')
        path = Product_Bundle.objects.get(bundle=b.bundle).label()
        tree = etree.parse(path)
        if not any(lid.endswith(':data') for _e, lid in bundle_member_entries(tree.getroot())):
            entry = etree.SubElement(tree.getroot(), PDS + 'Bundle_Member_Entry')
            etree.SubElement(entry, PDS + 'lid_reference').text = b.bundle.lid() + ':data'
            tree.write(path, xml_declaration=True, encoding='utf-8')
        self.assertFalse(os.path.isdir(os.path.join(b.directory(), 'data')))
        self.assertEqual(stale_bundle_members(b.bundle), [])

# -*- coding: utf-8 -*-
"""Adding a document must update the collection's inventory.

Reported as the validation panel insisting the document collection was empty on a
bundle that had three documents in it. The panel was telling the truth about the
label: every collection carries an inventory table naming its members and a record
count in the label, both derived from membership, and five of the views that add a
document never rewrote either. The collection sat at records=0 with an empty
inventory file however many documents went into it, so PDS rejected it as an empty
collection.

Driven through the real views and read off disk, because the models were right the
whole time: member_lidvids() returned all three documents correctly.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import Bundle, Investigation, Product_Collection, Product_Document

NS = {'pds': 'http://pds.nasa.gov/pds4/pds/v1'}


class DocumentInventoryTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-docinv-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-docinv-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('docinv', password='pw')
        self.client.login(username='docinv', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'docinv bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='docinv bundle')

    # -- helpers -----------------------------------------------------------------

    def add_document(self, name):
        """Through the annex path, which is the one an AMA bundle uses."""
        response = self.client.post(
            reverse('build:annex_collection_document', args=[str(self.bundle.pk)]),
            {'form_name': 'document_form', 'document_name': name,
             'document_id': name, 'file_name': name + '.pdf',
             'comment': 'a document', 'document_std_id': 'PDF/A',
             'source': 'bundle'})
        self.assertIn(response.status_code, (200, 302))

    def document_collection(self):
        return Product_Collection.objects.filter(bundle=self.bundle).first()

    def inventory_rows(self):
        collection = self.document_collection()
        path = os.path.splitext(collection.label())[0] + '.csv'
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as handle:
            return [line for line in handle.read().splitlines() if line.strip()]

    def declared_records(self):
        root = ET.parse(self.document_collection().label()).getroot()
        return (root.findtext(
            'pds:File_Area_Inventory/pds:Inventory/pds:records', '', NS) or '').strip()

    # -- the bug -----------------------------------------------------------------

    def test_adding_a_document_puts_it_in_the_inventory(self):
        self.add_document('user_guide')
        self.assertEqual(len(self.inventory_rows() or []), 1,
                         'the document is not listed in the collection inventory')

    def test_the_label_counts_the_document(self):
        """records=0 is what PDS rejects as an empty collection."""
        self.add_document('user_guide')
        self.assertEqual(self.declared_records(), '1')

    def test_three_documents_make_three_records(self):
        for name in ('user_guide', 'variables', 'how_to_read'):
            self.add_document(name)
        self.assertEqual(len(self.inventory_rows() or []), 3)
        self.assertEqual(self.declared_records(), '3')

    def test_the_inventory_names_the_document_that_was_added(self):
        self.add_document('user_guide')
        rows = self.inventory_rows() or []
        self.assertTrue(any('user_guide' in row for row in rows),
                        'the inventory does not name the document: {}'.format(rows))

    def test_the_documents_reach_the_database_too(self):
        """Guards the premise: the models were never the broken part."""
        self.add_document('user_guide')
        self.assertEqual(Product_Document.objects.filter(bundle=self.bundle).count(), 1)
        self.assertEqual(len(list(self.document_collection().member_lidvids())), 1)

    def test_an_empty_collection_still_reports_zero(self):
        """The panel must keep telling the truth when the collection really is empty."""
        self.assertEqual(self.declared_records(), '0')

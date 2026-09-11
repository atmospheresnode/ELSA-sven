# -*- coding: utf-8 -*-
"""Bundle-level metadata has to reach every collection label, including the user's own.

A bundle's collections live in two tables: Product_Collection for the ones ELSA
creates, AdditionalCollections for the ones the user adds. Citation Information,
Modification History and the Alias are copied into all of their labels.

Every view that wrote them queried only the first table, so a collection the user
added kept the empty <given_name/> skeleton it was created with. The reported
symptom was that filling in the author "did not save": it saved into the bundle and
the document collection, and PDS then reported the blank in the third label as an
error against a citation the user had demonstrably filled in.

These drive the real views and read the XML on disk, because the bug was invisible
in the model: the Citation_Information row was correct all along.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import (AdditionalCollections, Bundle, Citation_Information,
                          Investigation, Product_Collection)

NS = {'pds': 'http://pds.nasa.gov/pds4/pds/v1'}


class MetadataReachesEveryCollectionTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-reach-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-reach-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('reach', password='pw')
        self.client.login(username='reach', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')

        self.client.post(reverse('build:build'), {
            'name': 'reach bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='reach bundle')

        # The collection the user adds themselves. This is the one that was missed.
        self.client.post(reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
                         {'collection_name': 'mydata', 'collection_type': 'External'})
        self.added = AdditionalCollections.objects.filter(bundle=self.bundle).first()
        self.assertIsNotNone(self.added, 'the user-added collection was not created')

    # -- helpers -----------------------------------------------------------------

    def collection_labels(self):
        """Every collection label on disk, by directory name."""
        found = {}
        for dirpath, _dirnames, filenames in os.walk(self.bundle.directory()):
            for filename in filenames:
                if filename.startswith('collection_') and filename.endswith('.xml'):
                    found[os.path.basename(dirpath)] = os.path.join(dirpath, filename)
        return found

    def add_citation(self):
        response = self.client.post(
            reverse('build:citation_information', args=[str(self.bundle.pk)]),
            {'number_of_authors_people': 1, 'number_of_authors_organization': 0,
             'number_of_editors_people': 0, 'number_of_editors_organization': 0,
             'publication_year': '2026', 'description': 'A description', 'keyword': 'k'})
        self.assertIn(response.status_code, (200, 302))
        citation = Citation_Information.objects.get(bundle=self.bundle)
        return citation

    def fill_author(self, citation, given='Ada', family='Lovelace'):
        response = self.client.post(
            reverse('build:edit_citation_information',
                    args=[str(self.bundle.pk), str(citation.pk)]),
            {'author_person_0_given_name': given,
             'author_person_0_family_name': family,
             'author_person_0_orcid': '0000-0001-2345-6789',
             'author_person_0_affiliation': 'New Mexico State University'})
        self.assertIn(response.status_code, (200, 302))

    def authors_in(self, path):
        root = ET.parse(path).getroot()
        people = root.findall(
            'pds:Identification_Area/pds:Citation_Information/pds:List_Author/pds:Person', NS)
        return [((person.findtext('pds:given_name', '', NS) or '').strip(),
                 (person.findtext('pds:family_name', '', NS) or '').strip())
                for person in people]

    # -- the bug -----------------------------------------------------------------

    def test_both_collection_tables_are_in_play(self):
        """Guards the premise: if this stops being true the rest proves nothing."""
        self.assertTrue(Product_Collection.objects.filter(bundle=self.bundle).exists())
        self.assertTrue(AdditionalCollections.objects.filter(bundle=self.bundle).exists())
        self.assertGreaterEqual(len(self.collection_labels()), 2)

    def test_an_author_reaches_the_collection_the_user_added(self):
        citation = self.add_citation()
        self.fill_author(citation)

        labels = self.collection_labels()
        self.assertIn('mydata', labels, 'the user-added collection has no label')
        self.assertEqual(self.authors_in(labels['mydata']), [('Ada', 'Lovelace')])

    def test_no_collection_label_is_left_with_a_blank_author(self):
        """The blank is what PDS reports, so no label may keep one."""
        citation = self.add_citation()
        self.fill_author(citation)

        for directory, path in self.collection_labels().items():
            for given, family in self.authors_in(path):
                self.assertTrue(
                    given and family,
                    'the {} collection label still has a blank author, which is the '
                    'error PDS reports against a citation the user filled in'.format(
                        directory))

    def test_the_bundle_label_still_gets_the_author(self):
        """The half that already worked, so a fix to the other half cannot break it."""
        citation = self.add_citation()
        self.fill_author(citation)

        bundle_labels = [
            os.path.join(self.bundle.directory(), name)
            for name in os.listdir(self.bundle.directory())
            if name.startswith('bundle_') and name.endswith('.xml')]
        self.assertEqual(len(bundle_labels), 1, 'expected exactly one bundle label')
        self.assertEqual(self.authors_in(bundle_labels[0]), [('Ada', 'Lovelace')])

    def test_editing_an_author_again_reaches_every_label(self):
        """A correction has to propagate as widely as the first write did."""
        citation = self.add_citation()
        self.fill_author(citation)
        self.fill_author(citation, given='Grace', family='Hopper')

        for directory, path in self.collection_labels().items():
            self.assertEqual(
                self.authors_in(path), [('Grace', 'Hopper')],
                'the {} collection label kept the old author'.format(directory))

    def test_a_collection_added_after_the_citation_is_not_born_blank(self):
        """The production ordering.

        The reported bundle had a filled-in citation and a user-added collection
        whose label still carried an empty <given_name/>. That happens when the
        collection is created after the citation: whatever writes its label gives it
        the citation skeleton, and the edit that would have filled it has already
        run. A collection created later must come into being with the citation the
        bundle already has, or it is invalid the moment it exists.
        """
        citation = self.add_citation()
        self.fill_author(citation)

        self.client.post(reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
                         {'collection_name': 'later', 'collection_type': 'External'})

        labels = self.collection_labels()
        self.assertIn('later', labels, 'the later collection has no label')
        for given, family in self.authors_in(labels['later']):
            self.assertTrue(
                given and family,
                'a collection created after the citation was born with a blank '
                'author, which PDS reports as an error on a citation the user filled in')

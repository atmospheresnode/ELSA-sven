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


class DataProductLabelsGetTheCitationTests(TestCase):
    """A NetCDF product label carries a citation too, and was in no write path.

    This is the case that survived the first round of fixes. The reported bundle had
    a citation filled in, bundle and collection labels correct and written minutes
    ago, and one data product label from an hour earlier still holding an empty
    <given_name/>. Re-entering the citation did not help, because nothing ELSA does
    to a citation has ever touched a data product label: they could only be reached
    by regenerating them from the NetCDF file they describe.
    """

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-dpl-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('dpl', password='pw')
        self.client.login(username='dpl', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'dpl bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='dpl bundle')

        # NetCDF files always live in a collection the user added: NetCDFFile.collection
        # is a foreign key to AdditionalCollections, which is the collection the
        # reported bundle kept its data in too.
        self.client.post(reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
                         {'collection_name': 'sims', 'collection_type': 'External'})
        self.collection = AdditionalCollections.objects.filter(bundle=self.bundle).first()
        self.assertIsNotNone(self.collection)

        self.citation = self.add_citation()
        self.fill_author('Ada', 'Lovelace')

    def add_citation(self):
        self.client.post(
            reverse('build:citation_information', args=[str(self.bundle.pk)]),
            {'number_of_authors_people': 1, 'number_of_authors_organization': 0,
             'number_of_editors_people': 0, 'number_of_editors_organization': 0,
             'publication_year': '2026', 'description': 'A description', 'keyword': 'k'})
        return Citation_Information.objects.get(bundle=self.bundle)

    def fill_author(self, given, family):
        self.client.post(
            reverse('build:edit_citation_information',
                    args=[str(self.bundle.pk), str(self.citation.pk)]),
            {'author_person_0_given_name': given,
             'author_person_0_family_name': family,
             'author_person_0_orcid': '0000-0001-2345-6789',
             'author_person_0_affiliation': 'New Mexico State University'})

    def write_stale_product_label(self):
        """A data product label as the generator wrote it before the citation edit.

        Built by hand rather than by uploading a NetCDF, which needs a real file and
        xarray; what matters here is a label on disk, in the right place, holding the
        blank skeleton, which is exactly the state of the reported bundle.
        """
        from build.models import NetCDFFile
        row = NetCDFFile.objects.create(
            bundle=self.bundle, collection=self.collection, title='sample',
            file='sample.nc')
        label = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<Product_External xmlns="http://pds.nasa.gov/pds4/pds/v1">\n'
            '  <Identification_Area>\n'
            '    <logical_identifier>urn:nasa:pds-ama:dpl_bundle:sims:sample.nc</logical_identifier>\n'
            '    <version_id>1.0</version_id>\n'
            '    <title>sample</title>\n'
            '    <information_model_version>1.24.0.0</information_model_version>\n'
            '    <product_class>Product_External</product_class>\n'
            '    <Citation_Information>\n'
            '      <publication_year>2026</publication_year>\n'
            '      <description>A description</description>\n'
            '      <List_Author>\n'
            '        <Person>\n'
            '          <given_name/>\n'
            '          <family_name/>\n'
            '          <person_orcid/>\n'
            '          <Affiliation><organization_name/></Affiliation>\n'
            '        </Person>\n'
            '      </List_Author>\n'
            '    </Citation_Information>\n'
            '    <Modification_History/>\n'
            '  </Identification_Area>\n'
            '</Product_External>\n')
        path = row.label()
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(label)
        return row, path

    def authors_in(self, path):
        root = ET.parse(path).getroot()
        return [((p.findtext('pds:given_name', '', NS) or '').strip(),
                 (p.findtext('pds:family_name', '', NS) or '').strip())
                for p in root.findall(
                    'pds:Identification_Area/pds:Citation_Information/'
                    'pds:List_Author/pds:Person', NS)]

    # -- the bug -----------------------------------------------------------------

    def test_a_netcdf_file_knows_where_its_label_is(self):
        """Without this the label is unreachable by anything but regeneration."""
        row, path = self.write_stale_product_label()
        self.assertTrue(path.endswith('sample.xml'), path)
        self.assertTrue(os.path.exists(row.label()))

    def test_an_extensionless_upload_does_not_get_an_extensionless_label(self):
        from build.models import NetCDFFile
        row = NetCDFFile.objects.create(
            bundle=self.bundle, collection=self.collection,
            title='no extension', file='00000.atmos_average')
        self.assertTrue(row.label().endswith('00000.atmos_average.xml'), row.label())

    def test_editing_the_citation_reaches_a_data_product_label(self):
        _row, path = self.write_stale_product_label()
        self.assertEqual(self.authors_in(path), [('', '')], 'the fixture is not stale')

        self.fill_author('Grace', 'Hopper')

        self.assertEqual(self.authors_in(path), [('Grace', 'Hopper')],
                         'the data product label was left behind again')

    def test_no_label_anywhere_keeps_a_blank_author(self):
        """What the validation panel actually reports on."""
        _row, path = self.write_stale_product_label()
        self.fill_author('Grace', 'Hopper')

        for dirpath, _dirnames, filenames in os.walk(self.bundle.directory()):
            for filename in filenames:
                if not filename.endswith('.xml'):
                    continue
                full = os.path.join(dirpath, filename)
                for given, family in self.authors_in(full):
                    self.assertTrue(
                        given and family,
                        '{} still has a blank author'.format(
                            os.path.relpath(full, self.bundle.directory())))

    def has_citation(self, path):
        root = ET.parse(path).getroot()
        return root.find('pds:Identification_Area/pds:Citation_Information', NS) is not None

    def test_deleting_the_citation_removes_it_from_data_products_too(self):
        """The state the reported bundle was actually left in.

        Its Citation_Information row was gone and the bundle label had no citation,
        while the data product label still carried one. A bundle that says it has no
        citation while one of its own products still has one is inconsistent in a way
        no amount of re-entering the citation can fix.
        """
        _row, path = self.write_stale_product_label()
        self.fill_author('Grace', 'Hopper')
        self.assertTrue(self.has_citation(path), 'the fixture has no citation to delete')

        response = self.client.get(reverse(
            'build:citation_information_delete',
            args=[str(self.bundle.pk), str(self.citation.pk)]))
        self.assertIn(response.status_code, (200, 302))

        bundle_label = os.path.join(
            self.bundle.directory(),
            [n for n in os.listdir(self.bundle.directory())
             if n.startswith('bundle_') and n.endswith('.xml')][0])
        self.assertFalse(self.has_citation(bundle_label),
                         'the bundle label kept the citation')
        self.assertFalse(self.has_citation(path),
                         'the data product label kept a citation the bundle no longer has')


class CollectionWithoutACitationSectionTests(MetadataReachesEveryCollectionTests):
    """Editing a citation must survive a label that has no citation section.

    Reported as a 500 on the citation edit page. fill_label_values has always
    carried a comment saying it would create a Citation_Information if it found
    none, and never did: every label it ran against happened to have one, so the
    missing branch was invisible.

    It stopped being invisible when the edit was widened to reach the collections a
    user adds. A collection created while the bundle had no citation has no
    Citation_Information in its label, and the edit walked into .find() on None.
    """

    def strip_citation(self, collection_directory_name):
        """A collection label as it is when the collection predates the citation."""
        import xml.etree.ElementTree as ElementTree
        path = self.collection_labels()[collection_directory_name]
        ElementTree.register_namespace('', NS['pds'])
        tree = ElementTree.parse(path)
        identification = tree.getroot().find('pds:Identification_Area', NS)
        citation = identification.find('pds:Citation_Information', NS)
        if citation is not None:
            identification.remove(citation)
        tree.write(path, encoding='utf-8', xml_declaration=True)
        return path

    def test_editing_survives_a_collection_label_with_no_citation(self):
        citation = self.add_citation()
        path = self.strip_citation('mydata')
        self.assertEqual(self.authors_in(path), [], 'the fixture still has a citation')

        self.fill_author(citation, given='Ada', family='Lovelace')

        self.assertEqual(self.authors_in(path), [('Ada', 'Lovelace')],
                         'the label was skipped instead of being given a citation')

    def test_the_other_labels_are_still_written(self):
        """A crash on one label used to take the whole edit with it."""
        citation = self.add_citation()
        self.strip_citation('mydata')
        self.fill_author(citation, given='Grace', family='Hopper')

        for directory, path in self.collection_labels().items():
            self.assertEqual(self.authors_in(path), [('Grace', 'Hopper')],
                             '{} did not get the author'.format(directory))

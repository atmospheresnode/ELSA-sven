# -*- coding: utf-8 -*-
"""Build bundles of known shapes, through the real views, for end-to-end checking.

The point of driving the views rather than the models is that every defect worth
finding here lived in the gap between them: the model was right and the label on
disk was wrong. A harness that calls fill_label() directly would have missed all of
them.

Each shape is a situation a real user gets into. They are deliberately not all
valid: a bundle nobody has finished should report the things they have not done,
and a harness that only builds complete bundles cannot tell a real complaint from a
missing rule.
"""
from __future__ import unicode_literals

import os

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from build.models import (AdditionalCollections, Bundle, Citation_Information,
                          Investigation, NetCDFFile, Target)

AMA_INVESTIGATION = {
    'name': 'Atmospheric Modeling Annex',
    'type_of': 'Individual Investigation',
    'lid': 'urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
    'file_ref': '',
}


def ensure_context():
    """The context products the build view expects to find."""
    investigation, _ = Investigation.objects.get_or_create(
        lid=AMA_INVESTIGATION['lid'], defaults=AMA_INVESTIGATION)
    target, _ = Target.objects.get_or_create(
        lid='urn:nasa:pds:context:target:planet.mars',
        defaults={'name': 'Mars', 'type_of': 'PLANET', 'file_ref': ''})
    return investigation, target


class BundleBuilder(object):
    """Drives the real views to produce a bundle on disk."""

    def __init__(self, username='corpus'):
        self.user, _ = User.objects.get_or_create(username=username)
        self.user.set_password('pw')
        self.user.save()
        self.client = Client()
        self.client.force_login(self.user)
        ensure_context()

    # -- steps -------------------------------------------------------------------

    def create(self, name, bundle_type='External', version='1O00'):
        self.client.post(reverse('build:build'), {
            'name': name, 'bundle_type': bundle_type,
            'version': version, 'bundleID': ''})
        self.bundle = Bundle.objects.get(name=name, user=self.user)
        return self

    def add_collection(self, name, collection_type='External'):
        self.client.post(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
            {'collection_name': name, 'collection_type': collection_type})
        return self

    def add_citation(self, authors=1, description='A test bundle', keyword='test'):
        self.client.post(
            reverse('build:citation_information', args=[str(self.bundle.pk)]),
            {'number_of_authors_people': authors,
             'number_of_authors_organization': 0,
             'number_of_editors_people': 0, 'number_of_editors_organization': 0,
             'publication_year': '2026', 'description': description,
             'keyword': keyword})
        return self

    def fill_authors(self, given='Ada', family='Lovelace'):
        citation = Citation_Information.objects.filter(bundle=self.bundle).first()
        if citation is None:
            return self
        payload = {}
        for index in range(citation.number_of_authors_people):
            payload['author_person_{}_given_name'.format(index)] = given
            payload['author_person_{}_family_name'.format(index)] = family
            payload['author_person_{}_orcid'.format(index)] = '0000-0001-2345-6789'
            payload['author_person_{}_affiliation'.format(index)] = 'NMSU'
        self.client.post(
            reverse('build:edit_citation_information',
                    args=[str(self.bundle.pk), str(citation.pk)]), payload)
        return self

    def add_modification_history(self, description='Initial delivery'):
        self.client.post(
            reverse('build:modification_history', args=[str(self.bundle.pk)]),
            {'version_id': '1.0', 'modification_date': '2026-09-17',
             'description': description})
        return self

    def add_document(self, name='user_guide', file_name=None):
        self.client.post(
            reverse('build:annex_collection_document', args=[str(self.bundle.pk)]),
            {'form_name': 'document_form', 'document_name': name,
             'document_id': name, 'file_name': file_name or (name + '.pdf'),
             'comment': 'a document', 'document_std_id': 'PDF/A',
             'source': 'bundle'})
        return self

    def add_target(self):
        target = Target.objects.get(lid='urn:nasa:pds:context:target:planet.mars')
        self.bundle.targets.add(target)
        return self

    def upload_netcdf(self, source_path, collection_name, as_name=None):
        collection = AdditionalCollections.objects.filter(
            bundle=self.bundle, collection_name=collection_name).first()
        if collection is None:
            return self
        with open(source_path, 'rb') as handle:
            upload = SimpleUploadedFile(
                as_name or os.path.basename(source_path), handle.read(),
                content_type='application/x-netcdf')
        self.client.post(
            reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
            {'collection': collection.collection_name, 'netcdf_files': upload},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        return self

    def fill_ama(self):
        """Model Metadata, Simulation Configuration and File Description."""
        row = NetCDFFile.objects.filter(bundle=self.bundle).first()
        if row is None:
            return self
        self.client.post(
            reverse('build:netcdf_ama', args=[str(self.bundle.pk), str(row.pk)]),
            {'model-type': 'GCM', 'model-name': 'EPIC', 'model-version': '3.85',
             'model-institution': 'New Mexico Tech',
             'sim-horizontal_grid_type': 'lat-lon', 'sim-model_resolution': '128',
             'sim-model_resolution_unit': 'deg',
             'sim-vertical_grid_type': 'Isentropic', 'sim-vertical_grid_unit': 'K',
             'sim-model_timestep': '120', 'sim-model_timestep_unit': 's',
             'sim-northern_boundary': '30.0', 'sim-southern_boundary': '-30.0',
             'sim-eastern_boundary': '90.0', 'sim-western_boundary': '-90.0',
             'sim-upper_boundary': '12', 'sim-lower_boundary': '1',
             'sim-start_time': '0', 'sim-end_time': '820', 'sim-time_unit': 's',
             'sim-description': 'A simulation',
             'desc-top_level': '12', 'desc-bottom_level': '1', 'desc-level_unit': 'K',
             'desc-start_time': '0', 'desc-end_time': '820', 'desc-time_unit': 's',
             'desc-postprocessing_methods': 'none'})
        return self

    def corrupt_ama_value(self, field='type', value='NOT A REAL MODEL TYPE'):
        """Put a value the AMA dictionary does not accept into the label.

        Written through the model rather than the form, because the form offers a
        dropdown and will not produce this. The point is not to test the form; it is
        to prove the AMA schematron rules are actually being run and that what they
        report reaches the user in plain language. A rule nobody has seen fire is a
        rule nobody knows works.
        """
        from build.models import ModelMetadata, NetCDFFile
        from build.views import regenerate_netcdf_labels

        metadata = ModelMetadata.objects.filter(
            collection__bundle=self.bundle).first()
        if metadata is None:
            return self
        setattr(metadata, field, value)
        metadata.save()
        regenerate_netcdf_labels(
            self.bundle, list(NetCDFFile.objects.filter(bundle=self.bundle)))
        return self

    def add_alias(self, alternate_id='ALT-1', alternate_title='An alternate title'):
        self.client.post(
            reverse('build:alias', args=[str(self.bundle.pk)]),
            {'alternate_id': alternate_id, 'alternate_title': alternate_title,
             'comment': 'an alias'})
        return self

    def directory(self):
        return self.bundle.directory()

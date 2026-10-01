# -*- coding: utf-8 -*-
"""Logical identifiers ELSA composes have to satisfy the PDS4 pattern.

Reported from a real bundle: a NetCDF uploaded as

    00000.atmos_average_pstd_-_Copy.nc

produced

    urn:nasa:pds-ama:sept_ama:new:00000.atmos_average_pstd_-_Copy.nc

and PDS rejected it with

    cvc-pattern-valid: Value '...' is not facet-valid with respect to pattern
    'urn(:[\\p{Ll}\\p{Nd}\\-._]+){3,5}' for type 'logical_identifier'.

\\p{Ll} is lowercase-only, so the capital C and P in "Copy" are the whole problem.
The user had done nothing wrong: PDS4 permits a mixed-case file name, and file_name
in the label keeps it. Only the identifier derived from it may not be mixed case,
and deriving it is ELSA's job.

The identifier is composed in two places that must agree exactly, or the collection
inventory names products whose labels call themselves something else:

    views._process_single_netcdf        the product label
    AdditionalCollections.member_lidvids the inventory entry
"""
from __future__ import unicode_literals

import os
import re
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from build import validate_rules
from build.models import (AdditionalCollections, Bundle, NetCDFFile,
                          pds_lid_segment)

# The pattern PDS4 puts on logical_identifier, one segment at a time.
SEGMENT = re.compile(r'^[a-z0-9._-]+$')
WHOLE_LID = re.compile(r'^urn(:[a-z0-9._-]+){3,5}$')


class SegmentTests(SimpleTestCase):

    def assertValid(self, raw):
        cleaned = pds_lid_segment(raw)
        self.assertTrue(SEGMENT.match(cleaned),
                        '{!r} became {!r}, still not a valid segment'.format(
                            raw, cleaned))
        return cleaned

    def test_the_reported_file_name(self):
        self.assertEqual(
            self.assertValid('00000.atmos_average_pstd_-_Copy.nc'),
            '00000.atmos_average_pstd_-_copy.nc')

    def test_a_windows_copy_before_django_renames_it(self):
        self.assertValid('00000.atmos_average_pstd - Copy.nc')

    def test_the_characters_pds4_does_allow_survive(self):
        # Dots, hyphens and underscores are all in the permitted set and must not be
        # replaced: doing so would rename every file that already validates.
        self.assertEqual(pds_lid_segment('a-b_c.1.nc'), 'a-b_c.1.nc')

    def test_an_already_valid_segment_is_untouched(self):
        for value in ('new', 'document', '00000.atmos_average.nc', 'sept_ama'):
            self.assertEqual(pds_lid_segment(value), value)

    def test_disallowed_characters_become_underscores_rather_than_vanishing(self):
        # Dropping them would collide 'a(1).nc' and 'a1.nc' into one identifier.
        self.assertNotEqual(pds_lid_segment('data (1).nc'),
                            pds_lid_segment('data1.nc'))
        self.assertValid('data (1).nc')

    def test_an_ampersand_in_a_bundle_name(self):
        self.assertValid('Mars & Venus')

    def test_it_is_safe_on_empty_and_none(self):
        self.assertEqual(pds_lid_segment(''), '')
        self.assertIsNone(pds_lid_segment(None))


class BundleLidTests(TestCase):
    """Nothing a user types on the bundle form may reach a LID unpermitted."""

    def setUp(self):
        self.user = User.objects.create_user('lid', password='pw')

    def bundle(self, **kwargs):
        defaults = dict(name='lid bundle', user=self.user, version='1O00',
                        bundle_type='External')
        defaults.update(kwargs)
        return Bundle.objects.create(**defaults)

    def test_a_bundle_id_with_a_forbidden_character(self):
        lid = self.bundle(bundleID='Test#1').lid()
        self.assertTrue(WHOLE_LID.match(lid), lid)

    def test_a_bundle_id_that_is_already_clean_is_unchanged(self):
        self.assertEqual(self.bundle(bundleID='sept_ama').lid(),
                         'urn:nasa:pds-ama:sept_ama')

    def test_an_uppercase_bundle_id(self):
        self.assertEqual(self.bundle(bundleID='Sept_AMA').lid(),
                         'urn:nasa:pds-ama:sept_ama')


class InventoryAgreesWithTheLabelTests(TestCase):
    """The two composers must produce byte-identical identifiers."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-lid-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-lid-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('lidinv', password='pw')
        self.bundle = Bundle.objects.create(
            name='lid inv bundle', user=self.user, version='1O00',
            bundle_type='External', bundleID='lid_inv_bundle')
        self.collection = AdditionalCollections.objects.create(
            bundle=self.bundle, collection_name='New')

    def test_the_inventory_entry_is_a_valid_lidvid(self):
        NetCDFFile.objects.create(
            title='x', file='00000.atmos_average_pstd_-_Copy.nc',
            bundle=self.bundle, collection=self.collection, processed=True)
        entry = self.collection.member_lidvids()[0]
        lid, _, vid = entry.rpartition('::')
        self.assertEqual(vid, '1.0')
        self.assertTrue(WHOLE_LID.match(lid), lid)

    def test_the_inventory_matches_what_the_label_writer_would_compose(self):
        # Composed here the way views._process_single_netcdf composes it, from the
        # same two inputs. If either side stops cleaning, this diverges.
        name = '00000.atmos_average_pstd_-_Copy.nc'
        NetCDFFile.objects.create(
            title='x', file=name, bundle=self.bundle,
            collection=self.collection, processed=True)
        subdir_name = os.path.basename(self.collection.directory())
        expected = '{}:{}:{}'.format(self.bundle.lid(),
                                     pds_lid_segment(subdir_name),
                                     pds_lid_segment(name))
        self.assertEqual(self.collection.member_lidvids()[0],
                         expected + '::1.0')


class RuleTests(SimpleTestCase):
    """The error is no longer shown as untranslated."""

    FINDING = {
        'severity': 'ERROR', 'type': 'error.label.schema',
        'message': ("cvc-pattern-valid: Value "
                    "'urn:nasa:pds-ama:sept_ama:new:00000.atmos_average_pstd_-_Copy.nc' "
                    "is not facet-valid with respect to pattern "
                    "'urn(:[\\p{Ll}\\p{Nd}\\-._]+){3,5}' for type 'logical_identifier'."),
        'label': '00000.atmos_average_pstd_-_Copy.xml',
        'label_path': '/new/00000.atmos_average_pstd_-_Copy.xml',
        'line': 6,
        'element_path': 'Product_External/Identification_Area/logical_identifier',
    }

    def test_it_is_translated(self):
        rule = validate_rules.classify(self.FINDING)
        self.assertIsNot(rule, validate_rules.UNMAPPED)
        self.assertEqual(rule.key, 'elsa-lid-pattern')

    def test_it_is_not_put_in_front_of_the_user(self):
        # There is nothing for a data provider to do: they cannot compose a LID, and
        # the file name is allowed to stay as it is.
        translated = validate_rules.translate([self.FINDING])
        self.assertEqual(translated['user'], [])
        self.assertEqual([i['key'] for i in translated['elsa']],
                         ['elsa-lid-pattern'])

    def test_the_companion_type_error_folds_into_the_same_item(self):
        # PDS reports this twice, once as the pattern failure and once as a type
        # failure on the same element, so the panel must not show it as two problems.
        companion = dict(self.FINDING)
        companion['message'] = (
            "cvc-type.3.1.3: The value 'urn:nasa:pds-ama:sept_ama:new:"
            "00000.atmos_average_pstd_-_Copy.nc' of element 'logical_identifier' "
            "is not valid.")
        translated = validate_rules.translate([self.FINDING, companion])
        self.assertEqual(len(translated['elsa']), 1)
        self.assertEqual(len(translated['elsa'][0]['findings']), 2)

    def test_nothing_is_left_unmapped(self):
        self.assertEqual(validate_rules.translate([self.FINDING])['unmapped'], [])

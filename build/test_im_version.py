# -*- coding: utf-8 -*-
"""The PDS4 information model version ELSA stamps into every label.

PDS4 keeps a build number four characters wide by encoding any component of ten or
more as a single letter: A=10, B=11, ... O=24, ... Z=35. So build 1O00, which is
what ELSA writes today, is information model version 1.24.0.0.

Two defects are guarded here, both of which put a wrong version into every label:

* with_dots was a hand-written branch per letter that stopped at K. O fell through
  every branch and was dropped, so 1O00 became "1.0.0". 1Q00 would have done the
  same, which matters because that is the version under discussion for the move.
* version_update_old found the dotted field by counting occurrences of the AAAA
  placeholder and treating the third one as the dotted one. That is only correct
  for a template carrying exactly the expected placeholders in exactly the expected
  order; it is keyed off the element name now.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile

from django.test import SimpleTestCase

from build.models import Version


class WithDotsTests(SimpleTestCase):

    def setUp(self):
        self.version = Version()

    def test_a_plain_numeric_build(self):
        self.assertEqual(self.version.with_dots('1800'), '1.8.0.0')

    def test_the_letters_that_already_worked_still_do(self):
        for build, expected in (('1D00', '1.13.0.0'), ('1E00', '1.14.0.0'),
                                ('1K00', '1.20.0.0')):
            self.assertEqual(self.version.with_dots(build), expected, build)

    def test_the_version_elsa_writes_today(self):
        """1O00 came out as 1.0.0, because O was past the end of the old table."""
        self.assertEqual(self.version.with_dots('1O00'), '1.24.0.0')

    def test_the_version_under_discussion_for_the_move(self):
        self.assertEqual(self.version.with_dots('1Q00'), '1.26.0.0')

    def test_every_letter_maps_to_its_number(self):
        """A=10 through Z=35, so a future build cannot fall off the end again."""
        for offset in range(26):
            letter = chr(ord('A') + offset)
            self.assertEqual(self.version.with_dots('1{}00'.format(letter)),
                             '1.{}.0.0'.format(10 + offset), letter)

    def test_a_lowercase_letter_is_treated_the_same(self):
        self.assertEqual(self.version.with_dots('1o00'), '1.24.0.0')

    def test_no_component_is_ever_silently_dropped(self):
        """The old failure mode: a letter it did not know became nothing at all."""
        for build in ('1800', '1D00', '1O00', '1Q00', '1Z00'):
            self.assertEqual(len(self.version.with_dots(build).split('.')), 4, build)


class VersionUpdateTests(SimpleTestCase):
    """The substitution that puts those values into a label on disk."""

    TEMPLATE = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<?xml-model href="https://pds.nasa.gov/pds4/pds/v1/PDS4_PDS_AAAA.sch"?>\n'
        '<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1"\n'
        '   xsi:schemaLocation="PDS4_PDS_AAAA.xsd">\n'
        '  <Identification_Area>\n'
        '    <information_model_version>AAAA</information_model_version>\n'
        '  </Identification_Area>\n'
        '</Product_Bundle>\n')

    def setUp(self):
        self.version = Version()
        self.directory = tempfile.mkdtemp(prefix='elsa-imv-')
        self.addCleanup(shutil.rmtree, self.directory, True)

    def stamp(self, template=None, number='1O00'):
        source = os.path.join(self.directory, 'in.xml')
        target = os.path.join(self.directory, 'out.xml')
        with open(source, 'w', encoding='utf-8') as handle:
            handle.write(self.TEMPLATE if template is None else template)
        self.version.version_update_old(number, source, target)
        with open(target, encoding='utf-8') as handle:
            return handle.read()

    def test_the_dotted_form_goes_in_the_version_element(self):
        self.assertIn(
            '<information_model_version>1.24.0.0</information_model_version>',
            self.stamp())

    def test_the_build_number_goes_in_the_schema_names(self):
        """Those are filenames, so they take 1O00 rather than the dotted form."""
        output = self.stamp()
        self.assertIn('PDS4_PDS_1O00.sch', output)
        self.assertIn('PDS4_PDS_1O00.xsd', output)

    def test_no_placeholder_survives(self):
        self.assertNotIn('AAAA', self.stamp())

    def test_it_is_keyed_off_the_element_not_the_position(self):
        """A template with a different number of placeholders, in a different order.

        The old code counted occurrences and treated the third as the dotted one,
        so moving the version element or adding a schema reference put the dotted
        value in a filename and a build number in the version field.
        """
        template = (
            '<Product_Bundle xmlns="http://pds.nasa.gov/pds4/pds/v1">\n'
            '  <information_model_version>AAAA</information_model_version>\n'
            '  <a>PDS4_PDS_AAAA.xsd</a>\n'
            '  <b>PDS4_AMA_AAAA_1300.xsd</b>\n'
            '  <c>PDS4_PDS_AAAA.sch</c>\n'
            '</Product_Bundle>\n')
        output = self.stamp(template)
        self.assertIn(
            '<information_model_version>1.24.0.0</information_model_version>', output)
        for name in ('PDS4_PDS_1O00.xsd', 'PDS4_AMA_1O00_1300.xsd',
                     'PDS4_PDS_1O00.sch'):
            self.assertIn(name, output)

    def test_a_namespaced_version_element_is_found_too(self):
        template = ('<pds:Product_Bundle xmlns:pds="http://pds.nasa.gov/pds4/pds/v1">\n'
                    '  <pds:information_model_version>AAAA'
                    '</pds:information_model_version>\n'
                    '</pds:Product_Bundle>\n')
        self.assertIn('>1.24.0.0<', self.stamp(template))

    def test_a_template_with_no_placeholder_is_copied_unchanged(self):
        """An already-stamped label is a copy, not an error."""
        template = ('<Product_Bundle>\n'
                    '  <information_model_version>1.24.0.0'
                    '</information_model_version>\n</Product_Bundle>\n')
        self.assertEqual(self.stamp(template), template)

    def test_a_different_build_number_stamps_its_own_values(self):
        output = self.stamp(number='1Q00')
        self.assertIn(
            '<information_model_version>1.26.0.0</information_model_version>', output)
        self.assertIn('PDS4_PDS_1Q00.sch', output)

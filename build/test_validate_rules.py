"""Tests for the translation table.

The property that matters most is not that any single rule is right, it is that
nothing reaches a data provider that they cannot act on, and nothing is silently
dropped. Those two are tested first.
"""
from django.test import SimpleTestCase

from build.validate_rules import (ADVISORY, ELSA, RULES, USER, cards, classify,
                                  summarise, translate, UNMAPPED)


def finding(message='something', type_='error.label.schema', path='', label='a.xml',
            severity='ERROR'):
    return {'severity': severity, 'type': type_, 'message': message,
            'label': label, 'label_path': '/' + label, 'line': 1,
            'element_path': path}


class NothingIsLostTests(SimpleTestCase):
    """Every finding lands somewhere, and unrecognised ones are never hidden."""

    def test_an_unrecognised_finding_is_shown_not_dropped(self):
        result = translate([finding(message='a message nobody wrote a rule for')])
        self.assertEqual(len(result['advisory']), 1)
        self.assertEqual(len(result['unmapped']), 1)

    def test_an_unrecognised_finding_never_blocks_submission(self):
        """A rule nobody anticipated must not put a wall in front of a user."""
        summary = summarise([finding(message='brand new PDS rule')])
        self.assertTrue(summary['can_submit'])

    def test_an_unrecognised_finding_keeps_its_original_message(self):
        result = translate([finding(message='the exact words PDS used')])
        shown = result['advisory'][0]['findings'][0]['message']
        self.assertEqual(shown, 'the exact words PDS used')

    def test_every_finding_lands_in_exactly_one_bucket(self):
        findings = [
            finding(message='Citation_Information and its description are required'),
            finding(type_='warning.label.context_ref_mismatch', severity='WARNING'),
            finding(message='something unmapped'),
        ]
        result = translate(findings)
        total = sum(len(item['findings'])
                    for bucket in ('user', 'advisory', 'elsa')
                    for item in result[bucket])
        self.assertEqual(total, len(findings))

    def test_classify_always_returns_a_rule(self):
        self.assertIsNotNone(classify(finding(message='!!!')))


class AudienceTests(SimpleTestCase):
    """The whole point: a user sees only what a user can do something about."""

    def test_elsa_defects_are_hidden_from_the_user(self):
        result = translate([finding(
            type_='error.label.schematron',
            path='Product_Bundle/Identification_Area/information_model_version',
            message='The attribute pds:Identification_Area/pds:information_model_version '
                    "must be equal to the value '1.24.0.0'.")])
        self.assertEqual(result['user'], [])
        self.assertEqual(len(result['elsa']), 1)

    def test_a_hidden_defect_does_not_block_submission(self):
        summary = summarise([finding(
            path='Context_Area/Time_Coordinates/start_date_time',
            message="cvc-minLength-valid: Value '' with length = '0' is not valid.")])
        self.assertTrue(summary['can_submit'])
        self.assertEqual(summary['hidden'], 1)

    def test_a_user_problem_does_block_submission(self):
        summary = summarise([finding(
            message='In Product_Bundle both Citation_Information and its description '
                    'are required.')])
        self.assertFalse(summary['can_submit'])
        self.assertEqual(summary['blocking'], 1)

    def test_warnings_are_shown_but_never_block(self):
        summary = summarise([finding(
            type_='warning.label.context_ref_mismatch', severity='WARNING',
            message='Context reference name mismatch.')])
        self.assertTrue(summary['can_submit'])
        self.assertEqual(summary['advisory'], 1)


class CollapsingTests(SimpleTestCase):
    """One thing to do reads as one line, however many times PDS says it."""

    def test_citation_reported_on_four_labels_is_one_item(self):
        message = ('In Product_Collection both Citation_Information and its '
                   'description are required.')
        findings = [finding(message=message, label='label{}.xml'.format(n))
                    for n in range(4)]
        result = translate(findings)
        self.assertEqual(len(result['user']), 1)
        self.assertEqual(len(result['user'][0]['labels']), 4)

    def test_an_empty_collection_is_one_item_not_four(self):
        """PDS reports a bad record count and a bad record length, twice each."""
        findings = [
            finding(path='File_Area_Inventory/Inventory/records',
                    message="cvc-minInclusive-valid: Value '0' is not facet-valid"),
            finding(path='File_Area_Inventory/Inventory/records',
                    message="cvc-type.3.1.3: The value '0' of element 'records'"),
            finding(path='File_Area_Inventory/Inventory/Record_Delimited/maximum_record_length',
                    message="cvc-minInclusive-valid: Value '0' is not facet-valid"),
            finding(path='File_Area_Inventory/Inventory/Record_Delimited/maximum_record_length',
                    message="cvc-complex-type.2.2: Element must have no element children"),
        ]
        result = translate(findings)
        self.assertEqual(len(result['user']), 1)
        self.assertEqual(len(result['user'][0]['findings']), 4)

    def test_the_raw_findings_stay_reachable(self):
        """Technical detail is one click away, never gone."""
        message = 'In Product_Bundle both Citation_Information and its description are required.'
        result = translate([finding(message=message)])
        self.assertEqual(result['user'][0]['findings'][0]['message'], message)


class WordingTests(SimpleTestCase):
    """Copy is the product here, so it gets asserted like anything else."""

    def test_no_rule_shows_a_raw_pds_error_code(self):
        for rule in RULES + [UNMAPPED]:
            for text in (rule.title, rule.detail):
                self.assertNotIn('cvc-', text, rule.key)
                self.assertNotIn('facet-valid', text, rule.key)

    def test_user_facing_rules_say_what_to_do(self):
        """A user-facing item that only names the problem is half an instruction."""
        doing_words = ('add', 'choose', 'pick', 'rename', 'remove', 're-upload',
                       'upload', 'fill', 'set', 'select')
        for rule in RULES:
            if rule.audience != USER:
                continue
            text = (rule.title + ' ' + rule.detail).lower()
            self.assertTrue(any(word in text for word in doing_words),
                            '{} never says what to do'.format(rule.key))

    def test_every_user_rule_names_the_card_that_fixes_it(self):
        for rule in RULES:
            if rule.audience == USER:
                self.assertTrue(rule.card, '{} has nowhere to send the user'.format(rule.key))

    def test_hidden_rules_explain_themselves_to_staff(self):
        for rule in RULES:
            if rule.audience == ELSA:
                self.assertTrue(len(rule.detail) > 30, rule.key)


class GroupingTests(SimpleTestCase):

    def test_items_are_grouped_by_the_card_that_fixes_them(self):
        findings = [
            finding(message='In Product_Bundle both Citation_Information and its '
                            'description are required.'),
            finding(path='File_Area_Inventory/Inventory/records',
                    message="Value '0' is not facet-valid"),
        ]
        grouped = dict(cards(findings))
        self.assertIn('Citation Information', grouped)
        self.assertIn('Documents', grouped)

    def test_a_clean_bundle_produces_nothing_to_fix(self):
        summary = summarise([])
        self.assertTrue(summary['can_submit'])
        self.assertEqual(summary['blocking'], 0)

    def test_the_biggest_problem_comes_first(self):
        findings = [finding(message='In Product_Bundle both Citation_Information and '
                                    'its description are required.')]
        findings += [finding(path='File_Area_Inventory/Inventory/records',
                             message="Value '0' is not facet-valid",
                             label='l{}.xml'.format(n)) for n in range(5)]
        result = translate(findings)
        self.assertEqual(result['user'][0]['key'], 'collection-empty')


class RuleTableIntegrityTests(SimpleTestCase):

    def test_rule_keys_are_unique(self):
        keys = [rule.key for rule in RULES]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_rule_can_match_something(self):
        """A rule with no criteria would match nothing and hide later rules."""
        for rule in RULES:
            self.assertTrue(rule.types or rule.path or rule.message, rule.key)

    def test_every_rule_has_a_known_audience(self):
        for rule in RULES:
            self.assertIn(rule.audience, (USER, ADVISORY, ELSA), rule.key)

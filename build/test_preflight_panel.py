"""The pre-flight panel as it renders on the bundle page.

Renders the real page through the real view in each state the panel can be in, and
asserts on what a person would see. The thing being guarded is that a data provider
is never shown a finding they cannot act on, and never shown a raw PDS error code.
"""
import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from build.models import Bundle, Investigation, ValidationRun


def finding(message, type_='error.label.schema', path='', label='bundle_x.xml'):
    return {'severity': 'ERROR', 'type': type_, 'message': message, 'label': label,
            'label_path': '/' + label, 'line': 1, 'element_path': path}


CITATION = finding(
    'In Product_Bundle both Citation_Information and its description are required.',
    type_='error.label.schematron', path='Product_Bundle/Identification_Area')

EMPTY_COLLECTION = finding(
    "cvc-minInclusive-valid: Value '0' is not facet-valid with respect to minInclusive '1'.",
    path='Product_Collection/File_Area_Inventory/Inventory/records')

ELSA_DEFECT = finding(
    "cvc-minLength-valid: Value '' with length = '0' is not facet-valid.",
    path='Product_Bundle/Context_Area/Time_Coordinates/start_date_time')

ADVISORY = {'severity': 'WARNING', 'type': 'warning.label.context_ref_mismatch',
            'message': 'Context reference name mismatch.', 'label': 'bundle_x.xml',
            'label_path': '/bundle_x.xml', 'line': 3, 'element_path': ''}


class PreflightPanelTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-panel-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.media = tempfile.mkdtemp(prefix='elsa-panel-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        media_patcher = override_settings(MEDIA_ROOT=self.media)
        media_patcher.enable()
        self.addCleanup(media_patcher.disable)

        self.user = User.objects.create_user('panel', password='pw')
        self.client.login(username='panel', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')

        # Built through the real view, because the bundle page redirects away from a
        # Bundle row that has no Product_Bundle behind it.
        response = self.client.post(reverse('build:build'), {
            'name': 'panel bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.assertIn(response.status_code, (200, 302))
        self.bundle = Bundle.objects.get(name='panel bundle')

    def page(self):
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    # The page now carries two tabs. "What to fix" is the translated one and is what
    # opens; "Validation output" is the raw PDS text, shown deliberately. An assertion
    # about jargon has to say which of the two it means, so these split the body at
    # the boundary between them.
    RAW_PANE = 'id="preflight_pane_raw"'

    def fix_pane(self, response=None):
        body = (response or self.page()).content.decode()
        return body.split(self.RAW_PANE)[0]

    def raw_pane(self, response=None):
        body = (response or self.page()).content.decode()
        self.assertIn(self.RAW_PANE, body, 'the raw output tab is not on the page')
        return body.split(self.RAW_PANE)[1]

    def run_with(self, findings, **kwargs):
        defaults = dict(bundle=self.bundle, status=ValidationRun.STATUS_DONE,
                        products_total=2, products_done=2,
                        phase=ValidationRun.PHASE_DONE, findings=findings,
                        error_count=len(findings), bundle_updated_at=self.bundle.updated_at)
        defaults.update(kwargs)
        return ValidationRun.objects.create(**defaults)

    # -- the states the panel can be in -----------------------------------------

    def test_a_bundle_never_checked_says_a_check_is_starting(self):
        """With auto-check on, the page starts one itself and says so."""
        with override_settings(VALIDATE_AUTO_CHECK=True):
            page = self.page()
        self.assertContains(page, 'PDS Validation')
        self.assertContains(page, 'Checking this bundle')
        self.assertContains(page, 'data-auto-check="1"')

    def test_a_bundle_never_checked_invites_a_check_when_auto_is_off(self):
        with override_settings(VALIDATE_AUTO_CHECK=False):
            page = self.page()
        self.assertContains(page, 'has not been checked yet')
        self.assertContains(page, 'Check this bundle')
        self.assertNotContains(page, 'data-auto-check="1"')

    def test_findings_are_shown_in_plain_language(self):
        self.run_with([CITATION])
        page = self.page()
        self.assertContains(page, 'Add Citation Information')
        self.assertContains(page, 'Citation Information')

    def test_a_raw_pds_error_code_never_reaches_the_translated_tab(self):
        """The whole point of the translation layer."""
        self.run_with([CITATION, EMPTY_COLLECTION])
        pane = self.fix_pane()
        for jargon in ('cvc-minInclusive-valid', 'cvc-minLength-valid', 'facet-valid',
                       'Product_Collection/File_Area_Inventory'):
            self.assertNotIn(jargon, pane, 'raw PDS output leaked into the translation')

    def test_an_elsa_defect_is_not_shown_in_the_translated_tab(self):
        """A finding they cannot act on is noise, and teaches them to ignore the panel."""
        self.run_with([ELSA_DEFECT])
        pane = self.fix_pane()
        self.assertNotIn('Time_Coordinates', pane)
        self.assertNotIn('An empty container that ELSA wrote', pane)

    def test_an_elsa_defect_does_not_count_against_the_user(self):
        self.run_with([ELSA_DEFECT])
        page = self.page()
        self.assertEqual(page.context['validation_summary']['blocking'], 0)
        self.assertTrue(page.context['validation_summary']['can_submit'])

    def test_a_clean_bundle_says_so(self):
        self.run_with([ADVISORY])
        page = self.page()
        self.assertContains(page, 'Nothing is blocking this bundle')

    def test_what_passed_is_counted_too(self):
        """A panel that only ever reports failure is one people avoid opening."""
        self.run_with([CITATION], products_total=12)
        self.assertContains(self.page(), 'Labels checked')

    def test_advisory_items_are_shown_separately(self):
        self.run_with([ADVISORY])
        self.assertContains(self.page(), 'Worth reviewing')

    def test_stale_results_are_marked(self):
        run = self.run_with([CITATION])
        self.bundle.save()                      # auto_now bumps updated_at
        run.refresh_from_db()
        self.assertContains(self.page(), 'out of date')

    def test_a_failed_run_explains_itself(self):
        self.run_with([], status=ValidationRun.STATUS_FAILED,
                      failure_reason='validate is not installed on this host')
        self.assertContains(self.page(), 'not installed')

    # -- the Fix buttons ---------------------------------------------------------

    def test_a_fix_button_points_at_the_modal_that_fixes_it(self):
        self.run_with([CITATION])
        self.assertContains(self.page(), 'data-bs-target="#citation_information_modal"')

    def test_items_are_grouped_by_card_not_by_file(self):
        self.run_with([CITATION, EMPTY_COLLECTION])
        page = self.page()
        names = [card for card, _items in page.context['validation_cards']]
        self.assertIn('Citation Information', names)
        self.assertIn('Documents', names)

    def test_one_problem_on_four_labels_reads_as_one_item(self):
        findings = [finding(
            'In Product_Collection both Citation_Information and its description are required.',
            type_='error.label.schematron', path='Product_Collection/Identification_Area',
            label='collection_{}.xml'.format(n)) for n in range(4)]
        self.run_with(findings)
        page = self.page()
        self.assertEqual(page.context['validation_summary']['blocking'], 1)
        self.assertContains(page, 'Affects 4 labels')

    # -- the two tabs ------------------------------------------------------------

    def test_both_tabs_are_offered(self):
        self.run_with([CITATION])
        page = self.page()
        self.assertContains(page, 'What to fix')
        self.assertContains(page, 'Validation output')

    def test_the_translated_tab_is_the_one_that_opens(self):
        """Raw output has to be a deliberate click, or the translation buys nothing."""
        self.run_with([CITATION, ELSA_DEFECT])
        body = self.page().content.decode()
        fix_tab = body.split('id="preflight_tab_fix"')[1].split('>')[0]
        raw_tab = body.split('id="preflight_tab_raw"')[1].split('>')[0]
        self.assertIn('aria-selected="true"', fix_tab)
        self.assertIn('aria-selected="false"', raw_tab)
        # The button carrying "active" is the one Bootstrap opens on.
        self.assertIn('class="nav-link active" id="preflight_tab_fix"', body)

    def test_only_the_translated_pane_starts_active(self):
        """Both panes visible at once would show the same findings twice."""
        self.run_with([CITATION])
        body = self.page().content.decode()
        self.assertIn('class="tab-pane fade show active" id="preflight_pane_fix"', body)
        self.assertIn('class="tab-pane fade" id="preflight_pane_raw"', body)

    def test_the_raw_tab_shows_what_pds_actually_said(self):
        self.run_with([CITATION, EMPTY_COLLECTION])
        pane = self.raw_pane()
        self.assertIn('cvc-minInclusive-valid', pane)
        self.assertIn(
            'In Product_Bundle both Citation_Information and its description are required.',
            pane)

    def test_the_raw_tab_hides_nothing_the_translation_hides(self):
        """The reason for offering it: nobody has to take the translation on trust."""
        self.run_with([ELSA_DEFECT])
        pane = self.raw_pane()
        self.assertIn('Time_Coordinates', pane)
        self.assertIn('cvc-minLength-valid', pane)

    def test_a_three_kilobyte_message_is_folded_not_truncated(self):
        """PDS's date-time pattern message runs past 3000 characters."""
        huge = 'cvc-pattern-valid: Value is not facet-valid with respect to ' + 'A|' * 1600
        self.run_with([finding(huge)])
        pane = self.raw_pane()
        self.assertIn('<details>', pane)
        self.assertIn(huge[:120], pane)         # the opening words identify it
        self.assertIn(huge[-60:], pane)         # and the whole thing is still there

    def test_a_short_message_is_not_folded(self):
        self.run_with([finding('Modification_History is not complete.')])
        pane = self.raw_pane()
        self.assertIn('Modification_History is not complete.', pane)
        self.assertNotIn('<details>', pane)

    def test_the_raw_tab_counts_every_finding_not_just_the_shown_ones(self):
        self.run_with([CITATION, ELSA_DEFECT, ADVISORY])
        page = self.page()
        self.assertEqual(page.context['validation_raw_total'], 3)
        self.assertEqual(page.context['validation_summary']['blocking'], 1)

    def test_the_raw_tab_groups_by_the_label_the_finding_came_from(self):
        self.run_with([finding('first problem', label='collection_b.xml'),
                       finding('second problem', label='bundle_a.xml')])
        page = self.page()
        names = [name for name, _items in page.context['validation_raw']]
        self.assertEqual(names, ['bundle_a.xml', 'collection_b.xml'])
        pane = self.raw_pane(page)
        self.assertLess(pane.index('bundle_a.xml'), pane.index('collection_b.xml'))

    def test_a_finding_with_no_label_is_still_shown(self):
        """Bundle-level findings carry no label path; they must not vanish."""
        self.run_with([finding('something about the bundle as a whole', label='')])
        page = self.page()
        self.assertEqual([name for name, _ in page.context['validation_raw']], ['bundle'])
        self.assertIn('something about the bundle as a whole', self.raw_pane(page))

    def test_the_raw_tab_is_shown_to_the_owner_not_only_to_staff(self):
        self.run_with([CITATION, EMPTY_COLLECTION])
        self.assertFalse(self.user.is_staff)
        # raw_pane() fails if the tab is absent; the jargon proves it is populated.
        self.assertIn('cvc-minInclusive-valid', self.raw_pane())
        self.assertNotContains(self.page(), 'Full report')

    def test_no_tabs_before_a_check_has_run(self):
        """Nothing to put in either tab, so neither is drawn."""
        body = self.page().content.decode()
        self.assertNotIn('preflight_tab_raw', body)

    def test_a_run_that_reported_nothing_says_the_bundle_passed(self):
        self.run_with([], products_total=9)
        page = self.page()
        self.assertContains(page, 'PDS reported nothing on this bundle')
        self.assertNotContains(page, 'finished without storing any findings')

    # -- where the panel lives ---------------------------------------------------

    def test_validation_is_a_row_in_the_bundle_components_card(self):
        """Not a card of its own: it had a full-width card to say nothing most days."""
        self.run_with([CITATION])
        page = self.page()
        self.assertContains(page, 'preflight_row_badge')
        self.assertContains(page, 'data-bs-target="#validation_modal"')
        self.assertNotContains(page, 'id="preflight_card"')

    def test_the_row_badge_counts_what_the_user_must_fix(self):
        """Not the raw error count, which is an order of magnitude larger."""
        self.run_with([CITATION, ELSA_DEFECT, EMPTY_COLLECTION])
        body = self.page().content.decode()
        badge = body.split('id="preflight_row_badge"')[0].rsplit('<span', 1)[-1] \
            + body.split('id="preflight_row_badge"')[1].split('</span>')[0]
        self.assertIn('2 to fix', badge)
        self.assertIn('bg-danger', badge)

    def test_the_row_badge_says_passed_when_nothing_blocks(self):
        self.run_with([ADVISORY])
        badge = self.page().content.decode().split('id="preflight_row_badge"')[1]
        self.assertIn('Passed', badge.split('</span>')[0])

    def test_the_row_badge_prefers_out_of_date_over_passed(self):
        """Results that no longer describe the bundle are not a pass."""
        self.run_with([ADVISORY])
        self.bundle.save()                      # auto_now bumps updated_at
        body = self.page().content.decode()
        badge = body.split('id="preflight_row_badge"')[1].split('</span>')[0]
        self.assertIn('Out of date', badge)
        self.assertNotIn('Passed', badge)

    def test_the_row_badge_says_not_checked_before_a_run(self):
        with override_settings(VALIDATE_AUTO_CHECK=False):
            body = self.page().content.decode()
        self.assertIn('Not checked', body.split('id="preflight_row_badge"')[1])

    def test_the_modal_is_rendered_once(self):
        self.run_with([CITATION])
        self.assertEqual(self.page().content.decode().count('id="validation_modal"'), 1)

    def test_the_modal_exists_for_an_archive_bundle_too(self):
        """The row is in both layouts; the modal was inside the External branch."""
        response = self.client.post(reverse('build:build'), {
            'name': 'panel archive', 'bundle_type': 'Archive',
            'version': '1O00', 'bundleID': ''})
        self.assertIn(response.status_code, (200, 302))
        archive = Bundle.objects.get(name='panel archive')
        page = self.client.get(reverse('build:bundle', args=[archive.pk]))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn('preflight_row_badge', body)
        self.assertIn('id="validation_modal"', body)

    # -- the review modal defers to validation -----------------------------------

    def test_the_review_modal_states_the_validation_verdict(self):
        self.run_with([CITATION, EMPTY_COLLECTION])
        page = self.page()
        self.assertContains(page, 'PDS validation found')
        self.assertContains(page, '2 things')

    def test_the_review_modal_says_so_when_nothing_blocks(self):
        self.run_with([ADVISORY])
        self.assertContains(self.page(), 'found nothing blocking this bundle')

    def test_the_review_modal_flags_stale_results_before_sending(self):
        self.run_with([ADVISORY])
        self.bundle.save()
        self.assertContains(self.page(), 'no longer describe what you are about to send')

    def test_the_review_modal_no_longer_renders_its_own_verdict_heading(self):
        """The component list is an inventory now, not the thing that decides."""
        self.run_with([CITATION])
        page = self.page()
        self.assertContains(page, 'What you are sending')

    @override_settings(VALIDATE_BLOCKS_SUBMISSION=True)
    def test_the_review_modal_submit_is_disabled_when_validation_blocks(self):
        """It used to walk people into the next modal, which then refused them.

        Checked by the reason on the button, not merely by "disabled": this bundle
        has no components filled in either, so it would be disabled regardless and
        the assertion would pass without the gate being wired at all.
        """
        self.run_with([CITATION])
        page = self.page()
        reason = page.context['validation_block'][1]
        self.assertIn('title="{}"'.format(reason), page.content.decode())

    @override_settings(VALIDATE_BLOCKS_SUBMISSION=True)
    def test_validation_stops_blocking_once_it_passes(self):
        """Whereupon the footer falls through to the component check, as before."""
        self.run_with([ADVISORY])
        page = self.page()
        self.assertIsNone(page.context['validation_block'])
        self.assertNotContains(page, 'Run the PDS validation check before submitting')

    # -- refreshing itself -------------------------------------------------------

    def test_the_panel_can_be_fetched_on_its_own(self):
        """What the page swaps in when a check finishes."""
        self.run_with([CITATION])
        response = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'preflight_body')
        self.assertContains(response, 'Add Citation Information')

    def test_the_fetched_panel_matches_what_the_page_renders(self):
        """Both come from one context builder, so they cannot drift apart."""
        self.run_with([CITATION, EMPTY_COLLECTION])
        partial = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk]))
        page = self.page()
        for key in ('validation_summary', 'validation_raw_total'):
            self.assertEqual(partial.context[key], page.context[key], key)
        self.assertEqual(
            [name for name, _ in partial.context['validation_cards']],
            [name for name, _ in page.context['validation_cards']])

    def test_the_fetched_panel_carries_the_swappable_region(self):
        """The page replaces this element's contents and nothing else."""
        self.run_with([CITATION])
        body = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk])).content.decode()
        self.assertIn('id="preflight_body"', body)

    def test_the_run_button_is_outside_the_swapped_region(self):
        """Replacing it would throw away the click handler bound to it."""
        body = self.page().content.decode()
        region = body.split('id="preflight_body"')[1].split('<!-- /preflight_body -->')[0]
        self.assertIn('<!-- /preflight_body -->', body,
                      'the boundary marker is gone, so this test proves nothing')
        self.assertNotIn('id="preflight_run"', region)

    def test_nobody_is_asked_to_refresh_the_page(self):
        """The panel used to say "Refresh to see the details"."""
        self.run_with([CITATION])
        page = self.page()
        self.assertNotContains(page, 'Refresh to see the details')
        self.assertNotContains(page, 'window.location.reload(); return false;')

    def test_another_user_cannot_fetch_the_panel(self):
        self.run_with([CITATION])
        other = User.objects.create_user('panelthief', password='pw')
        self.client.force_login(other)
        response = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk]))
        self.assertNotEqual(response.status_code, 200)

    def test_fetching_the_panel_starts_nothing(self):
        """It is fetched whenever a check finishes; it must not start another."""
        self.run_with([CITATION])
        before = ValidationRun.objects.count()
        with override_settings(VALIDATE_AUTO_CHECK=True):
            self.client.get(reverse('build:validation_panel', args=[self.bundle.pk]))
        self.assertEqual(ValidationRun.objects.count(), before)

    def test_the_fetched_fragments_carry_the_review_verdict_too(self):
        """The panel is not the only thing that shows the result.

        The Review & Submit window states the verdict and decides whether to offer a
        Submit button. Refreshing only the panel left both of those answering with
        whatever was true at page load, which is how the window came to refuse a
        submission over a stale result that had already been re-checked.
        """
        self.run_with([CITATION])
        body = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk])).content.decode()
        for element in ('preflight_body', 'validation_verdict', 'review_submit_action'):
            self.assertIn('id="{}"'.format(element), body,
                          '{} is not in what the page fetches'.format(element))

    def test_the_page_and_the_fragments_agree_about_the_verdict(self):
        self.run_with([CITATION, EMPTY_COLLECTION])
        page = self.page().content.decode()
        fragments = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk])).content.decode()

        def verdict(text):
            return text.split('id="validation_verdict"')[1].split(
                '<!-- /validation_verdict -->')[0]

        self.assertIn('2 things', verdict(page))
        self.assertIn('2 things', verdict(fragments))

    @override_settings(VALIDATE_BLOCKS_SUBMISSION=True)
    def test_a_cleared_result_lets_the_fragments_offer_submit_again(self):
        """The reported symptom: blocked on a result that no longer applies."""
        stale = self.run_with([CITATION])
        self.bundle.save()                      # the bundle changed: now stale
        stale.refresh_from_db()
        self.assertTrue(stale.is_stale())
        blocked = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk]))
        self.assertIsNotNone(blocked.context['validation_block'])

        # A fresh check that finds nothing, exactly as pressing Check again would.
        ValidationRun.objects.all().delete()
        self.run_with([ADVISORY])

        cleared = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk]))
        self.assertIsNone(cleared.context['validation_block'],
                          'the fragments still report the superseded block')
        self.assertNotIn('no longer describe what you are about to send',
                         cleared.content.decode())

    def test_the_review_window_refreshes_itself_when_opened(self):
        """A check can finish between the page loading and someone deciding to send."""
        with open('templates/build/bundle/bundle.html', encoding='utf-8') as handle:
            text = handle.read()
        start = text.index('Pre-flight check: start a run')
        panel_script = text[start:text.index('</script>', start)]
        self.assertIn("show.bs.modal", panel_script)
        self.assertIn('reviewBundleModal', panel_script)

    # -- the controls ------------------------------------------------------------

    def test_the_panel_offers_to_run_a_check(self):
        page = self.page()
        self.assertContains(page, 'preflight_run')
        self.assertContains(page, reverse('build:start_validation', args=[self.bundle.pk]))
        self.assertContains(page, reverse('build:validation_status', args=[self.bundle.pk]))

    def test_the_technical_tally_lives_with_the_raw_output(self):
        """One place for the technical view, not a disclosure beside a tab for it."""
        self.run_with([CITATION], products_total=7)
        page = self.page()
        self.assertNotContains(page, 'Technical detail')
        self.assertIn('across 7 labels', self.raw_pane(page))

    def test_the_full_report_link_is_staff_only(self):
        run = self.run_with([CITATION])
        self.assertNotContains(self.page(), 'Full report')

        staff = User.objects.create_user('panelstaff', password='pw', is_staff=True)
        self.bundle.user = staff
        self.bundle.save()
        self.client.force_login(staff)
        self.assertContains(self.page(), 'Full report')

    def test_nothing_is_started_just_by_opening_the_page(self):
        """Opening a bundle must not spawn a JVM."""
        self.page()
        self.assertEqual(ValidationRun.objects.filter(bundle=self.bundle).count(), 0)


class TemplateHygieneTests(TestCase):
    """Django's {# #} comment is single-line only.

    A multi-line one is not a comment at all: it renders as visible text on the
    page. Two were live in the pre-flight panel until the rendered output was read.

    build.tests.AMARegressionTests already guards this by rendering the bundle page
    and looking for '{#' in the output, which is the stronger check and is what
    caught the panel. This one scans the source of the two staff templates as well,
    which that test never renders, and it names the offending file rather than only
    reporting that something leaked.
    """

    TEMPLATES = [
        'templates/build/validation/panel.html',
        'templates/build/validation/report.html',
        'templates/build/validation/runs.html',
    ]

    def test_no_multiline_hash_comments(self):
        import re
        for path in self.TEMPLATES:
            with open(path, encoding='utf-8') as handle:
                text = handle.read()
            spans = re.findall(r'\{#(?:(?!#\}).)*?\n(?:(?!#\}).)*?#\}', text, re.S)
            self.assertEqual(
                spans, [],
                '{} has a multi-line {{# #}} that will render as visible text; '
                'use {{% comment %}}'.format(path))


class ExplainButtonTests(TestCase):
    """"Why does this matter?" routes through the existing assistant, on demand only."""

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-explain-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-explain-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('explain', password='pw')
        self.client.login(username='explain', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'explain bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='explain bundle')
        ValidationRun.objects.create(
            bundle=self.bundle, status=ValidationRun.STATUS_DONE, products_total=2,
            products_done=2, phase=ValidationRun.PHASE_DONE, findings=[CITATION],
            error_count=1, bundle_updated_at=self.bundle.updated_at)

    def page(self):
        return self.client.get(reverse('build:bundle', args=[self.bundle.pk]))

    def test_each_item_offers_an_explanation(self):
        self.assertContains(self.page(), 'Why does this matter?')

    def test_the_question_names_the_actual_problem(self):
        body = self.page().content.decode()
        self.assertIn('preflight-explain', body)
        self.assertIn('Add Citation Information', body)

    def test_it_goes_through_the_existing_assistant(self):
        """Not a second path to a model: the assistant has the knowledge base,
        the rate limiting and the history."""
        body = self.page().content.decode()
        self.assertIn('window.elsaAssistant', body)

    def test_nothing_is_asked_without_a_click(self):
        """The assistant runs on a quota; an explanation per finding would exhaust it."""
        body = self.page().content.decode()
        self.assertNotIn('elsaAssistant.ask(', body.split('preflight-explain')[0])


class NeverReloadsThePageTests(TestCase):
    """The validation panel must never reload the page by itself.

    It did, so the results could be rendered once in the template rather than twice.
    That broke NetCDF uploads in a way only visible in use: a check starts by itself
    on page load, the upload posts over XMLHttpRequest, and a reload during those few
    seconds aborted the upload with its progress bar still saying "Processing". No
    row was created and nothing told the user why.
    """

    def test_the_panel_script_does_not_reload_on_completion(self):
        with open('templates/build/bundle/bundle.html', encoding='utf-8') as handle:
            text = handle.read()

        start = text.index('Pre-flight check: start a run')
        end = text.index('</script>', start)
        panel_script = text[start:end]

        self.assertNotIn(
            'window.location.reload', panel_script,
            'the validation panel reloads the page, which aborts an upload in flight')

    def test_the_upload_still_posts_over_xhr(self):
        """The reason the reload mattered. If this ever changes, revisit the rule."""
        with open('templates/build/bundle/bundle.html', encoding='utf-8') as handle:
            self.assertIn('new XMLHttpRequest', handle.read())

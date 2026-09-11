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

# -*- coding: utf-8 -*-
"""End to end: changing a bundle makes the validator run again on its own.

Drives the real views and reads what the bundle page would do, because the bug was
invisible anywhere else. Staleness was measured against Bundle.updated_at, which is
auto_now and therefore only moves when Bundle.save() is called, and not one of the
views that edits a bundle's metadata saves the bundle. Adding a modification history
rewrote four labels and left updated_at exactly where it was, so no result was ever
stale, the automatic check never had a reason to fire, and the only way to refresh
the panel was to press the button.

Staleness is now measured against the bundle's files, which is what the validator
reads and what every one of these views actually changes.
"""

from __future__ import unicode_literals

import os
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from build import validate_runner
from build.requirement_fixture import satisfy_requirements
from build.models import (AdditionalCollections, Bundle, Citation_Information,
                          Investigation, ValidationRun)


@override_settings(VALIDATE_AUTO_CHECK=True)
class AutoRecheckOnChangeTests(TestCase):

    def setUp(self):
        self.archive = tempfile.mkdtemp(prefix='elsa-recheck-')
        self.addCleanup(shutil.rmtree, self.archive, True)
        self.media = tempfile.mkdtemp(prefix='elsa-recheck-media-')
        self.addCleanup(shutil.rmtree, self.media, True)
        patcher = override_settings(ARCHIVE_DIR=self.archive, MEDIA_ROOT=self.media)
        patcher.enable()
        self.addCleanup(patcher.disable)

        self.user = User.objects.create_user('recheck', password='pw')
        self.client.login(username='recheck', password='pw')
        Investigation.objects.create(
            name='Atmospheric Modeling Annex', type_of='Individual Investigation',
            lid='urn:nasa:pds:context:investigation:individual.atmospheric_modeling_annex',
            file_ref='')
        self.client.post(reverse('build:build'), {
            'name': 'recheck bundle', 'bundle_type': 'External',
            'version': '1O00', 'bundleID': ''})
        self.bundle = Bundle.objects.get(name='recheck bundle')
        # The citation is left outstanding: adding one is the change these tests use
        # to trigger a recheck, and supplying it here would give them two.
        satisfy_requirements(self.bundle, skip=['citation-missing'])

    # -- helpers -----------------------------------------------------------------

    def record_a_finished_check(self):
        """A completed run describing the bundle exactly as it is now."""
        run = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_DONE, phase=ValidationRun.PHASE_DONE,
            products_total=2, products_done=2,
            bundle_updated_at=self.bundle.updated_at,
            content_fingerprint=self.bundle.content_fingerprint())
        ValidationRun.objects.filter(pk=run.pk).update(finished_at=timezone.now())
        run.refresh_from_db()
        return run

    def page_will_recheck(self):
        """What the bundle page decides, read off the rendered page itself."""
        response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return b'data-auto-check="1"' in response.content

    def add_modification_history(self, description='Initial version'):
        return self.client.post(
            reverse('build:modification_history', args=[str(self.bundle.pk)]),
            {'version_id': '1.0', 'modification_date': '2026-01-15',
             'description': description})

    def add_citation(self):
        self.client.post(
            reverse('build:citation_information', args=[str(self.bundle.pk)]),
            {'number_of_authors_people': 1, 'number_of_authors_organization': 0,
             'number_of_editors_people': 0, 'number_of_editors_organization': 0,
             'publication_year': '2026', 'description': 'A description', 'keyword': 'k'})
        return Citation_Information.objects.get(bundle=self.bundle)

    # -- the baseline the fix depends on -----------------------------------------

    def test_a_bundle_never_checked_is_checked(self):
        self.assertTrue(self.page_will_recheck())

    def test_an_untouched_bundle_is_left_alone(self):
        self.record_a_finished_check()
        self.assertFalse(self.page_will_recheck())

    def test_opening_the_page_repeatedly_starts_nothing(self):
        """Every load would otherwise be a JVM."""
        self.record_a_finished_check()
        for _ in range(5):
            self.assertFalse(self.page_will_recheck())

    # -- the reported case -------------------------------------------------------

    def test_adding_a_modification_history_triggers_a_recheck(self):
        """The exact report: the panel said 2 to fix, one was the modification
        history, and adding it changed nothing until the button was pressed."""
        self.record_a_finished_check()
        self.assertFalse(self.page_will_recheck(), 'not a valid starting point')

        self.add_modification_history()

        self.assertTrue(self.page_will_recheck(),
                        'adding a modification history did not trigger a recheck')

    def test_adding_a_citation_triggers_a_recheck(self):
        self.record_a_finished_check()
        self.add_citation()
        self.assertTrue(self.page_will_recheck())

    def test_editing_a_citation_triggers_a_recheck(self):
        citation = self.add_citation()
        self.record_a_finished_check()
        self.assertFalse(self.page_will_recheck())

        self.client.post(
            reverse('build:edit_citation_information',
                    args=[str(self.bundle.pk), str(citation.pk)]),
            {'author_person_0_given_name': 'Ada',
             'author_person_0_family_name': 'Lovelace',
             'author_person_0_orcid': '0000-0001-2345-6789',
             'author_person_0_affiliation': 'New Mexico State University'})

        self.assertTrue(self.page_will_recheck(), 'an edit did not trigger a recheck')

    def test_deleting_something_triggers_a_recheck(self):
        citation = self.add_citation()
        self.record_a_finished_check()
        self.assertFalse(self.page_will_recheck())

        self.client.get(reverse('build:citation_information_delete',
                                args=[str(self.bundle.pk), str(citation.pk)]))

        self.assertTrue(self.page_will_recheck(), 'a delete did not trigger a recheck')

    def test_adding_a_collection_triggers_a_recheck(self):
        self.record_a_finished_check()
        self.client.post(reverse('build:bundle', kwargs={'pk_bundle': self.bundle.pk}),
                         {'collection_name': 'sims', 'collection_type': 'External'})
        self.assertTrue(self.page_will_recheck())

    def test_the_recheck_settles_rather_than_repeating(self):
        """The property that makes running with no cooldown safe.

        A change triggers one run; once that run describes the files, the next load
        is not stale and starts nothing. It repeats only while someone keeps editing.
        """
        self.record_a_finished_check()
        self.add_modification_history()
        self.assertTrue(self.page_will_recheck())

        self.record_a_finished_check()          # as if that run had completed
        for _ in range(3):
            self.assertFalse(self.page_will_recheck(),
                             'the recheck did not settle and would run forever')

    # -- the manual button stays -------------------------------------------------

    def test_the_manual_button_is_always_there(self):
        """Explicitly asked for: automatic checks must not replace the button."""
        for _ in range(2):
            response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
            self.assertContains(response, 'id="preflight_run"')
            self.record_a_finished_check()
            self.add_modification_history()

    def test_the_button_works_when_automatic_checks_are_off(self):
        self.record_a_finished_check()
        with override_settings(VALIDATE_AUTO_CHECK=False):
            response = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
            self.assertContains(response, 'id="preflight_run"')
            self.assertNotContains(response, 'data-auto-check="1"')

    # -- what the staleness is measured against ----------------------------------

    def test_the_old_timestamp_signal_really_was_dead(self):
        """Guards the diagnosis, so nobody restores the timestamp comparison.

        If a view ever does start saving the bundle this will still pass; what it
        pins down is that the metadata views do not, which is why the timestamp
        cannot be the signal.
        """
        before = Bundle.objects.get(pk=self.bundle.pk).updated_at
        self.add_modification_history()
        after = Bundle.objects.get(pk=self.bundle.pk).updated_at
        self.assertEqual(before, after,
                         'Bundle.updated_at moved; the comment explaining why '
                         'staleness cannot rely on it needs revisiting')

    def test_a_run_records_what_the_files_looked_like(self):
        run = self.record_a_finished_check()
        self.assertTrue(run.content_fingerprint)
        self.assertFalse(run.is_stale())

        self.add_modification_history()
        run.refresh_from_db()
        self.assertTrue(run.is_stale())

    def test_a_bundle_with_no_directory_does_not_claim_to_be_stale(self):
        run = self.record_a_finished_check()
        shutil.rmtree(self.bundle.directory(), ignore_errors=True)
        run.refresh_from_db()
        self.assertFalse(run.is_stale())


@override_settings(VALIDATE_AUTO_CHECK=True)
class PanelRefreshesItselfTests(AutoRecheckOnChangeTests):
    """The panel replaces its own contents when a check finishes.

    It used to say "Results updated. Refresh to see the details", which asked someone
    to reload a page to be shown a result the server already had. Reloading is not the
    alternative: this code did reload once, and a reload firing while a NetCDF upload
    was in flight tore the request down with the progress bar still saying
    "Processing".
    """

    def panel(self):
        response = self.client.get(
            reverse('build:validation_panel', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def record_findings(self, findings):
        run = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_DONE, phase=ValidationRun.PHASE_DONE,
            products_total=2, products_done=2, findings=findings,
            error_count=len(findings),
            bundle_updated_at=self.bundle.updated_at,
            content_fingerprint=self.bundle.content_fingerprint())
        ValidationRun.objects.filter(pk=run.pk).update(finished_at=timezone.now())
        return run

    def test_the_fetched_panel_shows_the_newest_run(self):
        """The whole point: what comes back is the result, not a prompt to reload."""
        # The citation is what these tests leave outstanding, and ELSA now asks for
        # one whether or not PDS mentioned it. Supplying it here leaves the finding
        # as the only thing that can put this wording on the page, which is what
        # makes its disappearance evidence that the newest run is being shown.
        satisfy_requirements(self.bundle)
        citation = {
            'severity': 'ERROR', 'type': 'error.label.schematron',
            'message': 'In Product_Bundle both Citation_Information and its '
                       'description are required.',
            'label': 'b.xml', 'label_path': '/b.xml', 'line': 1,
            'element_path': 'Product_Bundle/Identification_Area'}
        self.record_findings([citation])
        self.assertContains(self.panel(), 'Add Citation Information')

        ValidationRun.objects.all().delete()
        self.record_findings([])
        response = self.panel()
        self.assertNotContains(response, 'Add Citation Information')

    def test_the_swapped_region_is_self_contained(self):
        """Everything the page replaces comes back in one element."""
        self.record_findings([])
        body = self.panel().content.decode()
        self.assertIn('id="preflight_body"', body)
        self.assertIn('<!-- /preflight_body -->', body)
        region = body.split('id="preflight_body"')[1].split('<!-- /preflight_body -->')[0]
        for element in ('preflight_progress', 'preflight_results', 'preflight_updated'):
            self.assertIn(element, region,
                          '{} is outside the region the page swaps'.format(element))

    def test_the_panel_fetches_instead_of_reloading(self):
        """Reloading aborts an upload in flight; that bug is not coming back.

        Scoped to the panel's own script, because the page reloads elsewhere for
        reasons that have nothing to do with validation.
        """
        with open('templates/build/bundle/bundle.html', encoding='utf-8') as handle:
            text = handle.read()
        start = text.index('Pre-flight check: start a run')
        panel_script = text[start:text.index('</script>', start)]

        self.assertNotIn(
            'window.location.reload', panel_script,
            'the validation panel reloads the page, which aborts an upload in flight')
        self.assertIn('refreshPanel', panel_script,
                      'the panel no longer refreshes itself')

    def test_the_panel_is_told_where_to_fetch_itself_from(self):
        self.record_findings([])
        page = self.client.get(reverse('build:bundle', args=[self.bundle.pk]))
        self.assertContains(
            page, reverse('build:validation_panel', args=[self.bundle.pk]))

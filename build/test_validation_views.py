"""Routes, gating and payload shape for the Phase 1 validation views."""
import json
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from build.models import Bundle, ValidationRun
from build.requirement_fixture import satisfy_requirements


class ValidationViewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', password='pw')
        self.other = User.objects.create_user('other', password='pw')
        self.staff = User.objects.create_user('staffer', password='pw', is_staff=True)
        self.bundle = Bundle.objects.create(
            name='view test bundle', user=self.owner, version='1O00', bundle_type='External')
        # These tests are about how PDS's findings are counted. ELSA's own requirements
        # are counted alongside them now, so the bundle meets those and only the
        # findings below are left to count.
        satisfy_requirements(self.bundle)
        self.run = ValidationRun.objects.create(
            bundle=self.bundle, tier=ValidationRun.TIER_STRUCTURE,
            status=ValidationRun.STATUS_DONE, error_count=3, warning_count=1,
            products_total=2, products_done=2, phase=ValidationRun.PHASE_DONE,
            findings=[{'severity': 'ERROR', 'type': 'error.label.schema',
                       'message': 'demo', 'label': 'a.xml', 'label_path': '/a.xml',
                       'line': 4, 'element_path': 'Product_Bundle/Identification_Area'}])

    def test_status_returns_state_for_the_owner(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(reverse('build:validation_status', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(payload['status'], ValidationRun.STATUS_DONE)
        self.assertEqual(payload['errors'], 3)
        self.assertTrue(payload['finished'])
        self.assertIn('percent', payload)
        self.assertIn('stale', payload)

    def test_status_carries_the_translated_count_not_just_the_raw_one(self):
        """The Bundle Components badge is rendered from the translated count.

        If the poll only carried error_count, the badge would jump the moment a
        check finished: on a real AMA bundle from 3 to fix to 43 to fix, for a
        bundle nothing had touched.
        """
        self.run.findings = [
            {'severity': 'ERROR', 'type': 'error.label.schematron',
             'message': 'In Product_Bundle both Citation_Information and its '
                        'description are required.',
             'label': 'a.xml', 'label_path': '/a.xml', 'line': 1,
             'element_path': 'Product_Bundle/Identification_Area'},
            # An ELSA defect: counted as an error by PDS, never shown to the user.
            {'severity': 'ERROR', 'type': 'error.label.schema',
             'message': "cvc-minLength-valid: Value '' with length = '0' is not facet-valid.",
             'label': 'a.xml', 'label_path': '/a.xml', 'line': 9,
             'element_path': 'Product_Bundle/Context_Area/Time_Coordinates/start_date_time'},
        ]
        self.run.error_count = 2
        self.run.save()

        self.client.login(username='owner', password='pw')
        payload = json.loads(self.client.get(
            reverse('build:validation_status', args=[self.bundle.pk])).content)
        self.assertEqual(payload['errors'], 2)
        self.assertEqual(payload['blocking'], 1)

    def test_status_reports_no_blocking_work_on_a_clean_run(self):
        self.run.findings = []
        self.run.error_count = 0
        self.run.save()
        self.client.login(username='owner', password='pw')
        payload = json.loads(self.client.get(
            reverse('build:validation_status', args=[self.bundle.pk])).content)
        self.assertEqual(payload['blocking'], 0)

    def test_status_reports_none_when_nothing_has_run(self):
        empty = Bundle.objects.create(name='never validated', user=self.owner, version='1O00')
        self.client.login(username='owner', password='pw')
        payload = json.loads(self.client.get(
            reverse('build:validation_status', args=[empty.pk])).content)
        self.assertEqual(payload['status'], 'none')

    def test_another_user_cannot_read_status(self):
        self.client.login(username='other', password='pw')
        response = self.client.get(reverse('build:validation_status', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 302)

    def test_start_requires_post(self):
        self.client.login(username='owner', password='pw')
        response = self.client.get(reverse('build:start_validation', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 405)

    def test_another_user_cannot_start_a_run(self):
        self.client.login(username='other', password='pw')
        response = self.client.post(reverse('build:start_validation', args=[self.bundle.pk]))
        self.assertEqual(response.status_code, 302)

    def test_report_is_staff_only(self):
        self.client.login(username='owner', password='pw')
        self.assertEqual(
            self.client.get(reverse('build:validation_report', args=[self.run.pk])).status_code,
            302)

    def test_staff_can_read_the_report(self):
        self.client.login(username='staffer', password='pw')
        response = self.client.get(reverse('build:validation_report', args=[self.run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Identification_Area')

    def test_runs_list_is_staff_only(self):
        self.client.login(username='owner', password='pw')
        self.assertEqual(
            self.client.get(reverse('build:validation_runs')).status_code, 302)

    def test_staff_can_read_the_runs_list(self):
        self.client.login(username='staffer', password='pw')
        response = self.client.get(reverse('build:validation_runs'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'view test bundle')

    def test_anonymous_is_redirected_to_login(self):
        for name, args in [('build:validation_runs', []),
                           ('build:validation_status', [self.bundle.pk])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 302)

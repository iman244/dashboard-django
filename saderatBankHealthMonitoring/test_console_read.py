from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

from .models import MonitoringType, PatientEntry, PatientIdentity, SaderatBankHealthMonitoring

API = '/api/saderat-bank-health-monitoring/'


class ConsoleReadAccessTests(APITestCase):
    """Any signed-in console user reads; staff write; patients only use /me/."""

    @classmethod
    def setUpTestData(cls):
        step_2 = MonitoringType.objects.get(slug='step_2')
        cls.upload = SaderatBankHealthMonitoring.objects.create(
            name='Mehr', type=step_2, json=[{'کد ملی': '0012345678'}])
        cls.entry = PatientEntry.objects.create(
            monitoring=step_2, national_id='0012345678', values={})
        User = get_user_model()
        cls.viewer = User.objects.create_user('viewer', password='pw')
        cls.patient = User.objects.create_user('patient', password='pw')
        PatientIdentity.objects.create(user=cls.patient, national_id='0012345678')

    def setUp(self):
        cache.clear()

    def reads(self):
        return [
            'monitoring-types/',
            f'monitoring-types/{self.upload.type_id}/',
            'monitorings/',
            f'monitorings/{self.upload.id}/',
            'patient-entries/',
            f'patient-entries/{self.entry.id}/',
            'patient-records/?national_id=0012345678',
            'person-reports/?national_id=0012345678',
        ]

    def test_viewer_reads_every_console_endpoint(self):
        self.client.force_authenticate(self.viewer)
        for endpoint in self.reads():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(API + endpoint).status_code, 200)

    def test_viewer_cannot_write(self):
        self.client.force_authenticate(self.viewer)
        writes = [
            ('post', 'monitoring-types/', {'slug': 'x', 'name_en': 'X', 'name_fa': 'X'}),
            ('patch', f'monitoring-types/{self.upload.type_id}/', {'name_en': 'Y'}),
            ('delete', f'monitorings/{self.upload.id}/', None),
            ('post', 'monitorings/upload_excel/', {}),
            ('post', 'patient-entries/', {}),
            ('patch', f'patient-entries/{self.entry.id}/', {'values': {}}),
            ('delete', f'patient-entries/{self.entry.id}/', None),
            ('post', 'patient-entries/presign/', {}),
        ]
        for method, endpoint, body in writes:
            with self.subTest(method=method, endpoint=endpoint):
                response = getattr(self.client, method)(API + endpoint, body, format='json')
                self.assertEqual(response.status_code, 403)

    def test_patient_account_is_refused_everywhere_but_me(self):
        self.client.force_authenticate(self.patient)
        for endpoint in self.reads():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(API + endpoint).status_code, 403)
        self.assertEqual(self.client.get(API + 'patient-records/me/').status_code, 200)

    def test_anonymous_is_unauthorized(self):
        for endpoint in self.reads():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(API + endpoint).status_code, 401)


class CampaignCountTests(APITestCase):
    def test_types_carry_upload_and_record_counts(self):
        step_2 = MonitoringType.objects.get(slug='step_2')
        SaderatBankHealthMonitoring.objects.create(name='A', type=step_2, json=[])
        SaderatBankHealthMonitoring.objects.create(name='B', type=step_2, json=[])
        PatientEntry.objects.create(monitoring=step_2, national_id='0012345678', values={})
        viewer = get_user_model().objects.create_user('v2', password='pw')
        self.client.force_authenticate(viewer)
        rows = {r['slug']: r for r in self.client.get(API + 'monitoring-types/').data}
        self.assertEqual(rows['step_2']['upload_count'], 2)
        self.assertEqual(rows['step_2']['record_count'], 1)
        self.assertEqual(rows['step_1']['upload_count'], 0)
        one = self.client.get(API + f'monitoring-types/{step_2.id}/').data
        self.assertEqual(one['upload_count'], 2)

    def test_nested_monitoring_type_omits_campaign_counts(self):
        step_2 = MonitoringType.objects.get(slug='step_2')
        PatientEntry.objects.create(
            monitoring=step_2, national_id='0012345678', values={})
        viewer = get_user_model().objects.create_user('v3', password='pw')
        self.client.force_authenticate(viewer)
        records = self.client.get(
            API + 'patient-records/?national_id=0012345678').data
        self.assertNotIn('upload_count', records[0]['monitoring'])
        self.assertNotIn('record_count', records[0]['monitoring'])


class PatientEntryNationalIdFilterTests(APITestCase):
    """The ?national_id= filter finds an entry however the id was typed."""

    @classmethod
    def setUpTestData(cls):
        step_2 = MonitoringType.objects.get(slug='step_2')
        cls.entry = PatientEntry.objects.create(
            monitoring=step_2, national_id='0850157269', values={})
        cls.viewer = get_user_model().objects.create_user('viewer', password='pw')

    def test_lost_leading_zero_and_persian_digits_still_find_the_entry(self):
        self.client.force_authenticate(self.viewer)
        for typed in ('0850157269', '850157269', '۰۸۵۰۱۵۷۲۶۹'):
            with self.subTest(typed=typed):
                response = self.client.get(API + 'patient-entries/', {'national_id': typed})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row['id'] for row in response.json()], [self.entry.id])

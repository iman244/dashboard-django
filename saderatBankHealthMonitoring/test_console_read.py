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

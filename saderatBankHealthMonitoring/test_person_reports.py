from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MonitoringType, PatientIdentity, SaderatBankHealthMonitoring
from .national_id import canonical_national_id
from .tests import excel_upload

URL = 'person-reports'


class CanonicalNationalIdTests(TestCase):
    def test_restores_zeros_lost_to_a_numeric_cell(self):
        self.assertEqual(canonical_national_id(12345678), '0012345678')
        self.assertEqual(canonical_national_id(12345678.0), '0012345678')
        self.assertEqual(canonical_national_id('12345678'), '0012345678')

    def test_folds_persian_digits_and_trims(self):
        self.assertEqual(canonical_national_id(' ۰۰۱۲۳۴۵۶۷۸ '), '0012345678')

    def test_leaves_anything_else_alone(self):
        self.assertIsNone(canonical_national_id(None))
        self.assertEqual(canonical_national_id('abc'), 'abc')
        self.assertEqual(canonical_national_id('1234567'), '1234567')


class PersonReportsApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        step_1 = MonitoringType.objects.get(slug='step_1')
        step_2 = MonitoringType.objects.get(slug='step_2')
        cls.first = SaderatBankHealthMonitoring.objects.create(
            name='Farvardin', type=step_1,
            json=[{'personel.کد ملی': '0012345678'},
                  {'personel.کد ملی': '0099999999'}])
        cls.duplicated = SaderatBankHealthMonitoring.objects.create(
            name='Mehr', type=step_2,
            json=[{'کد ملی': '0012345678'}, {'کد ملی': '0012345678'}])
        # Uploaded before parsing kept the column as text: stored as a number.
        cls.legacy = SaderatBankHealthMonitoring.objects.create(
            name='Legacy', type=step_2, json=[{'کد ملی': 12345678}])
        SaderatBankHealthMonitoring.objects.create(
            name='Someone else', type=step_2, json=[{'کد ملی': '0099999999'}])
        SaderatBankHealthMonitoring.objects.create(
            name='Empty', type=step_2, json=[])
        User = get_user_model()
        cls.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        cls.viewer = User.objects.create_user('viewer', password='pw')
        cls.patient = User.objects.create_user('patient', password='pw', is_staff=True)
        PatientIdentity.objects.create(user=cls.patient, national_id='0012345678')

    def setUp(self):
        cache.clear()

    def get(self, national_id='0012345678'):
        return self.client.get(reverse(URL), {'national_id': national_id})

    def test_finds_every_upload_that_mentions_the_person(self):
        self.client.force_authenticate(self.staff)
        response = self.get()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_id = {row['id']: row for row in response.data}
        self.assertEqual(
            set(by_id), {self.first.id, self.duplicated.id, self.legacy.id})
        self.assertEqual(by_id[self.duplicated.id]['match_count'], 2)
        self.assertEqual(by_id[self.first.id]['type'], 'step_1')
        self.assertEqual(
            set(by_id[self.first.id]),
            {'id', 'name', 'type', 'monitoring', 'created_at', 'match_count'})

    def test_newest_upload_first(self):
        self.client.force_authenticate(self.staff)
        ids = [row['id'] for row in self.get().data]
        self.assertEqual(ids, [self.legacy.id, self.duplicated.id, self.first.id])

    def test_accepts_persian_digits(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(len(self.get('۰۰۱۲۳۴۵۶۷۸').data), 3)

    def test_rejects_a_malformed_id(self):
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.get('12').status_code, status.HTTP_400_BAD_REQUEST)

    def test_console_users_read_patients_do_not(self):
        self.assertEqual(self.get().status_code, status.HTTP_401_UNAUTHORIZED)
        self.client.force_authenticate(self.viewer)
        self.assertEqual(self.get().status_code, status.HTTP_200_OK)
        self.client.force_authenticate(self.patient)
        self.assertEqual(self.get().status_code, status.HTTP_403_FORBIDDEN)

    def test_items_name_their_campaign(self):
        self.client.force_authenticate(self.staff)
        item = next(r for r in self.get().data if r['id'] == self.first.id)
        self.assertEqual(item['monitoring']['slug'], 'step_1')
        self.assertEqual(set(item['monitoring']), {'id', 'slug', 'name_en', 'name_fa'})
        self.assertNotIn('rows', item)

    def test_one_campaign_with_the_persons_rows_only(self):
        self.client.force_authenticate(self.staff)
        step_2 = MonitoringType.objects.get(slug='step_2')
        response = self.client.get(reverse(URL), {'national_id': '0012345678', 'monitoring': step_2.id})
        self.assertEqual(response.status_code, 200)
        by_id = {r['id']: r for r in response.data}
        self.assertEqual(set(by_id), {self.duplicated.id, self.legacy.id})
        self.assertEqual(len(by_id[self.duplicated.id]['rows']), 2)
        self.assertEqual(by_id[self.legacy.id]['rows'], [{'کد ملی': 12345678}])

    def test_rejects_a_non_numeric_monitoring(self):
        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse(URL), {'national_id': '0012345678', 'monitoring': 'x'})
        self.assertEqual(response.status_code, 400)


class UploadKeepsNationalIdZerosTests(APITestCase):
    def test_numeric_national_id_cells_are_stored_as_ten_digit_text(self):
        staff = get_user_model().objects.create_user('op', password='pw', is_staff=True)
        self.client.force_authenticate(staff)
        response = self.client.post(
            reverse('monitorings-upload-excel'),
            {'name': 'Aban', 'type': 'step_2',
             'file': excel_upload([{'کد ملی': 12345678, 'نام': 'Ali'}])},
            format='multipart')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        stored = SaderatBankHealthMonitoring.objects.get(name='Aban').json
        self.assertEqual(stored[0]['کد ملی'], '0012345678')

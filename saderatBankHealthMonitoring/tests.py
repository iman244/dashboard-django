import io

import pandas as pd
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import MonitoringType, SaderatBankHealthMonitoring


def excel_upload(rows):
    """An .xlsx file object of `rows`, as the upload endpoint receives one."""
    buffer = io.BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False)
    buffer.seek(0)
    buffer.name = 'report.xlsx'
    return buffer


class MonitoringTypeApiTests(APITestCase):
    """CRUD over the types themselves."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(
            'staff', 'staff@example.com', 'pw', is_staff=True)
        cls.member = User.objects.create_user(
            'member', 'member@example.com', 'pw')
        cls.step_1 = MonitoringType.objects.get(slug='step_1')

    def test_anonymous_cannot_read(self):
        response = self.client.get(reverse('monitoring-types-list'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_member_reads_types(self):
        self.client.force_authenticate(self.member)
        response = self.client.get(reverse('monitoring-types-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data[0],
            {'id': self.step_1.id, 'slug': 'step_1',
             'name_en': 'Step 1', 'name_fa': 'مرحله ۱'},
        )

    def test_member_cannot_write(self):
        self.client.force_authenticate(self.member)
        response = self.client.post(
            reverse('monitoring-types-list'),
            {'slug': 'step_3', 'name_en': 'Step 3', 'name_fa': 'مرحله ۳'},
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_creates_type(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            reverse('monitoring-types-list'),
            {'slug': 'step_3', 'name_en': 'Step 3', 'name_fa': 'مرحله ۳'},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(MonitoringType.objects.filter(slug='step_3').exists())

    def test_duplicate_slug_rejected(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            reverse('monitoring-types-list'),
            {'slug': 'step_1', 'name_en': 'Other', 'name_fa': 'دیگر'},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_put_renames_slug_and_reports_follow(self):
        """The reason the model has an integer key rather than a slug key."""
        monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=self.step_1, json=[])

        self.client.force_authenticate(self.staff)
        response = self.client.put(
            reverse('monitoring-types-detail', args=[self.step_1.id]),
            {'slug': 'step_one', 'name_en': 'Step One', 'name_fa': 'مرحله یک'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        monitoring.refresh_from_db()
        self.assertEqual(monitoring.type_id, self.step_1.id)
        self.assertEqual(monitoring.type.slug, 'step_one')

    def test_delete_unused_type(self):
        self.client.force_authenticate(self.staff)
        response = self.client.delete(
            reverse('monitoring-types-detail', args=[self.step_1.id]))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            MonitoringType.objects.filter(slug='step_1').exists())

    def test_delete_type_in_use_conflicts(self):
        SaderatBankHealthMonitoring.objects.create(
            name='March', type=self.step_1, json=[])

        self.client.force_authenticate(self.staff)
        response = self.client.delete(
            reverse('monitoring-types-detail', args=[self.step_1.id]))

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data['monitorings'], 1)
        self.assertTrue(MonitoringType.objects.filter(pk=self.step_1.pk).exists())


class MonitoringWireFormatTests(APITestCase):
    """`type` must stay the slug string the Next.js dashboard branches on."""

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.member = User.objects.create_user(
            'member', 'member@example.com', 'pw')
        cls.step_1 = MonitoringType.objects.get(slug='step_1')
        cls.step_2 = MonitoringType.objects.get(slug='step_2')
        cls.monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=cls.step_2, json=[{'a': 1}])

    def setUp(self):
        self.client.force_authenticate(self.member)

    def test_list_serializes_type_as_slug(self):
        response = self.client.get(reverse('monitorings-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]['type'], 'step_2')

    def test_retrieve_serializes_type_as_slug(self):
        response = self.client.get(
            reverse('monitorings-detail', args=[self.monitoring.id]))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['type'], 'step_2')

    def test_upload_excel_accepts_slug(self):
        response = self.client.post(
            reverse('monitorings-upload-excel'),
            {'name': 'April', 'type': 'step_1',
             'file': excel_upload([{'col': 'value'}])},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        uploaded = SaderatBankHealthMonitoring.objects.get(name='April')
        self.assertEqual(uploaded.type, self.step_1)
        self.assertEqual(uploaded.json, [{'col': 'value'}])

    def test_upload_excel_rejects_unknown_slug(self):
        response = self.client.post(
            reverse('monitorings-upload-excel'),
            {'name': 'April', 'type': 'step_9',
             'file': excel_upload([{'col': 'value'}])},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('type', response.data)
        self.assertFalse(
            SaderatBankHealthMonitoring.objects.filter(name='April').exists())

    def test_upload_excel_rejects_duplicate_name_and_type(self):
        response = self.client.post(
            reverse('monitorings-upload-excel'),
            {'name': 'March', 'type': 'step_2',
             'file': excel_upload([{'col': 'value'}])},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_same_name_under_another_type_is_allowed(self):
        response = self.client.post(
            reverse('monitorings-upload-excel'),
            {'name': 'March', 'type': 'step_1',
             'file': excel_upload([{'col': 'value'}])},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class MigrationSeedTests(APITestCase):
    """Migration 0004 must leave the two original steps in place."""

    def test_seeds_the_two_steps(self):
        self.assertEqual(
            list(MonitoringType.objects.values_list(
                'slug', 'name_en', 'name_fa')),
            [('step_1', 'Step 1', 'مرحله ۱'),
             ('step_2', 'Step 2', 'مرحله ۲')],
        )


class MonitoringTypeModelTests(APITestCase):
    def test_str_uses_english_name(self):
        step_1 = MonitoringType.objects.get(slug='step_1')
        monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=step_1, json=[])
        self.assertEqual(str(step_1), 'Step 1')
        self.assertEqual(str(monitoring), 'March (Step 1)')

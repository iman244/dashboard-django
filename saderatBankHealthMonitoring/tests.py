import io
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from django.db.utils import IntegrityError

from .models import (
    MonitoringType,
    PatientEntry,
    PatientEntryFile,
    SaderatBankHealthMonitoring,
)
from .national_id import normalize_national_id
from .s3 import S3Unavailable, build_key
from .schema import (
    file_fields,
    find_field,
    mime_matches,
    validate_field_schema,
)


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
             'name_en': 'Step 1', 'name_fa': 'مرحله ۱',
             'field_schema': {}},
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


def file_field(key='mri_image', **overrides):
    """A minimal valid file field, with overrides merged in."""
    field = {
        'key': key,
        'type': 'file',
        'label_en': 'MRI Image',
        'label_fa': 'تصویر ام‌آر‌آی',
        'required': False,
        'multiple': True,
        'accept': ['image/jpeg'],
        'max_size_mb': 50,
        'max_count': 10,
    }
    field.update(overrides)
    return field


def schema(*fields):
    return {'version': 1, 'fields': list(fields)}


class FieldSchemaValidationTests(TestCase):
    """The contract, enforced. No database involved."""

    def assertRejects(self, document, fragment):
        with self.assertRaises(DjangoValidationError) as caught:
            validate_field_schema(document)
        self.assertIn(fragment, str(caught.exception))

    def test_empty_schema_is_valid(self):
        self.assertIsNone(validate_field_schema({}))

    def test_minimal_file_field_is_valid(self):
        self.assertIsNone(validate_field_schema(schema(file_field())))

    def test_rejects_non_dict(self):
        self.assertRejects([], 'object')

    def test_rejects_unknown_version(self):
        self.assertRejects({'version': 2, 'fields': []}, 'version')

    def test_rejects_fields_not_a_list(self):
        self.assertRejects({'version': 1, 'fields': {}}, 'list')

    def test_rejects_unsupported_type(self):
        self.assertRejects(schema(file_field(type='string')), 'file')

    def test_rejects_reserved_key(self):
        self.assertRejects(schema(file_field(key='national_id')), 'reserved')

    def test_rejects_malformed_key(self):
        self.assertRejects(schema(file_field(key='MRI Image')), 'key')
        self.assertRejects(schema(file_field(key='9lives')), 'key')
        self.assertRejects(schema(file_field(key='has.dot')), 'key')

    def test_rejects_duplicate_keys(self):
        self.assertRejects(schema(file_field(), file_field()), 'duplicate')

    def test_requires_both_labels(self):
        self.assertRejects(schema(file_field(label_fa='')), 'label_fa')
        field = file_field()
        del field['label_en']
        self.assertRejects(schema(field), 'label_en')

    def test_rejects_non_positive_max_size(self):
        self.assertRejects(schema(file_field(max_size_mb=0)), 'max_size_mb')

    def test_rejects_non_positive_max_count(self):
        self.assertRejects(schema(file_field(max_count=0)), 'max_count')

    def test_rejects_empty_accept(self):
        self.assertRejects(schema(file_field(accept=[])), 'accept')

    def test_file_fields_returns_declarations(self):
        document = schema(file_field('mri_image'), file_field('xms'))
        self.assertEqual(
            [f['key'] for f in file_fields(document)],
            ['mri_image', 'xms'],
        )

    def test_file_fields_of_empty_schema(self):
        self.assertEqual(file_fields({}), [])

    def test_find_field_hits_and_misses(self):
        document = schema(file_field('mri_image'))
        self.assertEqual(find_field(document, 'mri_image')['key'], 'mri_image')
        self.assertIsNone(find_field(document, 'absent'))


class NationalIdNormalizationTests(TestCase):
    """Identity keys must fold to one spelling before they reach the database."""

    def test_ascii_passes_through(self):
        self.assertEqual(normalize_national_id('0012345678'), '0012345678')

    def test_persian_digits_fold(self):
        self.assertEqual(normalize_national_id('۰۰۱۲۳۴۵۶۷۸'), '0012345678')

    def test_arabic_indic_digits_fold(self):
        self.assertEqual(normalize_national_id('٠٠١٢٣٤٥٦٧٨'), '0012345678')

    def test_mixed_digits_fold(self):
        self.assertEqual(normalize_national_id('۰۰12٣٤5678'), '0012345678')

    def test_surrounding_whitespace_is_stripped(self):
        self.assertEqual(normalize_national_id('  0012345678 '), '0012345678')

    def test_empty_stays_empty(self):
        self.assertEqual(normalize_national_id(''), '')

    def test_non_string_is_returned_unchanged(self):
        self.assertIsNone(normalize_national_id(None))


class PatientEntryModelTests(APITestCase):
    """Identity, cascade and the schema column."""

    @classmethod
    def setUpTestData(cls):
        cls.step_1 = MonitoringType.objects.get(slug='step_1')
        cls.monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=cls.step_1, json=[])

    def test_field_schema_defaults_to_empty_dict(self):
        self.assertEqual(self.step_1.field_schema, {})

    def test_field_schema_rejects_invalid_document(self):
        self.step_1.field_schema = {'version': 1, 'fields': [{'key': 'X'}]}
        with self.assertRaises(DjangoValidationError):
            self.step_1.full_clean()

    def test_entry_national_id_is_normalized_on_save(self):
        entry = PatientEntry.objects.create(
            monitoring=self.monitoring, national_id='۰۰۱۲۳۴۵۶۷۸')
        entry.refresh_from_db()
        self.assertEqual(entry.national_id, '0012345678')

    def test_duplicate_national_id_in_one_monitoring_is_rejected(self):
        PatientEntry.objects.create(
            monitoring=self.monitoring, national_id='0012345678')
        with self.assertRaises(IntegrityError):
            PatientEntry.objects.create(
                monitoring=self.monitoring, national_id='0012345678')

    def test_same_national_id_under_another_monitoring_is_allowed(self):
        other = SaderatBankHealthMonitoring.objects.create(
            name='April', type=self.step_1, json=[])
        PatientEntry.objects.create(
            monitoring=self.monitoring, national_id='0012345678')
        PatientEntry.objects.create(
            monitoring=other, national_id='0012345678')
        self.assertEqual(PatientEntry.objects.count(), 2)

    def test_deleting_a_monitoring_takes_its_entries(self):
        entry = PatientEntry.objects.create(
            monitoring=self.monitoring, national_id='0012345678')
        PatientEntryFile.objects.create(
            entry=entry, field_key='mri_image', key='entries/1/x.jpg',
            original_name='x.jpg', content_type='image/jpeg', size=10)
        self.monitoring.delete()
        self.assertEqual(PatientEntry.objects.count(), 0)
        self.assertEqual(PatientEntryFile.objects.count(), 0)

    def test_excel_upload_is_unaffected_by_a_schema(self):
        """The load-bearing constraint: a schema never gates Excel."""
        self.step_1.field_schema = {
            'version': 1,
            'fields': [{
                'key': 'mri_image', 'type': 'file',
                'label_en': 'MRI', 'label_fa': 'ام‌آر‌آی',
            }],
        }
        self.step_1.save()
        User = get_user_model()
        user = User.objects.create_user('op', 'op@example.com', 'pw')
        self.client.force_authenticate(user)
        response = self.client.post(
            reverse('monitorings-upload-excel'),
            {'name': 'Unrelated', 'type': 'step_1',
             'file': excel_upload([{'a': 1}])},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class S3KeyTests(TestCase):
    def test_key_layout(self):
        key = build_key(7, '0012345678', 'mri_image', 'scan.JPG')
        self.assertTrue(key.startswith('entries/7/0012345678/mri_image/'))
        self.assertTrue(key.endswith('.jpg'))

    def test_key_is_unique_per_call(self):
        first = build_key(7, '0012345678', 'mri_image', 'scan.jpg')
        second = build_key(7, '0012345678', 'mri_image', 'scan.jpg')
        self.assertNotEqual(first, second)

    def test_national_id_is_folded_into_the_key(self):
        key = build_key(7, '۰۰۱۲۳۴۵۶۷۸', 'mri_image', 'scan.jpg')
        self.assertIn('/0012345678/', key)

    def test_extensionless_filename_is_allowed(self):
        key = build_key(7, '0012345678', 'xms', 'rawdata')
        self.assertIn('/xms/', key)


class MimeMatchTests(TestCase):
    def test_exact_match(self):
        self.assertTrue(mime_matches(['image/jpeg'], 'image/jpeg'))

    def test_subtype_wildcard(self):
        self.assertTrue(mime_matches(['image/*'], 'image/png'))
        self.assertFalse(mime_matches(['image/*'], 'application/pdf'))

    def test_full_wildcard(self):
        self.assertTrue(mime_matches(['*/*'], 'application/octet-stream'))

    def test_no_match(self):
        self.assertFalse(mime_matches(['image/jpeg'], 'image/png'))

    def test_case_is_ignored(self):
        self.assertTrue(mime_matches(['image/JPEG'], 'Image/jpeg'))


class PresignTests(APITestCase):
    """Only uploads the schema permits get a signature."""

    @classmethod
    def setUpTestData(cls):
        cls.type = MonitoringType.objects.create(
            slug='imaging', name_en='Imaging', name_fa='تصویربرداری',
            field_schema={
                'version': 1,
                'fields': [{
                    'key': 'mri_image', 'type': 'file',
                    'label_en': 'MRI Image', 'label_fa': 'تصویر ام‌آر‌آی',
                    'required': True, 'multiple': True,
                    'accept': ['image/jpeg', 'image/png'],
                    'max_size_mb': 5, 'max_count': 3,
                }],
            },
        )
        cls.monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=cls.type, json=[])
        User = get_user_model()
        cls.user = User.objects.create_user('op', 'op@example.com', 'pw')

    def setUp(self):
        self.client.force_authenticate(self.user)
        patcher = mock.patch(
            'saderatBankHealthMonitoring.views.presign_put',
            return_value={'url': 'https://example.invalid/signed',
                          'headers': {'Content-Type': 'image/jpeg'},
                          'expires_in': 900})
        self.presign_put = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, **overrides):
        payload = {
            'monitoring': self.monitoring.id,
            'national_id': '0012345678',
            'field_key': 'mri_image',
            'filename': 'scan.jpg',
            'content_type': 'image/jpeg',
            'size': 1024,
        }
        payload.update(overrides)
        return self.client.post(
            reverse('patient-entries-presign'), payload, format='json')

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.post().status_code,
                         status.HTTP_401_UNAUTHORIZED)

    def test_valid_request_is_signed(self):
        response = self.post()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['upload_url'],
                         'https://example.invalid/signed')
        self.assertIn('entries/', response.data['key'])

    def test_unknown_field_key_is_refused(self):
        response = self.post(field_key='nope')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.presign_put.assert_not_called()

    def test_disallowed_mime_is_refused(self):
        response = self.post(content_type='application/pdf')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.presign_put.assert_not_called()

    def test_oversize_is_refused(self):
        response = self.post(size=6 * 1024 * 1024)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.presign_put.assert_not_called()

    def test_persian_national_id_is_folded_into_the_key(self):
        response = self.post(national_id='۰۰۱۲۳۴۵۶۷۸')
        self.assertIn('/0012345678/', response.data['key'])

    def test_storage_failure_reports_503(self):
        self.presign_put.side_effect = S3Unavailable('down')
        self.assertEqual(self.post().status_code,
                         status.HTTP_503_SERVICE_UNAVAILABLE)


class PatientEntryApiTests(APITestCase):
    """Entries are committed against what the bucket actually holds."""

    @classmethod
    def setUpTestData(cls):
        cls.type = MonitoringType.objects.create(
            slug='imaging2', name_en='Imaging', name_fa='تصویربرداری',
            field_schema={
                'version': 1,
                'fields': [{
                    'key': 'mri_image', 'type': 'file',
                    'label_en': 'MRI Image', 'label_fa': 'تصویر ام‌آر‌آی',
                    'required': True, 'multiple': True,
                    'accept': ['image/jpeg'], 'max_size_mb': 5,
                    'max_count': 2,
                }],
            },
        )
        cls.monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=cls.type, json=[])
        User = get_user_model()
        cls.user = User.objects.create_user('op2', 'op2@example.com', 'pw')

    def setUp(self):
        self.client.force_authenticate(self.user)
        head = mock.patch(
            'saderatBankHealthMonitoring.serializers.head_object',
            return_value={'size': 1024, 'content_type': 'image/jpeg'})
        self.head_object = head.start()
        self.addCleanup(head.stop)
        get = mock.patch(
            'saderatBankHealthMonitoring.serializers.presign_get',
            return_value='https://example.invalid/read')
        get.start()
        self.addCleanup(get.stop)

    def file_payload(self, key='entries/1/0012345678/mri_image/abc.jpg'):
        return {'field_key': 'mri_image', 'key': key,
                'original_name': 'scan.jpg', 'content_type': 'image/jpeg',
                'size': 1024}

    def create(self, **overrides):
        payload = {
            'monitoring': self.monitoring.id,
            'national_id': '0012345678',
            'files': [self.file_payload()],
        }
        payload.update(overrides)
        return self.client.post(
            reverse('patient-entries-list'), payload, format='json')

    def test_anonymous_is_refused(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.create().status_code,
                         status.HTTP_401_UNAUTHORIZED)

    def test_creates_entry_with_files(self):
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(response.data['files']), 1)
        self.assertEqual(response.data['files'][0]['url'],
                         'https://example.invalid/read')

    def test_missing_object_is_refused(self):
        self.head_object.return_value = None
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PatientEntry.objects.count(), 0)

    def test_declared_size_must_match_the_bucket(self):
        """A client's claim about its own upload is never trusted."""
        self.head_object.return_value = {'size': 999999,
                                         'content_type': 'image/jpeg'}
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PatientEntry.objects.count(), 0)

    def test_unknown_field_key_is_refused(self):
        response = self.create(
            files=[dict(self.file_payload(), field_key='nope')])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_max_count_is_enforced(self):
        response = self.create(files=[
            self.file_payload('entries/1/0012345678/mri_image/a.jpg'),
            self.file_payload('entries/1/0012345678/mri_image/b.jpg'),
            self.file_payload('entries/1/0012345678/mri_image/c.jpg'),
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_national_id_conflicts(self):
        self.create()
        response = self.create(
            files=[self.file_payload('entries/1/0012345678/mri_image/d.jpg')])
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('id', response.data)

    def test_persian_national_id_conflicts_with_its_ascii_twin(self):
        """The whole reason normalization is server-side."""
        self.create()
        response = self.create(
            national_id='۰۰۱۲۳۴۵۶۷۸',
            files=[self.file_payload('entries/1/0012345678/mri_image/e.jpg')])
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_filter_by_monitoring_and_national_id(self):
        self.create()
        response = self.client.get(
            reverse('patient-entries-list'),
            {'monitoring': self.monitoring.id, 'national_id': '۰۰۱۲۳۴۵۶۷۸'})
        self.assertEqual(len(response.data), 1)

    def test_patch_replaces_the_file_set(self):
        entry_id = self.create().data['id']
        response = self.client.patch(
            reverse('patient-entries-detail', args=[entry_id]),
            {'files': [
                self.file_payload('entries/1/0012345678/mri_image/new.jpg')]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PatientEntryFile.objects.count(), 1)
        self.assertEqual(response.data['files'][0]['original_name'],
                         'scan.jpg')

    def test_delete_removes_entry_and_files(self):
        entry_id = self.create().data['id']
        self.client.delete(reverse('patient-entries-detail', args=[entry_id]))
        self.assertEqual(PatientEntry.objects.count(), 0)
        self.assertEqual(PatientEntryFile.objects.count(), 0)

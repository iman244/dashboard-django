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
    digit_string_fields,
    find_field,
    image_fields,
    is_multiple,
    mime_matches,
    validate_field_schema,
    validate_values,
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


def image_field(key='mri_image', **overrides):
    """A minimal valid image field, with overrides merged in."""
    field = {
        'key': key,
        'type': 'image',
        'label_en': 'MRI Image',
        'label_fa': 'تصویر ام‌آر‌آی',
        'required': False,
        'max_size_mb': 50,
        'max_count': 10,
    }
    field.update(overrides)
    return field


def digit_field(key='blood_pressure', **overrides):
    """A minimal valid digit_string field, with overrides merged in."""
    field = {
        'key': key,
        'type': 'digit_string',
        'label_en': 'Blood Pressure',
        'label_fa': 'فشار خون',
        'required': False,
    }
    field.update(overrides)
    return field


def schema(*fields, sections=None):
    document = {'version': 1, 'fields': list(fields)}
    if sections is not None:
        document['sections'] = sections
    return document


class FieldSchemaValidationTests(TestCase):
    """The contract, enforced. No database involved."""

    def assertRejects(self, document, fragment):
        with self.assertRaises(DjangoValidationError) as caught:
            validate_field_schema(document)
        self.assertIn(fragment, str(caught.exception))

    def test_empty_schema_is_valid(self):
        self.assertIsNone(validate_field_schema({}))

    def test_both_field_types_are_valid(self):
        self.assertIsNone(
            validate_field_schema(schema(image_field(), digit_field())))

    def test_rejects_non_dict(self):
        self.assertRejects([], 'object')

    def test_rejects_unknown_version(self):
        self.assertRejects({'version': 2, 'fields': []}, 'version')

    def test_rejects_fields_not_a_list(self):
        self.assertRejects({'version': 1, 'fields': {}}, 'list')

    def test_rejects_unsupported_type(self):
        self.assertRejects(schema(image_field(type='file')), 'not supported')
        self.assertRejects(schema(image_field(type='number')), 'not supported')

    def test_rejects_reserved_key(self):
        self.assertRejects(schema(image_field(key='national_id')), 'reserved')

    def test_rejects_malformed_key(self):
        self.assertRejects(schema(image_field(key='MRI Image')), 'key')
        self.assertRejects(schema(image_field(key='9lives')), 'key')
        self.assertRejects(schema(image_field(key='has.dot')), 'key')

    def test_rejects_duplicate_keys(self):
        self.assertRejects(
            schema(image_field(), image_field()), 'duplicate')

    def test_requires_both_labels(self):
        self.assertRejects(schema(image_field(label_fa='')), 'label_fa')
        field = image_field()
        del field['label_en']
        self.assertRejects(schema(field), 'label_en')

    def test_rejects_non_positive_max_size(self):
        self.assertRejects(schema(image_field(max_size_mb=0)), 'max_size_mb')

    def test_rejects_max_count_above_one_when_single(self):
        self.assertRejects(
            schema(image_field(multiple=False, max_count=3)), 'max_count')

    def test_images_default_to_multiple(self):
        self.assertTrue(is_multiple(image_field()))
        self.assertFalse(is_multiple(image_field(multiple=False)))

    def test_digit_strings_are_not_multiple(self):
        self.assertFalse(is_multiple(digit_field()))

    def test_rejects_min_length_above_max_length(self):
        self.assertRejects(
            schema(digit_field(min_length=10, max_length=4)), 'min_length')

    def test_accepts_length_bounds(self):
        self.assertIsNone(validate_field_schema(
            schema(digit_field(min_length=10, max_length=10))))

    def test_sections_must_be_a_list(self):
        document = schema(image_field())
        document['sections'] = {}
        self.assertRejects(document, 'list')

    def test_section_requires_both_titles(self):
        self.assertRejects(
            schema(sections=[{'key': 'vitals', 'title_en': 'Vitals',
                              'title_fa': ''}]),
            'title_fa')

    def test_rejects_duplicate_section_keys(self):
        section = {'key': 'vitals', 'title_en': 'Vitals', 'title_fa': 'حیاتی'}
        self.assertRejects(schema(sections=[section, dict(section)]),
                           'duplicate section')

    def test_field_may_reference_a_declared_section(self):
        self.assertIsNone(validate_field_schema(schema(
            digit_field(section='vitals'),
            sections=[{'key': 'vitals', 'title_en': 'Vitals',
                       'title_fa': 'حیاتی'}],
        )))

    def test_field_cannot_reference_an_undeclared_section(self):
        self.assertRejects(
            schema(digit_field(section='nowhere')), 'not a declared section')

    def test_field_without_a_section_is_valid(self):
        self.assertIsNone(validate_field_schema(schema(digit_field())))

    def test_type_accessors(self):
        document = schema(image_field('mri'), digit_field('bp'))
        self.assertEqual([f['key'] for f in image_fields(document)], ['mri'])
        self.assertEqual(
            [f['key'] for f in digit_string_fields(document)], ['bp'])

    def test_accessors_of_empty_schema(self):
        self.assertEqual(image_fields({}), [])
        self.assertEqual(digit_string_fields({}), [])

    def test_find_field_hits_and_misses(self):
        document = schema(image_field('mri_image'))
        self.assertEqual(find_field(document, 'mri_image')['key'], 'mri_image')
        self.assertIsNone(find_field(document, 'absent'))


class DigitStringValueTests(TestCase):
    """Digit strings are strings. Leading zeros are the whole point."""

    def assertRejects(self, document, values, fragment):
        with self.assertRaises(DjangoValidationError) as caught:
            validate_values(document, values)
        self.assertIn(fragment, str(caught.exception))

    def test_accepts_digits(self):
        self.assertIsNone(
            validate_values(schema(digit_field('bp')), {'bp': '12080'}))

    def test_leading_zeros_survive(self):
        document = schema(digit_field('code', min_length=10, max_length=10))
        self.assertIsNone(validate_values(document, {'code': '0012345678'}))

    def test_rejects_letters(self):
        self.assertRejects(
            schema(digit_field('bp')), {'bp': '120/80'}, 'digits only')

    def test_rejects_an_int(self):
        """An int has already lost the leading zero by the time we see it."""
        self.assertRejects(
            schema(digit_field('bp')), {'bp': 12080}, 'must be a string')

    def test_rejects_too_short(self):
        self.assertRejects(
            schema(digit_field('code', min_length=10)),
            {'code': '123'}, 'at least 10')

    def test_rejects_too_long(self):
        self.assertRejects(
            schema(digit_field('code', max_length=4)),
            {'code': '123456'}, 'at most 4')

    def test_required_field_must_be_present(self):
        self.assertRejects(
            schema(digit_field('bp', required=True)), {}, 'required')

    def test_optional_field_may_be_absent(self):
        self.assertIsNone(validate_values(schema(digit_field('bp')), {}))

    def test_optional_field_may_be_empty_string(self):
        self.assertIsNone(
            validate_values(schema(digit_field('bp')), {'bp': ''}))

    def test_rejects_undeclared_keys(self):
        self.assertRejects(
            schema(digit_field('bp')), {'ghost': '1'}, 'undeclared')

    def test_image_fields_do_not_live_in_values(self):
        self.assertRejects(
            schema(image_field('mri')), {'mri': '1'}, 'undeclared')


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
        cls.monitoring = cls.step_1

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
        other = MonitoringType.objects.get(slug='step_2')
        PatientEntry.objects.create(
            monitoring=self.monitoring, national_id='0012345678')
        PatientEntry.objects.create(
            monitoring=other, national_id='0012345678')
        self.assertEqual(PatientEntry.objects.count(), 2)

    def test_deleting_a_monitoring_takes_its_entries(self):
        doomed = MonitoringType.objects.create(
            slug='doomed', name_en='Doomed', name_fa='حذفی')
        entry = PatientEntry.objects.create(
            monitoring=doomed, national_id='0012345678')
        PatientEntryFile.objects.create(
            entry=entry, field_key='mri_image', key='entries/1/x.jpg',
            original_name='x.jpg', content_type='image/jpeg', size=10)
        doomed.delete()
        self.assertEqual(PatientEntry.objects.count(), 0)
        self.assertEqual(PatientEntryFile.objects.count(), 0)

    def test_deleting_a_spreadsheet_leaves_entries_alone(self):
        """The two stores are unrelated; neither cascades into the other."""
        batch = SaderatBankHealthMonitoring.objects.create(
            name='Some upload', type=self.step_1, json=[{'a': 1}])
        PatientEntry.objects.create(
            monitoring=self.step_1, national_id='0012345678')
        batch.delete()
        self.assertEqual(PatientEntry.objects.count(), 1)

    def test_excel_upload_is_unaffected_by_a_schema(self):
        """The load-bearing constraint: a schema never gates Excel."""
        self.step_1.field_schema = {
            'version': 1,
            'fields': [{
                'key': 'mri_image', 'type': 'image',
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
                    'key': 'mri_image', 'type': 'image',
                    'label_en': 'MRI Image', 'label_fa': 'تصویر ام‌آر‌آی',
                    'required': True, 'multiple': True,
                    'max_size_mb': 5, 'max_count': 3,
                }],
            },
        )
        cls.monitoring = cls.type
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
                    'key': 'mri_image', 'type': 'image',
                    'label_en': 'MRI Image', 'label_fa': 'تصویر ام‌آر‌آی',
                    'required': True, 'multiple': True,
                    'max_size_mb': 5, 'max_count': 2,
                }],
            },
        )
        cls.monitoring = cls.type
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

    def key(self, name='abc.jpg', national_id='0012345678'):
        return f'entries/{self.monitoring.id}/{national_id}/mri_image/{name}'

    def file_payload(self, key=None):
        return {'field_key': 'mri_image', 'key': key or self.key(),
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
            self.file_payload(self.key('a.jpg')),
            self.file_payload(self.key('b.jpg')),
            self.file_payload(self.key('c.jpg')),
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_national_id_conflicts(self):
        self.create()
        response = self.create(
            files=[self.file_payload(self.key('d.jpg'))])
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('id', response.data)

    def test_persian_national_id_conflicts_with_its_ascii_twin(self):
        """The whole reason normalization is server-side."""
        self.create()
        response = self.create(
            national_id='۰۰۱۲۳۴۵۶۷۸',
            files=[self.file_payload(self.key('e.jpg'))])
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
                self.file_payload(self.key('new.jpg'))]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(PatientEntryFile.objects.count(), 1)
        self.assertEqual(response.data['files'][0]['original_name'],
                         'scan.jpg')

    def test_editing_does_not_reverify_images_already_on_the_record(self):
        """Fixing a typo must not depend on storage being reachable."""
        entry_id = self.create().data['id']
        self.head_object.side_effect = S3Unavailable('unreachable')
        response = self.client.patch(
            reverse('patient-entries-detail', args=[entry_id]),
            {'files': [self.file_payload()]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK,
                         response.data)

    def test_edit_keeps_existing_images_and_adds_new_ones(self):
        """The ordinary edit: nothing removed, one image added."""
        entry_id = self.create().data['id']
        response = self.client.patch(
            reverse('patient-entries-detail', args=[entry_id]),
            {'files': [self.file_payload(),
                       self.file_payload(self.key('second.jpg'))]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK,
                         response.data)
        self.assertEqual(
            sorted(PatientEntryFile.objects.values_list('key', flat=True)),
            sorted([self.key(), self.key('second.jpg')]),
        )

    def test_new_image_from_another_records_folder_is_refused(self):
        """An upload is only attachable to the record it was signed for."""
        response = self.create(files=[
            self.file_payload(self.key('x.jpg', national_id='9999999999'))])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(PatientEntry.objects.count(), 0)

    def test_failed_edit_keeps_the_images_it_had(self):
        """Replacing the image set is all-or-nothing."""
        entry_id = self.create().data['id']
        self.client.raise_request_exception = False
        with mock.patch.object(
                PatientEntryFile.objects, 'bulk_create',
                side_effect=IntegrityError('boom')):
            response = self.client.patch(
                reverse('patient-entries-detail', args=[entry_id]),
                {'files': [self.file_payload(self.key('replacement.jpg'))]},
                format='json',
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            list(PatientEntryFile.objects.values_list('key', flat=True)),
            [self.key()],
        )

    def test_delete_removes_entry_and_files(self):
        entry_id = self.create().data['id']
        self.client.delete(reverse('patient-entries-detail', args=[entry_id]))
        self.assertEqual(PatientEntry.objects.count(), 0)
        self.assertEqual(PatientEntryFile.objects.count(), 0)


class PatientEntryValuesApiTests(APITestCase):
    """digit_string values travel through the API as strings."""

    @classmethod
    def setUpTestData(cls):
        cls.type = MonitoringType.objects.create(
            slug='vitals', name_en='Vitals', name_fa='علائم حیاتی',
            field_schema={
                'version': 1,
                'sections': [{'key': 'vitals', 'title_en': 'Vitals',
                              'title_fa': 'علائم حیاتی'}],
                'fields': [
                    {'key': 'personnel_code', 'type': 'digit_string',
                     'label_en': 'Personnel Code', 'label_fa': 'کد پرسنلی',
                     'section': 'vitals', 'required': True,
                     'min_length': 10, 'max_length': 10},
                    {'key': 'blood_pressure', 'type': 'digit_string',
                     'label_en': 'Blood Pressure', 'label_fa': 'فشار خون',
                     'section': 'vitals', 'required': False},
                ],
            },
        )
        cls.monitoring = cls.type
        User = get_user_model()
        cls.user = User.objects.create_user('op3', 'op3@example.com', 'pw')

    def setUp(self):
        self.client.force_authenticate(self.user)

    def create(self, values):
        return self.client.post(
            reverse('patient-entries-list'),
            {'monitoring': self.monitoring.id, 'national_id': '0012345678',
             'values': values},
            format='json',
        )

    def test_leading_zeros_survive_the_round_trip(self):
        response = self.create({'personnel_code': '0000000042'})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            response.data['values']['personnel_code'], '0000000042')
        entry = PatientEntry.objects.get(pk=response.data['id'])
        self.assertEqual(entry.values['personnel_code'], '0000000042')

    def test_missing_required_value_is_refused(self):
        response = self.create({'blood_pressure': '12080'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_letters_are_refused(self):
        response = self.create({'personnel_code': '00abc00042'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_length_is_refused(self):
        response = self.create({'personnel_code': '42'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_undeclared_key_is_refused(self):
        response = self.create(
            {'personnel_code': '0000000042', 'ghost': '1'})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PatientRecordsApiTests(APITestCase):
    """One patient's records across every monitoring, for display.

    Readable without signing in, because the patient portal has no sign-in
    of its own. That makes the national ID the only key, so the endpoint must
    never answer without one, and anonymous callers are rate-limited so it
    cannot be walked through ID by ID.
    """

    @classmethod
    def setUpTestData(cls):
        schema = {
            'version': 1,
            'fields': [
                {'key': 'bp', 'type': 'digit_string', 'label_en': 'BP',
                 'label_fa': 'فشار خون'},
                {'key': 'scan', 'type': 'image', 'label_en': 'Scan',
                 'label_fa': 'اسکن'},
            ],
        }
        cls.mri = MonitoringType.objects.create(
            slug='mri', name_en='MRI', name_fa='ام‌آر‌آی', field_schema=schema)
        cls.lab = MonitoringType.objects.create(
            slug='lab', name_en='Lab', name_fa='آزمایش', field_schema=schema)
        cls.entry = PatientEntry.objects.create(
            monitoring=cls.mri, national_id='0012345678', values={'bp': '120'})
        PatientEntryFile.objects.create(
            entry=cls.entry, field_key='scan',
            key=f'entries/{cls.mri.id}/0012345678/scan/a.jpg',
            original_name='a.jpg', content_type='image/jpeg', size=10)
        PatientEntry.objects.create(
            monitoring=cls.lab, national_id='0012345678', values={'bp': '90'})
        PatientEntry.objects.create(
            monitoring=cls.lab, national_id='0099999999', values={'bp': '80'})
        User = get_user_model()
        cls.user = User.objects.create_user('viewer', 'v@example.com', 'pw')

    def setUp(self):
        # Throttle counts live in the cache; a previous test's requests must
        # not count against this one.
        from django.core.cache import cache
        cache.clear()
        get = mock.patch(
            'saderatBankHealthMonitoring.serializers.presign_get',
            return_value='https://example.invalid/read')
        get.start()
        self.addCleanup(get.stop)

    def fetch(self, national_id='0012345678'):
        params = {} if national_id is None else {'national_id': national_id}
        return self.client.get(reverse('patient-records'), params)

    def test_lists_every_monitoring_for_the_patient_and_nobody_else(self):
        response = self.fetch()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            sorted(r['monitoring']['slug'] for r in response.data),
            ['lab', 'mri'])
        values = sorted(r['values']['bp'] for r in response.data)
        self.assertEqual(values, ['120', '90'])

    def test_carries_what_the_page_needs_to_render(self):
        record = next(r for r in self.fetch().data
                      if r['monitoring']['slug'] == 'mri')
        self.assertEqual(record['monitoring']['name_fa'], 'ام‌آر‌آی')
        self.assertEqual(
            [f['key'] for f in record['monitoring']['field_schema']['fields']],
            ['bp', 'scan'])
        self.assertEqual(record['files'][0]['url'],
                         'https://example.invalid/read')

    def test_is_readable_without_signing_in(self):
        self.assertEqual(self.fetch().status_code, status.HTTP_200_OK)

    def test_refuses_to_answer_without_a_national_id(self):
        self.assertEqual(self.fetch(None).status_code,
                         status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.fetch('').status_code,
                         status.HTTP_400_BAD_REQUEST)

    def test_refuses_anything_that_is_not_a_national_id(self):
        for bad in ('123', '00123456789', '00123x5678'):
            self.assertEqual(self.fetch(bad).status_code,
                             status.HTTP_400_BAD_REQUEST, bad)

    def test_accepts_persian_digits(self):
        response = self.fetch('۰۰۱۲۳۴۵۶۷۸')
        self.assertEqual(len(response.data), 2)

    def test_unknown_patient_is_an_empty_list(self):
        response = self.fetch('0000000001')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_anonymous_callers_are_rate_limited(self):
        codes = [self.fetch().status_code for _ in range(31)]
        self.assertEqual(codes[:30], [status.HTTP_200_OK] * 30)
        self.assertEqual(codes[30], status.HTTP_429_TOO_MANY_REQUESTS)

    def test_signed_in_staff_are_not_rate_limited(self):
        self.client.force_authenticate(self.user)
        codes = {self.fetch().status_code for _ in range(40)}
        self.assertEqual(codes, {status.HTTP_200_OK})

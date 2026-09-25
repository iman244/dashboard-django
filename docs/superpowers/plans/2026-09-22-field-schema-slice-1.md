# MonitoringType field_schema — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let staff declare `file` fields on a `MonitoringType`, and let an operator upload one or many files per field for a patient identified by national ID, stored in Arvan S3 via presigned PUT.

**Architecture:** `MonitoringType` gains a `field_schema` JSONB column describing fields. A new `PatientEntry` (one per monitoring + national ID) owns `PatientEntryFile` rows holding S3 object keys. The browser uploads bytes straight to S3 using a presigned URL that Django issues only after validating the file against the schema; Django then `HEAD`s each object before committing the row. All S3 calls are confined to one module so the proxy fallback stays cheap.

**Tech Stack:** Django 5.2.7, DRF 3.16, drf-spectacular, boto3 (new), Postgres 18; Next.js 16 App Router, TanStack Query v5, react-hook-form + zod, next-intl.

**Spec:** `docs/superpowers/specs/2026-09-22-monitoring-type-field-schema-design.md`

## Status — 2026-09-22

**Tasks 1-9 complete** on branch `feat/field-schema-uploads` in both repos.
Django: 73 tests passing. Next.js: build and `tsc --noEmit` clean, lint at
exactly 33 errors / 29 warnings (baseline held), message parity 485/485.

**Task 10 is blocked on the user**: it needs the bucket name, S3 credentials,
a bucket CORS rule, and a console login. Nothing in tasks 1-9 has been
exercised against real object storage -- every S3 call is mocked in the tests.

### Two places reality differed from this plan

1. **Task 6, the 409.** DRF generates a `UniqueTogetherValidator` from the
   model's `UniqueConstraint`, so a duplicate was caught in validation and
   returned 400; the planned `IntegrityError` handler never ran. That
   generated validator also SELECTs before inserting, which is the
   check-then-insert race the constraint exists to close. Fixed by setting
   `validators = []` on `PatientEntrySerializer.Meta` and letting the database
   raise.
2. **Task 6, the savepoint.** Catching `IntegrityError` and then querying for
   the existing row raised `TransactionManagementError`. The create is now
   wrapped in `transaction.atomic()` so the failed INSERT rolls back to a
   savepoint and the surrounding transaction stays usable.

Also: Task 9 as written created the form components but never rendered them.
The form is reached from a `Files` row action on the monitoring list page,
which opens `_entry-form/dialog.tsx`. This avoids touching
`step-1/[id]/page.tsx` (1576 lines).

## Global Constraints

- **The Excel path is untouched.** Do not modify `SaderatBankHealthMonitoringUploadExcelSerializer` or the `upload_excel` action. Do not add any validation, size limit, or schema check to the Excel upload. Do not couple entries to the `json` blob in either direction.
- **`field_schema` never gates the Excel upload.** No branching on its presence anywhere.
- Only `file` fields are supported in slice 1. Any other `type` value is a validation error.
- `national_id` is reserved and may not be declared as a field key.
- Field keys match `^[a-z][a-z0-9_]*$` and are unique within a schema.
- `label_en` and `label_fa` are both required and non-empty on every field.
- Django normalizes Persian (`۰۱۲`) and Arabic-Indic (`٠١٢`) digits to ASCII before any lookup, save, or key construction.
- Django is the validation authority; the Next.js copy is UX convenience only.
- dashboard-nextjs is bilingual en/fa: all copy goes through route-scoped namespaces in `messages/en.json` + `messages/fa.json` at exact key parity; prefer logical CSS (`ps-*`, `pe-*`, `start-*`, `end-*`) over physical.
- The lint baseline on dashboard-nextjs is **33 errors / 29 warnings**. Never let either grow. `npx tsc --noEmit` and `npm run build` must pass.
- Run `npm run build` once before `npx tsc --noEmit`, or `PageProps` globals are missing.

## Running the Django tests

There is no local Postgres. Start a disposable container first:

```bash
docker run -d --name mr-test-db -p 5432:5432 \
  -e POSTGRES_DB=medical-dashboard \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=qwer123456 \
  postgres:18
```

Then, from `dashboard-django/`:

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test saderatBankHealthMonitoring -v2
```

Tear down with `docker rm -f mr-test-db` when finished. Do **not** reuse
`compose.yml`'s `db` service — that is the production stack with a different
database name.

## File Structure

| File | Responsibility |
|---|---|
| `saderatBankHealthMonitoring/schema.py` | **Create.** Pure validation of a `field_schema` document. No Django, no DB, no I/O. |
| `saderatBankHealthMonitoring/national_id.py` | **Create.** `normalize_national_id()` — digit folding in one place. |
| `saderatBankHealthMonitoring/s3.py` | **Create.** The only module that talks to S3: client construction, presigned PUT/GET, `head_object`. |
| `saderatBankHealthMonitoring/models.py` | **Modify.** Add `field_schema`; add `PatientEntry`, `PatientEntryFile`. |
| `saderatBankHealthMonitoring/serializers.py` | **Modify.** Expose `field_schema`; add entry + presign serializers. |
| `saderatBankHealthMonitoring/views.py` | **Modify.** Add `PatientEntryViewSet`. |
| `saderatBankHealthMonitoring/urls.py` | **Modify.** Register `patient-entries`. |
| `saderatBankHealthMonitoring/tests.py` | **Modify.** Add test classes; fix the one existing test that asserts an exact dict. |
| `medicaldashboard/settings/base.py` | **Modify.** `S3_*` settings via `django-environ`. |
| `src/data/patient-entry/` (nextjs) | **Create.** Types + API hooks, mirroring `src/data/monitoring-type/`. |
| `src/data/patient-entry/upload.ts` (nextjs) | **Create.** Presign → PUT → collect descriptors. Isolated so the proxy fallback is a one-file change. |
| `.../console/saderat-bank-health-monitoring/_entry-form/` (nextjs) | **Create.** Schema-driven form. |

---

### Task 1: Schema validator

Pure functions, no database. Written first because everything else depends on
the contract being unambiguous.

**Files:**
- Create: `saderatBankHealthMonitoring/schema.py`
- Test: `saderatBankHealthMonitoring/tests.py` (append `FieldSchemaValidationTests`)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `validate_field_schema(document: dict) -> None` — raises `django.core.exceptions.ValidationError` with a human-readable message, or returns `None`.
  - `file_fields(document: dict) -> list[dict]` — the `file` field definitions, `[]` for an empty schema.
  - `find_field(document: dict, key: str) -> dict | None`
  - `RESERVED_KEYS = frozenset({'national_id'})`
  - `SUPPORTED_TYPES = frozenset({'file'})`

- [x] **Step 1: Write the failing tests**

Append to `saderatBankHealthMonitoring/tests.py`:

```python
from django.core.exceptions import ValidationError as DjangoValidationError

from .schema import (
    file_fields,
    find_field,
    validate_field_schema,
)


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
        self.assertRejects(
            schema(file_field(type='string')), 'file')

    def test_rejects_reserved_key(self):
        self.assertRejects(
            schema(file_field(key='national_id')), 'reserved')

    def test_rejects_malformed_key(self):
        self.assertRejects(schema(file_field(key='MRI Image')), 'key')
        self.assertRejects(schema(file_field(key='9lives')), 'key')
        self.assertRejects(schema(file_field(key='has.dot')), 'key')

    def test_rejects_duplicate_keys(self):
        self.assertRejects(
            schema(file_field(), file_field()), 'duplicate')

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
```

Add `from django.test import TestCase` to the imports at the top of the file if
it is not already there.

- [x] **Step 2: Run the tests to verify they fail**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test \
  saderatBankHealthMonitoring.tests.FieldSchemaValidationTests -v2
```

Expected: FAIL — `ModuleNotFoundError: No module named 'saderatBankHealthMonitoring.schema'`.

- [x] **Step 3: Write the implementation**

Create `saderatBankHealthMonitoring/schema.py`:

```python
"""Validation of a MonitoringType.field_schema document.

Pure functions: no Django models, no database, no I/O. The schema is data an
operator wrote, so every message here is read by a human trying to fix their
own JSON -- say what is wrong and where, not merely that something is.

Slice 1 supports one field type, `file`. Unsupported types are rejected rather
than ignored, so adding `string` and friends later is a pure addition and no
schema written today can mean something different tomorrow.
"""
import re

from django.core.exceptions import ValidationError

SCHEMA_VERSION = 1

# `national_id` identifies the entry; it is a column, not a field someone
# declares. Allowing it would produce two competing sources for one value.
RESERVED_KEYS = frozenset({'national_id'})

SUPPORTED_TYPES = frozenset({'file'})

# A JSONB object key and a `field_key` column value. Keeping it to this shape
# avoids the accessor-string problems that dotted keys already cause the
# dashboard's tables.
KEY_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')


def validate_field_schema(document):
    """Raise ValidationError unless `document` is a valid schema.

    An empty dict is valid and means "no fields declared yet" -- a type is not
    obliged to have a form, and never stops accepting Excel either way.
    """
    if not isinstance(document, dict):
        raise ValidationError('field_schema must be an object.')

    if not document:
        return None

    version = document.get('version')
    if version != SCHEMA_VERSION:
        raise ValidationError(
            f'Unsupported field_schema version {version!r}; '
            f'expected {SCHEMA_VERSION}.'
        )

    fields = document.get('fields', [])
    if not isinstance(fields, list):
        raise ValidationError('field_schema.fields must be a list.')

    seen = set()
    for index, field in enumerate(fields):
        _validate_field(field, index, seen)

    return None


def _validate_field(field, index, seen):
    where = f'fields[{index}]'

    if not isinstance(field, dict):
        raise ValidationError(f'{where} must be an object.')

    key = field.get('key')
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise ValidationError(
            f'{where}.key must match {KEY_PATTERN.pattern} '
            f'(got {key!r}).'
        )
    if key in RESERVED_KEYS:
        raise ValidationError(
            f'{where}.key {key!r} is reserved and cannot be declared.')
    if key in seen:
        raise ValidationError(f'duplicate field key {key!r}.')
    seen.add(key)

    field_type = field.get('type')
    if field_type not in SUPPORTED_TYPES:
        raise ValidationError(
            f'{where}.type {field_type!r} is not supported; '
            f'this version understands: {", ".join(sorted(SUPPORTED_TYPES))}.'
        )

    for label in ('label_en', 'label_fa'):
        value = field.get(label)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(
                f'{where}.{label} is required and must not be empty.')

    _validate_file_constraints(field, where)


def _validate_file_constraints(field, where):
    accept = field.get('accept', ['*/*'])
    if not isinstance(accept, list) or not accept:
        raise ValidationError(
            f'{where}.accept must be a non-empty list of MIME patterns.')
    for pattern in accept:
        if not isinstance(pattern, str) or '/' not in pattern:
            raise ValidationError(
                f'{where}.accept entry {pattern!r} is not a MIME pattern.')

    for constraint in ('max_size_mb', 'max_count'):
        value = field.get(constraint)
        if value is None:
            continue
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ValidationError(
                f'{where}.{constraint} must be a positive integer '
                f'(got {value!r}).'
            )


def file_fields(document):
    """The file field declarations of `document`, in order."""
    if not isinstance(document, dict):
        return []
    return [
        field for field in document.get('fields', [])
        if isinstance(field, dict) and field.get('type') == 'file'
    ]


def find_field(document, key):
    """The field declared as `key`, or None."""
    if not isinstance(document, dict):
        return None
    for field in document.get('fields', []):
        if isinstance(field, dict) and field.get('key') == key:
            return field
    return None
```

- [x] **Step 4: Run the tests to verify they pass**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test \
  saderatBankHealthMonitoring.tests.FieldSchemaValidationTests -v2
```

Expected: PASS, 16 tests.

- [x] **Step 5: Commit**

```bash
git add saderatBankHealthMonitoring/schema.py saderatBankHealthMonitoring/tests.py
git commit -m "Add field_schema validation, file fields only"
```

---

### Task 2: National ID normalization

**Files:**
- Create: `saderatBankHealthMonitoring/national_id.py`
- Test: `saderatBankHealthMonitoring/tests.py` (append `NationalIdNormalizationTests`)

**Interfaces:**
- Produces: `normalize_national_id(value: str) -> str`

- [x] **Step 1: Write the failing tests**

```python
from .national_id import normalize_national_id


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
```

- [x] **Step 2: Run the tests to verify they fail**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test \
  saderatBankHealthMonitoring.tests.NationalIdNormalizationTests -v2
```

Expected: FAIL — `No module named 'saderatBankHealthMonitoring.national_id'`.

- [x] **Step 3: Write the implementation**

Create `saderatBankHealthMonitoring/national_id.py`:

```python
"""Folding a national id to one canonical spelling.

An operator types on a Persian keyboard, which produces ۰۱۲; some source data
carries Arabic-Indic ٠١٢. As a *display* concern the dashboard already handles
this with `digitsFaToEn`. Here it is an *identity* concern: `national_id` is
half of a unique constraint, and '۰۰۱۲۳۴۵۶۷۸' and '0012345678' are different
strings. Without folding, the constraint would accept both and split one
patient's files across two entries without raising anything.

The client normalizes too. This is the guarantee; that is the courtesy.
"""

# Persian (U+06F0..) and Arabic-Indic (U+0660..) digits, in order.
_DIGIT_MAP = str.maketrans(
    '۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩',
    '01234567890123456789',
)


def normalize_national_id(value):
    """`value` with Persian/Arabic digits folded to ASCII and ends trimmed."""
    if not isinstance(value, str):
        return value
    return value.translate(_DIGIT_MAP).strip()
```

- [x] **Step 4: Run the tests to verify they pass**

Expected: PASS, 7 tests.

- [x] **Step 5: Commit**

```bash
git add saderatBankHealthMonitoring/national_id.py saderatBankHealthMonitoring/tests.py
git commit -m "Fold Persian and Arabic digits in national ids"
```

---

### Task 3: Models and migration

**Files:**
- Modify: `saderatBankHealthMonitoring/models.py`
- Modify: `saderatBankHealthMonitoring/serializers.py` (expose `field_schema`)
- Modify: `saderatBankHealthMonitoring/tests.py:37-46` — `test_member_reads_types` asserts an **exact dict** and will break when the serializer grows a field. Update it in this task, do not delete it.
- Create: `saderatBankHealthMonitoring/migrations/0005_field_schema_and_patient_entries.py` (generated)

**Interfaces:**
- Consumes: `validate_field_schema` (Task 1), `normalize_national_id` (Task 2).
- Produces:
  - `MonitoringType.field_schema` — `JSONField(default=dict, blank=True)`
  - `PatientEntry(monitoring, national_id, values, created_at, updated_at)` with `files` reverse accessor
  - `PatientEntryFile(entry, field_key, key, original_name, content_type, size, uploaded_at)`

- [x] **Step 1: Write the failing tests**

```python
from .models import PatientEntry, PatientEntryFile


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
```

Add `from django.db.utils import IntegrityError` to the test imports.

- [x] **Step 2: Run the tests to verify they fail**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test \
  saderatBankHealthMonitoring.tests.PatientEntryModelTests -v2
```

Expected: FAIL — `ImportError: cannot import name 'PatientEntry'`.

- [x] **Step 3: Write the models**

Append to `saderatBankHealthMonitoring/models.py`:

```python
from .national_id import normalize_national_id
from .schema import validate_field_schema
```

Add the column to `MonitoringType`, directly below `name_fa`:

```python
    # A loose description of the fields an operator fills in per patient for
    # this kind of report. Empty means "no fields declared yet" -- NOT "this is
    # an Excel type". The two capabilities are independent: a type may have a
    # schema, accept Excel uploads, both, or neither, and nothing branches on
    # which. See docs/superpowers/specs/2026-09-22-*-design.md.
    field_schema = models.JSONField(
        default=dict, blank=True, validators=[validate_field_schema])
```

Append the two new models:

```python
class PatientEntry(models.Model):
    """One patient's answers to a monitoring's field_schema.

    Independent of `SaderatBankHealthMonitoring.json`: an entry may exist for a
    national id the uploaded spreadsheet has never mentioned, and uploading a
    new spreadsheet never invalidates an entry. They are two stores that happen
    to share a key.

    CASCADE rather than PROTECT, unlike MonitoringType: an entry is *part of* a
    monitoring rather than a shared thing the monitoring refers to, so it has
    no meaning once the monitoring is gone.
    """

    monitoring = models.ForeignKey(
        SaderatBankHealthMonitoring,
        on_delete=models.CASCADE,
        related_name='entries',
    )
    national_id = models.CharField(max_length=10, db_index=True)
    # Where non-file field values land in slice 2. The column ships now so
    # adding string/number/date/boolean/choice needs no second migration.
    values = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['national_id']
        constraints = [
            models.UniqueConstraint(
                fields=['monitoring', 'national_id'],
                name='unique_entry_monitoring_national_id',
            ),
        ]

    def save(self, *args, **kwargs):
        # Folded here rather than only in the serializer, so an entry created
        # by a management command or the admin cannot bypass it and split one
        # patient across two rows.
        self.national_id = normalize_national_id(self.national_id)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.national_id} @ {self.monitoring.name}'


class PatientEntryFile(models.Model):
    """One uploaded object: its metadata here, its bytes in S3.

    `key` is the S3 object key rather than a FileField, because the browser
    writes the bytes directly via a presigned PUT. A FileField would imply
    Django owns that write.
    """

    entry = models.ForeignKey(
        PatientEntry, on_delete=models.CASCADE, related_name='files')
    field_key = models.CharField(max_length=64, db_index=True)
    key = models.CharField(max_length=512, unique=True)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=127)
    size = models.PositiveBigIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f'{self.field_key}: {self.original_name}'
```

- [x] **Step 4: Expose `field_schema` and fix the exact-dict test**

In `serializers.py`, add `'field_schema'` to `MonitoringTypeSerializer.Meta.fields`:

```python
class MonitoringTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoringType
        fields = ['id', 'slug', 'name_en', 'name_fa', 'field_schema']
```

In `tests.py`, `test_member_reads_types` compares the whole serialized dict, so
add the new key rather than loosening the assertion:

```python
        self.assertEqual(
            response.data[0],
            {'id': self.step_1.id, 'slug': 'step_1',
             'name_en': 'Step 1', 'name_fa': 'مرحله ۱',
             'field_schema': {}},
        )
```

- [x] **Step 5: Generate and inspect the migration**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py makemigrations saderatBankHealthMonitoring \
  --name field_schema_and_patient_entries
```

Read the generated file. It must contain `AddField` for `field_schema` and
`CreateModel` for both new models, and must **not** alter
`SaderatBankHealthMonitoring.json` or anything Excel-related.

- [x] **Step 6: Run the whole suite**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test saderatBankHealthMonitoring -v2
```

Expected: PASS, all pre-existing tests plus the new ones. The pre-existing
count must not drop.

- [x] **Step 7: Commit**

```bash
git add saderatBankHealthMonitoring/models.py saderatBankHealthMonitoring/serializers.py \
        saderatBankHealthMonitoring/migrations/ saderatBankHealthMonitoring/tests.py
git commit -m "Add field_schema, PatientEntry and PatientEntryFile"
```

---

### Task 4: S3 module and settings

The only module that talks to object storage. Confining it here is what keeps
the proxy fallback (approach C) a one-file change if browser→S3 proves
unreachable.

**Files:**
- Create: `saderatBankHealthMonitoring/s3.py`
- Modify: `medicaldashboard/settings/base.py`
- Modify: `requirements.txt`
- Test: `saderatBankHealthMonitoring/tests.py` (append `S3KeyTests`)

**Interfaces:**
- Produces:
  - `build_key(monitoring_id: int, national_id: str, field_key: str, filename: str) -> str`
  - `presign_put(key: str, content_type: str) -> dict` → `{'url': str, 'headers': dict, 'expires_in': int}`
  - `presign_get(key: str) -> str`
  - `head_object(key: str) -> dict | None` → `{'size': int, 'content_type': str}` or `None` when absent
  - `S3Unavailable` — raised when the bucket cannot be reached

- [x] **Step 1: Add the dependency**

```bash
./venv/bin/pip install 'boto3==1.35.99'
./venv/bin/pip freeze | grep -iE '^(boto3|botocore|s3transfer|jmespath)=' >> requirements.txt
sort -u -o requirements.txt requirements.txt
```

- [x] **Step 2: Add settings**

Append to `medicaldashboard/settings/base.py`, following the existing
`django-environ` idiom:

```python
# Object storage (Arvan, S3-compatible). Bytes are written by the operator's
# browser through a presigned PUT; Django only signs and verifies. Empty
# credentials are valid -- the presign endpoint then refuses rather than the
# application failing to boot, so local work without S3 is possible.
S3_ENDPOINT_URL = env('S3_ENDPOINT_URL',
                      default='https://s3.ir-thr-at1.arvanstorage.ir')
S3_ACCESS_KEY = env('S3_ACCESS_KEY', default='')
S3_SECRET_KEY = env('S3_SECRET_KEY', default='')
S3_BUCKET = env('S3_BUCKET', default='')
S3_REGION = env('S3_REGION', default='ir-thr-at1')
# Arvan accepts both; 'path' is the safer default for a bucket name that is not
# DNS-clean. Verify against the live bucket and change here only.
S3_ADDRESSING = env('S3_ADDRESSING', default='path')
S3_PRESIGN_TTL = env.int('S3_PRESIGN_TTL', default=900)
```

- [x] **Step 3: Write the failing tests**

Only key construction is tested here; signing and `HEAD` are exercised against
a stubbed client in Task 6, because testing boto3's own signing proves nothing.

```python
from .s3 import build_key


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
```

- [x] **Step 4: Run the tests to verify they fail**

Expected: FAIL — `No module named 'saderatBankHealthMonitoring.s3'`.

- [x] **Step 5: Write the implementation**

Create `saderatBankHealthMonitoring/s3.py`:

```python
"""Every call this project makes to object storage.

Deliberately the only such module. The design signs URLs so the operator's
browser uploads straight to the bucket, which keeps DICOM-sized files off a
two-core application server -- but that depends on the browser reaching the
bucket at all. If it cannot, the fallback is to proxy bytes through Django,
and that change should add a view here, not rework the models or the client.

Nothing in this module is imported by the Excel upload path.
"""
import os
import uuid

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from .national_id import normalize_national_id


class S3Unavailable(Exception):
    """Object storage could not be reached or is not configured."""


def is_configured():
    return bool(settings.S3_BUCKET and settings.S3_ACCESS_KEY
                and settings.S3_SECRET_KEY)


def client():
    if not is_configured():
        raise S3Unavailable('Object storage is not configured.')
    return boto3.client(
        's3',
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
        config=Config(
            signature_version='s3v4',
            s3={'addressing_style': settings.S3_ADDRESSING},
        ),
    )


def build_key(monitoring_id, national_id, field_key, filename):
    """Where an upload for this field of this patient lives.

    A uuid rather than the operator's filename: two people uploading `scan.jpg`
    to the same field must not overwrite one another, and the original name is
    kept in the database column that exists for it.
    """
    extension = os.path.splitext(filename or '')[1].lower()[:16]
    folded = normalize_national_id(national_id)
    return (
        f'entries/{monitoring_id}/{folded}/{field_key}/'
        f'{uuid.uuid4().hex}{extension}'
    )


def presign_put(key, content_type):
    """A URL the browser may PUT exactly this object to."""
    try:
        url = client().generate_presigned_url(
            'put_object',
            Params={
                'Bucket': settings.S3_BUCKET,
                'Key': key,
                'ContentType': content_type,
            },
            ExpiresIn=settings.S3_PRESIGN_TTL,
        )
    except (BotoCoreError, ClientError) as error:
        raise S3Unavailable(str(error)) from error
    return {
        'url': url,
        # The browser must send exactly this, or the signature will not match.
        'headers': {'Content-Type': content_type},
        'expires_in': settings.S3_PRESIGN_TTL,
    }


def presign_get(key):
    """A short-lived read URL; the bucket itself stays private."""
    try:
        return client().generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.S3_BUCKET, 'Key': key},
            ExpiresIn=settings.S3_PRESIGN_TTL,
        )
    except (BotoCoreError, ClientError) as error:
        raise S3Unavailable(str(error)) from error


def head_object(key):
    """`{'size', 'content_type'}` for `key`, or None if it is not there.

    This is what makes a client's claim about its own upload unnecessary to
    trust: the row is committed against what the bucket actually holds.
    """
    try:
        response = client().head_object(Bucket=settings.S3_BUCKET, Key=key)
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey'):
            return None
        raise S3Unavailable(str(error)) from error
    except BotoCoreError as error:
        raise S3Unavailable(str(error)) from error
    return {
        'size': response['ContentLength'],
        'content_type': response.get('ContentType', ''),
    }
```

- [x] **Step 6: Run the tests to verify they pass**

Expected: PASS, 4 tests.

- [x] **Step 7: Commit**

```bash
git add saderatBankHealthMonitoring/s3.py medicaldashboard/settings/base.py \
        requirements.txt saderatBankHealthMonitoring/tests.py
git commit -m "Add the object storage module and its settings"
```

---

### Task 5: Presign endpoint

Django signs nothing it has not first checked against the schema.

**Files:**
- Modify: `saderatBankHealthMonitoring/schema.py` (add upload checking)
- Modify: `saderatBankHealthMonitoring/serializers.py`
- Modify: `saderatBankHealthMonitoring/views.py`
- Modify: `saderatBankHealthMonitoring/urls.py`
- Test: `saderatBankHealthMonitoring/tests.py` (append `PresignTests`)

**Interfaces:**
- Consumes: `find_field`, `build_key`, `presign_put`, `S3Unavailable`.
- Produces:
  - `schema.mime_matches(patterns: list[str], content_type: str) -> bool`
  - `schema.check_upload(field: dict, content_type: str, size: int) -> None` — raises `ValidationError`
  - `POST /api/saderat-bank-health-monitoring/patient-entries/presign/`
  - URL name: `patient-entries-presign`

- [x] **Step 1: Write the failing tests**

```python
from unittest import mock


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
```

Add to the test imports: `from .s3 import S3Unavailable` and
`from .schema import mime_matches`.

- [x] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `cannot import name 'mime_matches'`.

- [x] **Step 3: Extend `schema.py`**

Append to `saderatBankHealthMonitoring/schema.py`:

```python
def mime_matches(patterns, content_type):
    """Whether `content_type` satisfies any of `patterns`.

    Supports `type/subtype`, `type/*` and `*/*`. Comparison is
    case-insensitive because browsers are not consistent about it.
    """
    if not content_type:
        return False
    actual = content_type.split(';')[0].strip().lower()
    for pattern in patterns or ['*/*']:
        candidate = pattern.strip().lower()
        if candidate == '*/*':
            return True
        if candidate == actual:
            return True
        if candidate.endswith('/*'):
            if actual.startswith(candidate[:-1]):
                return True
    return False


def check_upload(field, content_type, size):
    """Raise ValidationError unless this upload satisfies `field`."""
    accept = field.get('accept', ['*/*'])
    if not mime_matches(accept, content_type):
        raise ValidationError(
            f'{content_type!r} is not accepted by '
            f'{field["key"]!r}; allowed: {", ".join(accept)}.'
        )

    max_size_mb = field.get('max_size_mb')
    if max_size_mb is not None:
        limit = max_size_mb * 1024 * 1024
        if size > limit:
            raise ValidationError(
                f'File is {size} bytes; {field["key"]!r} allows at most '
                f'{max_size_mb} MB.'
            )
```

- [x] **Step 4: Add the serializer**

Append to `saderatBankHealthMonitoring/serializers.py`:

```python
class PresignRequestSerializer(serializers.Serializer):
    """What the browser must state before Django will sign anything."""

    monitoring = serializers.PrimaryKeyRelatedField(
        queryset=SaderatBankHealthMonitoring.objects.all())
    national_id = serializers.CharField(max_length=10)
    field_key = serializers.CharField(max_length=64)
    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=127)
    size = serializers.IntegerField(min_value=1)

    def validate_national_id(self, value):
        return normalize_national_id(value)
```

Add to the imports at the top of `serializers.py`:

```python
from django.core.exceptions import ValidationError as DjangoValidationError

from .national_id import normalize_national_id
from .schema import check_upload, find_field
```

- [x] **Step 5: Add the view**

Append to `saderatBankHealthMonitoring/views.py`:

```python
class PatientEntryViewSet(viewsets.ModelViewSet):
    """Per-patient entries against a monitoring's field_schema.

    Nothing here reads `SaderatBankHealthMonitoring.json`. Entries and the
    Excel blob are independent stores that share a key.
    """

    queryset = PatientEntry.objects.prefetch_related('files')
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Sign an upload for one file field',
        request=PresignRequestSerializer,
        responses={
            200: inline_serializer(
                name='PresignResponse',
                fields={
                    'upload_url': drf_serializers.CharField(),
                    'key': drf_serializers.CharField(),
                    'headers': drf_serializers.DictField(),
                    'expires_in': drf_serializers.IntegerField(),
                },
            ),
            400: OpenApiResponse(
                description='The schema does not permit this upload.'),
            503: OpenApiResponse(description='Object storage unavailable.'),
        },
    )
    @action(detail=False, methods=['post'])
    def presign(self, request):
        serializer = PresignRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        monitoring = data['monitoring']
        field = find_field(
            monitoring.type.field_schema, data['field_key'])
        if field is None or field.get('type') != 'file':
            raise drf_serializers.ValidationError(
                {'field_key': [
                    f'{data["field_key"]!r} is not a file field of '
                    f'{monitoring.type.slug!r}.']})

        try:
            check_upload(field, data['content_type'], data['size'])
        except DjangoValidationError as error:
            raise drf_serializers.ValidationError(
                {'file': list(error.messages)})

        key = build_key(
            monitoring.id, data['national_id'],
            data['field_key'], data['filename'])

        try:
            signed = presign_put(key, data['content_type'])
        except S3Unavailable as error:
            return Response(
                {'detail': f'Object storage is unavailable: {error}'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            'upload_url': signed['url'],
            'key': key,
            'headers': signed['headers'],
            'expires_in': signed['expires_in'],
        })
```

Add to the imports at the top of `views.py`:

```python
from django.core.exceptions import ValidationError as DjangoValidationError

from .models import PatientEntry
from .s3 import S3Unavailable, build_key, presign_put
from .schema import check_upload, find_field
from .serializers import PresignRequestSerializer
```

- [x] **Step 6: Register the route**

In `saderatBankHealthMonitoring/urls.py`:

```python
from .views import (
    MonitoringTypeViewSet,
    PatientEntryViewSet,
    SaderatBankHealthMonitoringViewSet,
)

router.register(
    r'patient-entries', PatientEntryViewSet, basename='patient-entries')
```

- [x] **Step 7: Run the tests to verify they pass**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test \
  saderatBankHealthMonitoring.tests.PresignTests \
  saderatBankHealthMonitoring.tests.MimeMatchTests -v2
```

Expected: PASS, 12 tests.

- [x] **Step 8: Commit**

```bash
git add saderatBankHealthMonitoring/
git commit -m "Sign uploads only for files the schema permits"
```

---

### Task 6: Entry read and write

**Files:**
- Modify: `saderatBankHealthMonitoring/serializers.py`
- Modify: `saderatBankHealthMonitoring/views.py`
- Test: `saderatBankHealthMonitoring/tests.py` (append `PatientEntryApiTests`)

**Interfaces:**
- Consumes: `head_object`, `presign_get`, `find_field`, `normalize_national_id`.
- Produces:
  - `PatientEntryFileSerializer` — read: `{id, field_key, original_name, content_type, size, url}`
  - `PatientEntrySerializer` — read/write: `{id, monitoring, national_id, values, files, created_at, updated_at}`; write accepts `files: [{field_key, key, original_name, content_type, size}]`
  - `GET|POST /patient-entries/`, `GET|PATCH|DELETE /patient-entries/{pk}/`
  - URL names: `patient-entries-list`, `patient-entries-detail`

- [x] **Step 1: Write the failing tests**

```python
class PatientEntryApiTests(APITestCase):
    """Entries are committed against what the bucket actually holds."""

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
                    'accept': ['image/jpeg'], 'max_size_mb': 5,
                    'max_count': 2,
                }],
            },
        )
        cls.monitoring = SaderatBankHealthMonitoring.objects.create(
            name='March', type=cls.type, json=[])
        User = get_user_model()
        cls.user = User.objects.create_user('op', 'op@example.com', 'pw')

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
        response = self.create()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('id', response.data)

    def test_persian_national_id_conflicts_with_its_ascii_twin(self):
        """The whole reason normalization is server-side."""
        self.create()
        response = self.create(national_id='۰۰۱۲۳۴۵۶۷۸')
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
```

- [x] **Step 2: Run the tests to verify they fail**

Expected: FAIL — `NoReverseMatch` or missing serializer.

- [x] **Step 3: Write the serializers**

Append to `saderatBankHealthMonitoring/serializers.py`:

```python
class PatientEntryFileSerializer(serializers.ModelSerializer):
    """A stored object. `url` is minted per read and expires."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = PatientEntryFile
        fields = ['id', 'field_key', 'key', 'original_name',
                  'content_type', 'size', 'url']
        read_only_fields = ['id', 'url']

    def get_url(self, obj):
        try:
            return presign_get(obj.key)
        except S3Unavailable:
            # A read must not fail wholesale because storage is unreachable;
            # the rest of the record is still worth showing.
            return None


class PatientEntrySerializer(serializers.ModelSerializer):
    files = PatientEntryFileSerializer(many=True, required=False)

    class Meta:
        model = PatientEntry
        fields = ['id', 'monitoring', 'national_id', 'values', 'files',
                  'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_national_id(self, value):
        return normalize_national_id(value)

    def validate(self, attrs):
        """Check every declared file against the schema and the bucket."""
        files = attrs.get('files')
        if files is None:
            return attrs

        monitoring = attrs.get('monitoring') or getattr(
            self.instance, 'monitoring', None)
        schema_document = monitoring.type.field_schema

        counts = {}
        for descriptor in files:
            field_key = descriptor['field_key']
            field = find_field(schema_document, field_key)
            if field is None or field.get('type') != 'file':
                raise serializers.ValidationError(
                    {'files': [f'{field_key!r} is not a file field of '
                               f'{monitoring.type.slug!r}.']})

            counts[field_key] = counts.get(field_key, 0) + 1
            max_count = field.get('max_count')
            if max_count is not None and counts[field_key] > max_count:
                raise serializers.ValidationError(
                    {'files': [f'{field_key!r} accepts at most {max_count} '
                               f'file(s).']})
            if not field.get('multiple', False) and counts[field_key] > 1:
                raise serializers.ValidationError(
                    {'files': [f'{field_key!r} accepts one file.']})

            try:
                check_upload(field, descriptor['content_type'],
                             descriptor['size'])
            except DjangoValidationError as error:
                raise serializers.ValidationError(
                    {'files': list(error.messages)})

            # The client tells us what it uploaded; the bucket is asked
            # whether that is true. Anything else would let a caller record a
            # 5 GB object as a 1 KB one.
            try:
                stored = head_object(descriptor['key'])
            except S3Unavailable as error:
                raise serializers.ValidationError(
                    {'files': [f'Object storage is unavailable: {error}']})
            if stored is None:
                raise serializers.ValidationError(
                    {'files': [f'No object was uploaded at '
                               f'{descriptor["key"]!r}.']})
            if stored['size'] != descriptor['size']:
                raise serializers.ValidationError(
                    {'files': [
                        f'{descriptor["key"]!r} is {stored["size"]} bytes in '
                        f'storage but was declared as {descriptor["size"]}.']})

        return attrs

    def create(self, validated_data):
        files = validated_data.pop('files', [])
        entry = PatientEntry.objects.create(**validated_data)
        self._replace_files(entry, files)
        return entry

    def update(self, instance, validated_data):
        files = validated_data.pop('files', None)
        for attribute, value in validated_data.items():
            setattr(instance, attribute, value)
        instance.save()
        if files is not None:
            self._replace_files(instance, files)
        return instance

    @staticmethod
    def _replace_files(entry, descriptors):
        """The submitted list is the whole truth for this entry.

        Objects in the bucket are not deleted here; see the spec's note on
        orphans. Removing the row is what makes the file gone from the app.
        """
        entry.files.all().delete()
        PatientEntryFile.objects.bulk_create([
            PatientEntryFile(entry=entry, **descriptor)
            for descriptor in descriptors
        ])
```

Add to the imports at the top of `serializers.py`:

```python
from .models import PatientEntry, PatientEntryFile
from .s3 import S3Unavailable, head_object, presign_get
```

- [x] **Step 4: Finish the viewset**

Add to `PatientEntryViewSet` in `views.py`, above the `presign` action:

```python
    serializer_class = PatientEntrySerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        monitoring = self.request.query_params.get('monitoring')
        if monitoring:
            queryset = queryset.filter(monitoring_id=monitoring)
        national_id = self.request.query_params.get('national_id')
        if national_id:
            # Folded on the way in, exactly as it was folded on the way to the
            # database, or a Persian-keyboard lookup would find nothing.
            queryset = queryset.filter(
                national_id=normalize_national_id(national_id))
        return queryset

    def create(self, request, *args, **kwargs):
        # The unique constraint is the check; asking first would race a
        # concurrent create. 409 rather than 400 so the client can offer to
        # open the existing entry instead of showing a field error.
        try:
            return super().create(request, *args, **kwargs)
        except IntegrityError:
            existing = PatientEntry.objects.filter(
                monitoring_id=request.data.get('monitoring'),
                national_id=normalize_national_id(
                    str(request.data.get('national_id', ''))),
            ).first()
            return Response(
                {'detail': 'This patient already has an entry for this '
                           'monitoring.',
                 'id': existing.id if existing else None},
                status=status.HTTP_409_CONFLICT,
            )
```

Add to the imports at the top of `views.py`:

```python
from django.db import IntegrityError

from .national_id import normalize_national_id
from .serializers import PatientEntrySerializer
```

Add `@extend_schema_view` decoration above the class so the generated schema
documents the 409:

```python
@extend_schema_view(
    list=extend_schema(summary='List patient entries'),
    retrieve=extend_schema(summary='Retrieve a patient entry'),
    create=extend_schema(
        summary='Create a patient entry',
        responses={
            201: PatientEntrySerializer,
            409: OpenApiResponse(
                description='This patient already has an entry here.'),
        },
    ),
    partial_update=extend_schema(summary='Update a patient entry'),
    destroy=extend_schema(summary='Delete a patient entry'),
)
```

- [x] **Step 5: Run the whole suite**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py test saderatBankHealthMonitoring -v2
```

Expected: PASS. Confirm the pre-existing Excel tests
(`MonitoringWireFormatTests`) still pass untouched.

- [x] **Step 6: Commit**

```bash
git add saderatBankHealthMonitoring/
git commit -m "Read and write patient entries, verified against the bucket"
```

---

### Task 7: Regenerate the API contract

**Files:**
- Modify: `openapi.yaml`
- Modify: `dashboard-nextjs/src/data/api-schema.d.ts`

- [x] **Step 1: Regenerate the schema**

```bash
DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python manage.py spectacular --file openapi.yaml
```

- [x] **Step 2: Confirm the new operations are present**

```bash
grep -n 'patient-entries' openapi.yaml | head
grep -n 'field_schema' openapi.yaml | head
```

Expected: the five entry routes plus `presign`, and `field_schema` on
`MonitoringType`.

- [x] **Step 3: Regenerate the client types**

```bash
cd ../dashboard-nextjs && npm run generate:api-types
```

- [x] **Step 4: Confirm the generated types compile**

```bash
cd ../dashboard-nextjs && npm run build && npx tsc --noEmit
```

Expected: both exit 0.

- [x] **Step 5: Commit both repos**

```bash
cd ../dashboard-django && git add openapi.yaml && \
  git commit -m "Regenerate the API contract"
cd ../dashboard-nextjs && git add src/data/api-schema.d.ts && \
  git commit -m "Regenerate API types for patient entries"
```

---

### Task 8: Next.js data layer

Mirrors `src/data/monitoring-type/` exactly — same file names, same hook
naming, same `AxiosError<{[key: string]: string[]}>` error shape.

**Files (all in `dashboard-nextjs`):**
- Create: `src/data/patient-entry/types.ts`
- Create: `src/data/patient-entry/api/list.ts`
- Create: `src/data/patient-entry/api/create.ts`
- Create: `src/data/patient-entry/api/update.ts`
- Create: `src/data/patient-entry/api/destroy.ts`
- Create: `src/data/patient-entry/api/presign.ts`
- Create: `src/data/patient-entry/api/index.ts`
- Create: `src/data/patient-entry/upload.ts`

**Interfaces:**
- Produces:
  - `PatientEntry`, `PatientEntry_CreateSerializer`, `PatientEntry_PatchSerializer`, `PatientEntryFile`, `FileDescriptor`
  - `useList_PatientEntry_API({ monitoring, nationalId })`
  - `useCreate_PatientEntry_API()`, `useUpdate_PatientEntry_API()`, `useDestroy_PatientEntry_API()`
  - `uploadToField({ monitoring, nationalId, fieldKey, file, onProgress }): Promise<FileDescriptor>`
  - `fileFieldsOf(type: MonitoringType): FileFieldDefinition[]`

- [x] **Step 1: Write the types**

`src/data/patient-entry/types.ts`:

```ts
import type { components } from "@/data/api-schema";
import type { MonitoringType } from "@/data/monitoring-type/types";

type Schemas = components["schemas"];

export type PatientEntry = Schemas["PatientEntry"];
export type PatientEntryFile = Schemas["PatientEntryFile"];
export type PatientEntry_CreateSerializer = Schemas["PatientEntryRequest"];
export type PatientEntry_PatchSerializer =
  Schemas["PatchedPatientEntryRequest"];

/**
 * What the entry endpoint accepts for one uploaded object. The browser has
 * already PUT the bytes; this is the receipt.
 */
export type FileDescriptor = {
  field_key: string;
  key: string;
  original_name: string;
  content_type: string;
  size: number;
};

/**
 * One `file` field, as `field_schema` declares it.
 *
 * `openapi-typescript` renders `field_schema` as an untyped object, because
 * Django stores it as JSONB and drf-spectacular has no shape to publish. This
 * is that shape, restated for the client. It is a convenience: Django is the
 * authority and revalidates everything on presign and on save.
 */
export type FileFieldDefinition = {
  key: string;
  type: "file";
  label_en: string;
  label_fa: string;
  required?: boolean;
  multiple?: boolean;
  accept?: string[];
  max_size_mb?: number;
  max_count?: number;
};

/** The file fields of `type`, or [] when it declares none. */
export const fileFieldsOf = (
  type: Pick<MonitoringType, "field_schema"> | undefined
): FileFieldDefinition[] => {
  const document = type?.field_schema as
    | { fields?: FileFieldDefinition[] }
    | undefined;
  return (document?.fields ?? []).filter((field) => field.type === "file");
};

/** The label for the active locale, with no English fallback in `fa`. */
export const labelOf = (field: FileFieldDefinition, locale: string) =>
  locale === "fa" ? field.label_fa : field.label_en;
```

- [x] **Step 2: Write the API hooks**

`src/data/patient-entry/api/list.ts`:

```ts
import { apiInstance } from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { PatientEntry } from "../types";

const PATH = "/saderat-bank-health-monitoring/patient-entries/";

export type PatientEntryListInput = {
  monitoring: number;
  nationalId?: string;
};

export const list = async ({ monitoring, nationalId }: PatientEntryListInput) => {
  const response = await apiInstance.get<PatientEntry[]>(PATH, {
    params: {
      monitoring,
      ...(nationalId ? { national_id: nationalId } : {}),
    },
    withAuthorization: true,
  });
  return response.data;
};

export const useList_PatientEntry_API = (input: PatientEntryListInput) =>
  useQuery({
    queryKey: ["patient-entries", input.monitoring, input.nationalId ?? null],
    queryFn: () => list(input),
    enabled: Boolean(input.monitoring),
  });
```

`src/data/patient-entry/api/create.ts`:

```ts
import { apiInstance } from "@/lib/api";
import { useMutation, UseMutationOptions } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { PatientEntry, PatientEntry_CreateSerializer } from "../types";

const PATH = "/saderat-bank-health-monitoring/patient-entries/";

export type PatientEntryCreateInput = {
  payload: PatientEntry_CreateSerializer;
};

export const create = async ({ payload }: PatientEntryCreateInput) => {
  const response = await apiInstance.post<PatientEntry>(PATH, payload, {
    withAuthorization: true,
  });
  return response.data;
};

export const useCreate_PatientEntry_API = (
  options?: Omit<
    UseMutationOptions<
      PatientEntry,
      // DRF's per-field arrays, plus the 409 body, which carries the id of the
      // entry that already exists so the caller can offer to open it.
      AxiosError<{ detail?: string; id?: number } & Record<string, string[]>>,
      PatientEntryCreateInput
    >,
    "mutationFn"
  >
) => useMutation({ mutationFn: create, ...options });
```

`src/data/patient-entry/api/update.ts`:

```ts
import { apiInstance } from "@/lib/api";
import { useMutation, UseMutationOptions } from "@tanstack/react-query";
import { AxiosError } from "axios";
import { PatientEntry, PatientEntry_PatchSerializer } from "../types";

const PATH = "/saderat-bank-health-monitoring/patient-entries/";

export type PatientEntryUpdateInput = {
  id: number;
  payload: PatientEntry_PatchSerializer;
};

export const update = async ({ id, payload }: PatientEntryUpdateInput) => {
  const response = await apiInstance.patch<PatientEntry>(
    `${PATH}${id}/`,
    payload,
    { withAuthorization: true }
  );
  return response.data;
};

export const useUpdate_PatientEntry_API = (
  options?: Omit<
    UseMutationOptions<
      PatientEntry,
      AxiosError<{ [key: string]: string[] }>,
      PatientEntryUpdateInput
    >,
    "mutationFn"
  >
) => useMutation({ mutationFn: update, ...options });
```

`src/data/patient-entry/api/destroy.ts`:

```ts
import { apiInstance } from "@/lib/api";
import { useMutation, UseMutationOptions } from "@tanstack/react-query";
import { AxiosError } from "axios";

const PATH = "/saderat-bank-health-monitoring/patient-entries/";

export type PatientEntryDestroyInput = { id: number };

export const destroy = async ({ id }: PatientEntryDestroyInput) => {
  await apiInstance.delete(`${PATH}${id}/`, { withAuthorization: true });
};

export const useDestroy_PatientEntry_API = (
  options?: Omit<
    UseMutationOptions<
      void,
      AxiosError<{ detail?: string }>,
      PatientEntryDestroyInput
    >,
    "mutationFn"
  >
) => useMutation({ mutationFn: destroy, ...options });
```

`src/data/patient-entry/api/presign.ts`:

```ts
import { apiInstance } from "@/lib/api";

const PATH = "/saderat-bank-health-monitoring/patient-entries/presign/";

export type PresignInput = {
  monitoring: number;
  national_id: string;
  field_key: string;
  filename: string;
  content_type: string;
  size: number;
};

export type PresignResult = {
  upload_url: string;
  key: string;
  headers: Record<string, string>;
  expires_in: number;
};

export const presign = async (input: PresignInput) => {
  const response = await apiInstance.post<PresignResult>(PATH, input, {
    withAuthorization: true,
  });
  return response.data;
};
```

`src/data/patient-entry/api/index.ts` re-exports all five modules, matching
`src/data/monitoring-type/api/index.ts`.

- [x] **Step 3: Write the upload helper**

`src/data/patient-entry/upload.ts`:

```ts
import axios from "axios";
import { digitsFaToEn } from "@persian-tools/persian-tools";
import { presign } from "./api/presign";
import type { FileDescriptor } from "./types";

/**
 * Put one file in object storage and return the receipt the entry endpoint
 * wants.
 *
 * Two steps: ask Django to sign an upload it has checked against the schema,
 * then PUT the bytes straight to the bucket. The bytes never pass through the
 * application server, which is what makes a 50 MB scan survive a two-core VM.
 *
 * If browser-to-bucket turns out to be unreachable on operators' networks, the
 * fallback is to POST the file to Django and let it forward. That change lives
 * in THIS FILE and nowhere else -- the form, the entry endpoint and the models
 * are all indifferent to how the bytes arrived.
 */
export const uploadToField = async ({
  monitoring,
  nationalId,
  fieldKey,
  file,
  onProgress,
}: {
  monitoring: number;
  nationalId: string;
  fieldKey: string;
  file: File;
  onProgress?: (percent: number) => void;
}): Promise<FileDescriptor> => {
  const signed = await presign({
    monitoring,
    national_id: digitsFaToEn(nationalId),
    field_key: fieldKey,
    filename: file.name,
    content_type: file.type || "application/octet-stream",
    size: file.size,
  });

  // A bare axios call, never `apiInstance`: that instance attaches the Django
  // JWT, and an unexpected Authorization header invalidates the S3 signature.
  await axios.put(signed.upload_url, file, {
    headers: signed.headers,
    onUploadProgress: (event) => {
      if (onProgress && event.total) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    },
  });

  return {
    field_key: fieldKey,
    key: signed.key,
    original_name: file.name,
    content_type: file.type || "application/octet-stream",
    size: file.size,
  };
};
```

- [x] **Step 4: Verify the types compile**

```bash
cd dashboard-nextjs && npm run build && npx tsc --noEmit
```

Expected: both exit 0.

- [x] **Step 5: Check the lint baseline has not grown**

```bash
cd dashboard-nextjs && npm run lint 2>&1 | tail -3
```

Expected: **33 errors, 29 warnings** — unchanged.

- [x] **Step 6: Commit**

```bash
git add src/data/patient-entry/
git commit -m "Add the patient entry data layer and upload helper"
```

---

### Task 9: The schema-driven form

**Files (all in `dashboard-nextjs`):**
- Create: `src/app/[locale]/(authenticated)/console/saderat-bank-health-monitoring/_entry-form/file-field.tsx`
- Create: `src/app/[locale]/(authenticated)/console/saderat-bank-health-monitoring/_entry-form/entry-form.tsx`
- Create: `src/app/[locale]/(authenticated)/console/saderat-bank-health-monitoring/_entry-form/index.ts`
- Modify: `messages/en.json`, `messages/fa.json`

**Interfaces:**
- Consumes: `fileFieldsOf`, `labelOf`, `uploadToField`, `useList_PatientEntry_API`, `useCreate_PatientEntry_API`, `useUpdate_PatientEntry_API`.
- Produces: `<EntryForm monitoring={id} type={monitoringType} />`

**Constraints for this task:** components at module scope, never declared
inside another component — `react-hooks/static-components` is already 25 of the
33 baseline errors and must not gain a 26th. Use `useWatch` rather than
`form.watch`, which caused a `set-state-in-effect` error on the monitoring-types
form. Logical CSS properties only.

- [x] **Step 1: Write the file field component**

`_entry-form/file-field.tsx` — one declared file field: a picker constrained by
`accept`, a list of chosen files with per-file progress, and removal. It is a
controlled component over `FileDescriptor[]`, so the parent owns all state.

```tsx
"use client";

import * as React from "react";
import { useLocale, useTranslations } from "next-intl";
import { Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { localeDigits } from "@/lib/utils";
import { uploadToField } from "@/data/patient-entry/upload";
import {
  labelOf,
  type FileDescriptor,
  type FileFieldDefinition,
} from "@/data/patient-entry/types";

export type FileFieldProps = {
  field: FileFieldDefinition;
  monitoring: number;
  nationalId: string;
  value: FileDescriptor[];
  onChange: (next: FileDescriptor[]) => void;
  disabled?: boolean;
};

export function FileField({
  field,
  monitoring,
  nationalId,
  value,
  onChange,
  disabled,
}: FileFieldProps) {
  const t = useTranslations("/console/saderat-bank-health-monitoring");
  const locale = useLocale();
  const [progress, setProgress] = React.useState<Record<string, number>>({});
  const [error, setError] = React.useState<string | null>(null);

  const atCapacity =
    field.max_count !== undefined && value.length >= field.max_count;

  const onPick = React.useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const picked = Array.from(event.target.files ?? []);
      event.target.value = "";
      if (picked.length === 0) return;
      setError(null);

      for (const file of picked) {
        try {
          const descriptor = await uploadToField({
            monitoring,
            nationalId,
            fieldKey: field.key,
            file,
            onProgress: (percent) =>
              setProgress((current) => ({ ...current, [file.name]: percent })),
          });
          onChange([...value, descriptor]);
        } catch {
          // Django's message is the useful one, but it is not localized; show
          // a translated line and let the console log carry the detail.
          setError(t("entryForm.uploadFailed", { name: file.name }));
        } finally {
          setProgress((current) => {
            const next = { ...current };
            delete next[file.name];
            return next;
          });
        }
      }
    },
    [field.key, monitoring, nationalId, onChange, t, value]
  );

  return (
    <div className="space-y-2">
      <Label htmlFor={`field-${field.key}`}>
        {labelOf(field, locale)}
        {field.required ? (
          <span className="text-destructive ms-1" aria-hidden>*</span>
        ) : null}
      </Label>

      <Input
        id={`field-${field.key}`}
        type="file"
        multiple={field.multiple}
        accept={field.accept?.join(",")}
        onChange={onPick}
        disabled={disabled || atCapacity}
      />

      {field.max_size_mb ? (
        <p className="text-muted-foreground text-xs">
          {t("entryForm.maxSize", {
            size: localeDigits(String(field.max_size_mb), locale),
          })}
        </p>
      ) : null}

      {error ? (
        <p className="text-destructive text-xs" role="alert">{error}</p>
      ) : null}

      <ul className="space-y-1">
        {value.map((descriptor) => (
          <li
            key={descriptor.key}
            className="flex items-center justify-between gap-2 text-sm"
          >
            <span className="truncate">{descriptor.original_name}</span>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("entryForm.removeFile", {
                name: descriptor.original_name,
              })}
              disabled={disabled}
              onClick={() =>
                onChange(value.filter((item) => item.key !== descriptor.key))
              }
            >
              <Trash2 className="size-4" />
            </Button>
          </li>
        ))}
        {Object.entries(progress).map(([name, percent]) => (
          <li key={name} className="text-muted-foreground text-sm">
            {name} — {localeDigits(String(percent), locale)}%
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [x] **Step 2: Write the form**

`_entry-form/entry-form.tsx`:

```tsx
"use client";

import * as React from "react";
import { useLocale, useTranslations } from "next-intl";
import { digitsFaToEn } from "@persian-tools/persian-tools";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";
import type { MonitoringType } from "@/data/monitoring-type/types";
import {
  fileFieldsOf,
  labelOf,
  type FileDescriptor,
} from "@/data/patient-entry/types";
import { useList_PatientEntry_API } from "@/data/patient-entry/api/list";
import { useCreate_PatientEntry_API } from "@/data/patient-entry/api/create";
import { useUpdate_PatientEntry_API } from "@/data/patient-entry/api/update";
import { FileField } from "./file-field";

export type EntryFormProps = {
  monitoring: number;
  type: MonitoringType;
};

export function EntryForm({ monitoring, type }: EntryFormProps) {
  const t = useTranslations("/console/saderat-bank-health-monitoring");
  const locale = useLocale();

  const [nationalId, setNationalId] = React.useState("");
  const [files, setFiles] = React.useState<FileDescriptor[]>([]);

  const fields = React.useMemo(() => fileFieldsOf(type), [type]);

  // Folded before it is used as a key, exactly as Django folds it, so a
  // Persian-keyboard entry finds the row an ASCII one created.
  const folded = digitsFaToEn(nationalId).trim();

  const existing = useList_PatientEntry_API({
    monitoring,
    nationalId: folded.length > 0 ? folded : undefined,
  });
  const entry = existing.data?.[0];

  // The server is the source of truth for what is already stored; adopt its
  // file list whenever a different patient is looked up.
  React.useEffect(() => {
    setFiles(
      (entry?.files ?? []).map((file) => ({
        field_key: file.field_key,
        key: file.key,
        original_name: file.original_name,
        content_type: file.content_type,
        size: file.size,
      }))
    );
  }, [entry]);

  const create = useCreate_PatientEntry_API();
  const update = useUpdate_PatientEntry_API();
  const saving = create.isPending || update.isPending;

  const onSave = React.useCallback(() => {
    const onDone = {
      onSuccess: () => {
        toast.success(t("entryForm.saved"));
        existing.refetch();
      },
      onError: () => toast.error(t("entryForm.saveFailed")),
    };

    if (entry) {
      update.mutate({ id: entry.id, payload: { files } }, onDone);
    } else {
      create.mutate(
        { payload: { monitoring, national_id: folded, files } },
        onDone
      );
    }
  }, [create, entry, existing, files, folded, monitoring, t, update]);

  if (fields.length === 0) {
    // A type with no declared fields is an ordinary state, not a failure.
    return (
      <p className="text-muted-foreground text-sm">{t("entryForm.noFields")}</p>
    );
  }

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Label htmlFor="entry-national-id">{t("entryForm.nationalId")}</Label>
        <Input
          id="entry-national-id"
          inputMode="numeric"
          value={nationalId}
          placeholder={t("entryForm.nationalIdPlaceholder")}
          onChange={(event) => setNationalId(event.target.value)}
        />
      </div>

      {folded.length > 0
        ? fields.map((field) => (
            <FileField
              key={field.key}
              field={field}
              monitoring={monitoring}
              nationalId={folded}
              disabled={saving}
              value={files.filter((file) => file.field_key === field.key)}
              onChange={(next) =>
                setFiles([
                  ...files.filter((file) => file.field_key !== field.key),
                  ...next,
                ])
              }
            />
          ))
        : null}

      <Button
        type="button"
        onClick={onSave}
        disabled={saving || folded.length === 0}
      >
        {t("entryForm.save")}
      </Button>

      <p className="sr-only">
        {fields.map((field) => labelOf(field, locale)).join(", ")}
      </p>
    </div>
  );
}
```

**Note on the `useEffect`:** `react-hooks/set-state-in-effect` is one of the
baseline error rules. Adopting server state into local state after a fetch is
the pattern the rule warns about, so if it fires here, hoist the file list into
a `key`ed remount (`<FileField key={entry?.id} .../>`) rather than suppressing
the rule or letting the error count grow past 33.

`_entry-form/index.ts`:

```ts
export { EntryForm } from "./entry-form";
export { FileField } from "./file-field";
```

- [x] **Step 3: Add the messages**

Append to the `/console/saderat-bank-health-monitoring` namespace in **both**
`messages/en.json` and `messages/fa.json`, at exact key parity:

```
entryForm.nationalId, entryForm.nationalIdPlaceholder, entryForm.save,
entryForm.saved, entryForm.saveFailed, entryForm.uploadFailed,
entryForm.maxSize, entryForm.removeFile, entryForm.noFields,
entryForm.alreadyExists
```

- [x] **Step 4: Verify parity**

```bash
cd dashboard-nextjs && node -e "
const en=require('./messages/en.json'),fa=require('./messages/fa.json');
const keys=o=>Object.entries(o).flatMap(([k,v])=>typeof v==='object'&&v?keys(v).map(s=>k+'.'+s):[k]);
const a=keys(en),b=keys(fa);
console.log('en',a.length,'fa',b.length);
console.log('missing in fa:',a.filter(k=>!b.includes(k)));
console.log('missing in en:',b.filter(k=>!a.includes(k)));
"
```

Expected: equal counts, both lists empty.

- [x] **Step 5: Verify build, types and lint**

```bash
cd dashboard-nextjs && npm run build && npx tsc --noEmit && npm run lint 2>&1 | tail -3
```

Expected: build and tsc exit 0; lint reports **33 errors, 29 warnings**.

- [x] **Step 6: Commit**

```bash
git add src/app messages/
git commit -m "Render an upload form from a monitoring type's field_schema"
```

---

### Task 10: End-to-end verification

No new code. This is the step that decides whether approach B survives.

- [ ] **Step 1: Confirm the S3 answers**

Requires the bucket name, credentials and CORS from the user. From a machine
that can reach Arvan:

```bash
cd dashboard-django && DJANGO_SETTINGS_MODULE=medicaldashboard.settings.development \
  ./venv/bin/python -c "
from saderatBankHealthMonitoring import s3
print(s3.client().head_bucket(Bucket=__import__('django.conf', fromlist=['settings']).settings.S3_BUCKET))
"
```

- [ ] **Step 2: Give a type a schema**

Through Django admin or DRF, set `field_schema` on a `MonitoringType` to the
two-field MRI/XMS document in the spec.

- [ ] **Step 3: Walk the form in `/fa`**

Upload one file and several files, exceed `max_count`, pick a disallowed MIME
type, and re-open the patient to confirm the files come back with working read
URLs. Check that labels render in Persian and the layout is correct RTL.

- [ ] **Step 4: Walk the same flow in `/en`**

- [ ] **Step 5: Confirm the Excel path is untouched**

Upload an Excel to the same monitoring type that now has a `field_schema`, and
confirm it succeeds exactly as before and that existing entries are unaffected.

- [ ] **Step 6: Record the outcome**

If browser→S3 failed, record it in the spec's "Known risk" section and open the
approach-C change: a Django upload view calling into `s3.py`, and a rewritten
`upload.ts`. Nothing else should need to move.

---

## Deployment notes (not part of slice 1's tasks)

Before this reaches production, `mainreport-platform/deploy.sh` needs the new
keys added to `ENV_KEYS_DJANGO`:

```
S3_ENDPOINT_URL S3_ACCESS_KEY S3_SECRET_KEY S3_BUCKET S3_REGION S3_ADDRESSING
```

and the matching GitHub secrets set. `gh secret set` is a blocked action for
the agent, so the user runs those. The bucket also needs a CORS rule allowing
`PUT` from `http://mainreport.ir`.

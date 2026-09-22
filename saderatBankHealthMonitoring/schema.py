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
            f'{where}.key must match {KEY_PATTERN.pattern} (got {key!r}).'
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
        if candidate.endswith('/*') and actual.startswith(candidate[:-1]):
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

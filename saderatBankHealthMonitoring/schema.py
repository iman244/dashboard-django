"""Validation of a MonitoringType.field_schema document.

Pure functions: no Django models, no database, no I/O. The schema is data an
administrator built in the dashboard's form builder, so every message here is
read by a human trying to fix their own form -- say what is wrong and where,
not merely that something is.

Two field types exist:

`digit_string` -- digits only, but stored and compared as a **string**. A
national code is 0012345678, and as a number that is 12345678: a different,
wrong value. Nothing in this module may ever coerce one to an int.

`image` -- one or many images. `accept` is not configurable; the type already
says what it takes, and asking an administrator to type MIME patterns invites
mistakes that only surface at upload time.
"""
import re

from django.core.exceptions import ValidationError

SCHEMA_VERSION = 1

# `national_id` identifies the entry; it is a column, not a field someone
# declares. Allowing it would produce two competing sources for one value.
RESERVED_KEYS = frozenset({'national_id'})

DIGIT_STRING = 'digit_string'
IMAGE = 'image'
SUPPORTED_TYPES = frozenset({DIGIT_STRING, IMAGE})

# What an `image` field accepts. Fixed, not author-supplied.
IMAGE_ACCEPT = ('image/jpeg', 'image/png', 'image/webp', 'image/gif')

# A JSONB object key and a `field_key` column value. Keeping it to this shape
# avoids the accessor-string problems that dotted keys already cause the
# dashboard's tables.
KEY_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')

DIGITS = re.compile(r'^[0-9]+$')


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

    section_keys = _validate_sections(document.get('sections', []))

    fields = document.get('fields', [])
    if not isinstance(fields, list):
        raise ValidationError('field_schema.fields must be a list.')

    seen = set()
    for index, field in enumerate(fields):
        _validate_field(field, index, seen, section_keys)

    return None


def _validate_sections(sections):
    if not isinstance(sections, list):
        raise ValidationError('field_schema.sections must be a list.')

    keys = set()
    for index, section in enumerate(sections):
        where = f'sections[{index}]'
        if not isinstance(section, dict):
            raise ValidationError(f'{where} must be an object.')

        key = section.get('key')
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            raise ValidationError(
                f'{where}.key must match {KEY_PATTERN.pattern} (got {key!r}).')
        if key in keys:
            raise ValidationError(f'duplicate section key {key!r}.')
        keys.add(key)

        for title in ('title_en', 'title_fa'):
            value = section.get(title)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    f'{where}.{title} is required and must not be empty.')

    return keys


def _validate_field(field, index, seen, section_keys):
    where = f'fields[{index}]'

    if not isinstance(field, dict):
        raise ValidationError(f'{where} must be an object.')

    key = field.get('key')
    if not isinstance(key, str) or not KEY_PATTERN.match(key):
        raise ValidationError(
            f'{where}.key must match {KEY_PATTERN.pattern} (got {key!r}).')
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

    section = field.get('section')
    if section is not None:
        if section not in section_keys:
            raise ValidationError(
                f'{where}.section {section!r} is not a declared section.')

    if field_type == DIGIT_STRING:
        _validate_digit_string_constraints(field, where)
    else:
        _validate_image_constraints(field, where)


def _positive_int(field, name, where):
    value = field.get(name)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError(
            f'{where}.{name} must be a positive integer (got {value!r}).')
    return value


def _validate_digit_string_constraints(field, where):
    minimum = _positive_int(field, 'min_length', where)
    maximum = _positive_int(field, 'max_length', where)
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValidationError(
            f'{where}.min_length ({minimum}) exceeds max_length ({maximum}).')


def _validate_image_constraints(field, where):
    _positive_int(field, 'max_size_mb', where)
    max_count = _positive_int(field, 'max_count', where)
    # `multiple` defaults to True for images: an operator photographing a scan
    # usually has several frames, and being forced to pick one is the common
    # complaint. A single-image field must say so explicitly.
    if max_count is not None and not is_multiple(field) and max_count > 1:
        raise ValidationError(
            f'{where}.max_count is {max_count} but multiple is false.')


def is_multiple(field):
    """Whether this field holds many values. Images default to many."""
    default = field.get('type') == IMAGE
    value = field.get('multiple', default)
    return bool(value)


def sections(document):
    """The declared sections, in order."""
    if not isinstance(document, dict):
        return []
    return [s for s in document.get('sections', []) if isinstance(s, dict)]


def fields_of_type(document, field_type):
    """The declarations of one type, in order."""
    if not isinstance(document, dict):
        return []
    return [
        field for field in document.get('fields', [])
        if isinstance(field, dict) and field.get('type') == field_type
    ]


def image_fields(document):
    return fields_of_type(document, IMAGE)


def digit_string_fields(document):
    return fields_of_type(document, DIGIT_STRING)


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
    if field.get('type') != IMAGE:
        raise ValidationError(
            f'{field.get("key")!r} does not take uploads.')

    if not mime_matches(IMAGE_ACCEPT, content_type):
        raise ValidationError(
            f'{content_type!r} is not an accepted image type; allowed: '
            f'{", ".join(IMAGE_ACCEPT)}.'
        )

    max_size_mb = field.get('max_size_mb')
    if max_size_mb is not None and size > max_size_mb * 1024 * 1024:
        raise ValidationError(
            f'File is {size} bytes; {field["key"]!r} allows at most '
            f'{max_size_mb} MB.'
        )


def validate_values(document, values):
    """Raise ValidationError unless `values` satisfies the digit_string fields.

    Only `digit_string` lives in `values`; images are rows, not JSON. Unknown
    keys are rejected rather than ignored, so a renamed field surfaces as an
    error instead of silently orphaning what an operator typed.
    """
    if not isinstance(values, dict):
        raise ValidationError('values must be an object.')

    declared = {field['key']: field for field in digit_string_fields(document)}

    unknown = set(values) - set(declared)
    if unknown:
        raise ValidationError(
            f'values contains undeclared field(s): '
            f'{", ".join(sorted(unknown))}.')

    for key, field in declared.items():
        present = key in values and values[key] not in (None, '')
        if not present:
            if field.get('required'):
                raise ValidationError(f'{key!r} is required.')
            continue

        value = values[key]
        # Never int(): '0012345678' is not 12345678, and a leading zero is
        # part of the value for every identifier this form collects.
        if not isinstance(value, str):
            raise ValidationError(
                f'{key!r} must be a string of digits, not {type(value).__name__}.')
        if not DIGITS.match(value):
            raise ValidationError(f'{key!r} accepts digits only.')

        minimum = field.get('min_length')
        if minimum is not None and len(value) < minimum:
            raise ValidationError(
                f'{key!r} must be at least {minimum} digits.')
        maximum = field.get('max_length')
        if maximum is not None and len(value) > maximum:
            raise ValidationError(
                f'{key!r} must be at most {maximum} digits.')

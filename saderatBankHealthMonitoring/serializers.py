import pandas as pd
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator
from .models import (
    MonitoringType,
    PatientEntry,
    PatientEntryFile,
    SaderatBankHealthMonitoring,
)
from .national_id import normalize_national_id
from .s3 import S3Unavailable, head_object, presign_get
from drf_spectacular.utils import extend_schema_field

from .schema import (
    IMAGE,
    check_upload,
    find_field,
    image_fields,
    is_multiple,
    validate_values,
)


class MonitoringTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = MonitoringType
        fields = ['id', 'slug', 'name_en', 'name_fa', 'field_schema']


def monitoring_type_field():
    """`type` on a monitoring, as the slug string the dashboard reads.

    Declared on every monitoring serializer rather than left to ModelSerializer,
    which would render the foreign key as its integer id and break the Next.js
    client's `type === 'step_2'` checks. Keeping it in one place means a new
    serializer cannot quietly regress that.
    """
    return serializers.SlugRelatedField(
        slug_field='slug',
        queryset=MonitoringType.objects.all(),
    )


class SaderatBankHealthMonitoringListSerializer(serializers.ModelSerializer):
    type = monitoring_type_field()

    class Meta:
        model = SaderatBankHealthMonitoring
        fields = ['id', 'name', 'type', 'created_at']


class SaderatBankHealthMonitoringRetrieveSerializer(
        serializers.ModelSerializer):
    type = monitoring_type_field()

    class Meta:
        model = SaderatBankHealthMonitoring
        fields = "__all__"


class SaderatBankHealthMonitoringUploadExcelSerializer(
        serializers.Serializer):
    name = serializers.CharField()
    type = monitoring_type_field()
    file = serializers.FileField()

    class Meta:
        validators = [
            UniqueTogetherValidator(
                queryset=SaderatBankHealthMonitoring.objects.all(),
                fields=('name', 'type'),
            ),
        ]

    def create(self, validated_data):
        name = validated_data['name']
        type = validated_data['type']
        file = validated_data['file']

        try:
            string_columns = {
                'personel.کد ملی': str,
                'تجمیع نتایج.کد ملی': str
            }

            df = pd.read_excel(file, dtype=string_columns)

            df = df.astype(object).where(pd.notnull(df), None)
            json_data = df.to_dict(orient="records")

        except Exception as e:
            raise serializers.ValidationError(
                f'Error reading Excel file: {str(e)}')

        instance = SaderatBankHealthMonitoring.objects.create(
            name=name,
            type=type,
            json=json_data
        )

        return instance


class PresignRequestSerializer(serializers.Serializer):
    """What the browser must state before Django will sign anything."""

    monitoring = serializers.PrimaryKeyRelatedField(
        queryset=MonitoringType.objects.all())
    national_id = serializers.CharField(max_length=10)
    field_key = serializers.CharField(max_length=64)
    filename = serializers.CharField(max_length=255)
    content_type = serializers.CharField(max_length=127)
    size = serializers.IntegerField(min_value=1)

    def validate_national_id(self, value):
        return normalize_national_id(value)


class PatientEntryFileSerializer(serializers.ModelSerializer):
    """A stored object. `url` is minted per read and expires."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = PatientEntryFile
        fields = ['id', 'field_key', 'key', 'original_name',
                  'content_type', 'size', 'url']
        read_only_fields = ['id', 'url']
        # No UniqueValidator on `key`. The model's unique=True would add one,
        # and it rejects an edit that sends back an image the record already
        # has -- which is every edit that keeps its images. Uniqueness is
        # still enforced by the database, and PatientEntrySerializer.validate
        # refuses keys that were not uploaded for this record.
        extra_kwargs = {'key': {'validators': []}}

    # Without this the generated client types `url` as a plain string, but a
    # read with storage unreachable returns null.
    @extend_schema_field(serializers.CharField(allow_null=True))
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
        # DRF would generate a UniqueTogetherValidator from the model's
        # UniqueConstraint. Two reasons not to let it: it reports 400, where
        # this API promises 409 carrying the existing entry's id so the client
        # can offer to open it; and it SELECTs before inserting, which is the
        # check-then-insert race the constraint exists to close. The database
        # is the guard -- the view turns its IntegrityError into the 409.
        validators = []

    def validate_national_id(self, value):
        return normalize_national_id(value)

    def validate(self, attrs):
        """Check values and files against the type's schema and the bucket."""
        # Who and what a record is about is fixed once it exists. Moving it
        # would carry its images -- stored under the old patient's folder --
        # to another patient, and its values to a schema they were never
        # checked against. Resending the same identity is harmless.
        if self.instance is not None:
            for name in ('national_id', 'monitoring'):
                if name in attrs and attrs[name] != getattr(
                        self.instance, name):
                    raise serializers.ValidationError(
                        {name: ['Cannot be changed once the record exists.']})

        monitoring = attrs.get('monitoring') or getattr(
            self.instance, 'monitoring', None)
        schema_document = monitoring.field_schema

        # digit_string values live in `values`; images are rows. Validated on
        # create, and on any update that supplies them.
        if 'values' in attrs or self.instance is None:
            try:
                validate_values(schema_document, attrs.get('values') or {})
            except DjangoValidationError as error:
                raise serializers.ValidationError(
                    {'values': list(error.messages)})

        # An update that leaves `files` out keeps the images it has, so there
        # is nothing to check. A create without files is checked as an empty
        # list, or a required image could be skipped by omitting the key.
        files = attrs.get('files')
        if files is None and self.instance is not None:
            return attrs
        files = files or []

        # Images already on this record were verified when they were attached.
        # Checking them again on every edit made fixing a typo depend on
        # storage being reachable, and proved nothing new.
        attached = (
            set(self.instance.files.values_list('key', flat=True))
            if self.instance is not None else set()
        )
        national_id = attrs.get('national_id') or getattr(
            self.instance, 'national_id', '')

        counts = {}
        seen = set()
        for descriptor in files:
            # Listed twice, the second insert would hit the unique key and
            # surface as "this patient already has an entry" -- a 409 naming
            # no entry. Refuse it here, as the field error it is.
            if descriptor['key'] in seen:
                raise serializers.ValidationError(
                    {'files': [f'{descriptor["key"]!r} is listed more than '
                               f'once.']})
            seen.add(descriptor['key'])

            field_key = descriptor['field_key']
            field = find_field(schema_document, field_key)
            if field is None or field.get('type') != IMAGE:
                raise serializers.ValidationError(
                    {'files': [f'{field_key!r} is not an image field of '
                               f'{monitoring.slug!r}.']})

            counts[field_key] = counts.get(field_key, 0) + 1
            max_count = field.get('max_count')
            if max_count is not None and counts[field_key] > max_count:
                raise serializers.ValidationError(
                    {'files': [f'{field_key!r} accepts at most {max_count} '
                               f'file(s).']})
            if not is_multiple(field) and counts[field_key] > 1:
                raise serializers.ValidationError(
                    {'files': [f'{field_key!r} accepts one image.']})

            if descriptor['key'] in attached:
                continue

            # A new image must be one signed for THIS record. Presign files
            # every upload under entries/<monitoring>/<national id>/<field>/,
            # so a key from anywhere else is another patient's image being
            # claimed -- the size check alone would happily accept it.
            expected = (
                f'entries/{monitoring.id}/{national_id}/{field_key}/')
            if not descriptor['key'].startswith(expected):
                raise serializers.ValidationError(
                    {'files': [f'{descriptor["key"]!r} was not uploaded for '
                               f'this record.']})

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

        # The submitted list replaces the record's images, so it must still
        # hold at least one for every required image field.
        missing = [field['key'] for field in image_fields(schema_document)
                   if field.get('required') and not counts.get(field['key'])]
        if missing:
            raise serializers.ValidationError(
                {'files': [f'{key!r} needs at least one image.'
                           for key in missing]})

        return attrs

    # Atomic, because replacing the image set is delete-then-insert: without
    # a transaction, an insert that fails leaves the record with no images.
    @transaction.atomic
    def create(self, validated_data):
        files = validated_data.pop('files', [])
        entry = PatientEntry.objects.create(**validated_data)
        self._replace_files(entry, files)
        return entry

    @transaction.atomic
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


class PatientRecordSerializer(serializers.ModelSerializer):
    """One of a patient's entries, with the monitoring it belongs to.

    Read-only, and self-contained on purpose: the patient portal cannot read
    monitoring types (that endpoint needs a sign-in), so each record carries
    the names and field_schema needed to label and order what it holds.
    """

    monitoring = MonitoringTypeSerializer(read_only=True)
    files = PatientEntryFileSerializer(many=True, read_only=True)

    class Meta:
        model = PatientEntry
        fields = ['id', 'monitoring', 'national_id', 'values', 'files',
                  'updated_at']
        read_only_fields = fields

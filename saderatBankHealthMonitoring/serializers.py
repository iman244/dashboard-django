import pandas as pd
from django.core.exceptions import ValidationError as DjangoValidationError
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
from .schema import check_upload, find_field


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
        queryset=SaderatBankHealthMonitoring.objects.all())
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

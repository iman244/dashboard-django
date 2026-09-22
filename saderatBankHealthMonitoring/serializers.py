import pandas as pd
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator
from .models import MonitoringType, SaderatBankHealthMonitoring
from .national_id import normalize_national_id
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

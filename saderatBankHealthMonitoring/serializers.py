import json
import pandas as pd
import numpy as np
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator
from .models import SaderatBankHealthMonitoring

class SaderatBankHealthMonitoringListSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaderatBankHealthMonitoring
        fields = ['id', 'name', 'type', 'created_at']
        
class SaderatBankHealthMonitoringRetrieveSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaderatBankHealthMonitoring
        fields = "__all__"
        
class SaderatBankHealthMonitoringUploadExcelSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.ChoiceField(
        choices=SaderatBankHealthMonitoring.Type.choices)
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
            raise serializers.ValidationError(f'Error reading Excel file: {str(e)}')
        
        instance = SaderatBankHealthMonitoring.objects.create(
            name=name,
            type=type,
            json=json_data
        )
        
        return instance
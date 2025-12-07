import json
import pandas as pd
import numpy as np
from rest_framework import serializers
from .models import SaderatBankHealthMonitoring

class SaderatBankHealthMonitoringListSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaderatBankHealthMonitoring
        fields = ['id', 'name', 'created_at']
        
class SaderatBankHealthMonitoringRetrieveSerializer(serializers.ModelSerializer):
    class Meta:
        model = SaderatBankHealthMonitoring
        fields = "__all__"
        
class SaderatBankHealthMonitoringUploadExcelSerializer(serializers.Serializer):
    name = serializers.CharField()
    file = serializers.FileField()
    
    def create(self, validated_data):
        name = validated_data['name']
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
            json=json_data
        )
        
        return instance
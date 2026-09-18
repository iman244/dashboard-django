from django.contrib import admin
from .models import MonitoringType, SaderatBankHealthMonitoring


@admin.register(MonitoringType)
class MonitoringTypeAdmin(admin.ModelAdmin):
    list_display = ('slug', 'name_en', 'name_fa')


@admin.register(SaderatBankHealthMonitoring)
class SaderatBankHealthMonitoringAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'created_at')
    list_select_related = ('type',)

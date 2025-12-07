from django.contrib import admin
from .models import SaderatBankHealthMonitoring

@admin.register(SaderatBankHealthMonitoring)
class SaderatBankHealthMonitoringAdmin(admin.ModelAdmin):
    pass
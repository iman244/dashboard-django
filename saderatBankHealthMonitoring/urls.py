from django.urls import path, include
from .views import (
    MonitoringTypeViewSet,
    PatientEntryViewSet,
    SaderatBankHealthMonitoringViewSet,
)
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'monitorings', SaderatBankHealthMonitoringViewSet, basename='monitorings')
router.register(r'monitoring-types', MonitoringTypeViewSet, basename='monitoring-types')
router.register(r'patient-entries', PatientEntryViewSet, basename='patient-entries')

urlpatterns = [
    path('', include(router.urls)),
]

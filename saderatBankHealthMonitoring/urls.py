from django.urls import path, include
from .views import (
    MonitoringTypeViewSet,
    PatientEntryViewSet,
    PatientRecordsView,
    PersonReportsView,
    OwnPatientRecordsView,
    SaderatBankHealthMonitoringViewSet,
)
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'monitorings', SaderatBankHealthMonitoringViewSet, basename='monitorings')
router.register(r'monitoring-types', MonitoringTypeViewSet, basename='monitoring-types')
router.register(r'patient-entries', PatientEntryViewSet, basename='patient-entries')

urlpatterns = [
    path('patient-records/me/', OwnPatientRecordsView.as_view(), name='patient-records-me'),
    path('patient-records/', PatientRecordsView.as_view(),
         name='patient-records'),
    path('person-reports/', PersonReportsView.as_view(),
         name='person-reports'),
    path('', include(router.urls)),
]

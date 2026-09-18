from django.urls import path, include
from .views import MonitoringTypeViewSet, SaderatBankHealthMonitoringViewSet
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'monitorings', SaderatBankHealthMonitoringViewSet, basename='monitorings')
router.register(r'monitoring-types', MonitoringTypeViewSet, basename='monitoring-types')

urlpatterns = [
    path('', include(router.urls)),
]

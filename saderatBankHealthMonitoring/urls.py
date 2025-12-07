from django.urls import path, include
from .views import SaderatBankHealthMonitoringViewSet
from rest_framework.routers import DefaultRouter

router = DefaultRouter()
router.register(r'monitorings', SaderatBankHealthMonitoringViewSet, basename='monitorings')

urlpatterns = [
    path('', include(router.urls)),
]
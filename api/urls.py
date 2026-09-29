from django.urls import re_path, include, path
from .views import PatientTokenView

urlpatterns = [
    path('auth/patient/jwt/create/', PatientTokenView.as_view(), name='patient-token-create'),
    re_path(r'^auth/', include('djoser.urls')),
    re_path(r'^auth/', include('djoser.urls.jwt')),
    re_path(r'^saderat-bank-health-monitoring/', include('saderatBankHealthMonitoring.urls')),
]

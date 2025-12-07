from django.urls import re_path, include

urlpatterns = [
    re_path(r'^auth/', include('djoser.urls')),
    re_path(r'^auth/', include('djoser.urls.jwt')),
    re_path(r'^saderat-bank-health-monitoring/', include('saderatBankHealthMonitoring.urls')),
]

from django.contrib.auth import get_user_model
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenViewBase

from saderatBankHealthMonitoring.models import PatientIdentity
from saderatBankHealthMonitoring.national_id import normalize_national_id


class PatientLoginThrottle(AnonRateThrottle):
    rate = '10/min'
    scope = 'patient_login'


class PatientTokenSerializer(serializers.Serializer):
    national_id = serializers.CharField(write_only=True, max_length=32)
    password = serializers.CharField(write_only=True, trim_whitespace=False)
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)

    def validate(self, attrs):
        identity = PatientIdentity.objects.select_related('user').filter(
            national_id=normalize_national_id(attrs['national_id'])).first()
        user = identity.user if identity else None
        if user is None:
            # Match password hashing work for unknown IDs to reduce enumeration.
            get_user_model()().set_password(attrs['password'])
            valid = False
        else:
            valid = user.check_password(attrs['password'])
        if not valid or not user.is_active or user.is_staff:
            raise AuthenticationFailed('No active account found with the given credentials')
        refresh = RefreshToken.for_user(user)
        return {'access': str(refresh.access_token), 'refresh': str(refresh)}


class PatientTokenView(TokenViewBase):
    serializer_class = PatientTokenSerializer
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [PatientLoginThrottle]

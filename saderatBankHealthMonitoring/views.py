from django.db.models import ProtectedError
from rest_framework import permissions, status, viewsets
from .models import MonitoringType, SaderatBankHealthMonitoring
from .serializers import (
    MonitoringTypeSerializer,
    SaderatBankHealthMonitoringRetrieveSerializer,
    SaderatBankHealthMonitoringUploadExcelSerializer,
)
from .serializers import SaderatBankHealthMonitoringListSerializer
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import (
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers


class IsStaffOrReadOnly(permissions.BasePermission):
    """Any signed-in user may read; only staff may write.

    Monitoring types are referenced by every report, so editing one is an
    administrative act. `is_staff` is already on the user payload the
    dashboard receives, so the client can gate the same actions in its UI.
    """

    message = 'Only staff users may modify monitoring types.'

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        if request.method in permissions.SAFE_METHODS:
            return True
        return bool(request.user.is_staff)


@extend_schema_view(
    list=extend_schema(summary='List monitoring types'),
    retrieve=extend_schema(summary='Retrieve a monitoring type'),
    create=extend_schema(summary='Create a monitoring type'),
    update=extend_schema(summary='Replace a monitoring type'),
    partial_update=extend_schema(summary='Update a monitoring type'),
    destroy=extend_schema(
        summary='Delete a monitoring type',
        responses={
            204: OpenApiResponse(description='Deleted'),
            409: OpenApiResponse(
                description='The type is still used by monitoring reports.'),
        },
    ),
)
class MonitoringTypeViewSet(viewsets.ModelViewSet):
    queryset = MonitoringType.objects.all()
    serializer_class = MonitoringTypeSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsStaffOrReadOnly]

    def destroy(self, request, *args, **kwargs):
        # The foreign key is PROTECT, so Django refuses to collect a type that
        # reports still point at. Let it decide, rather than counting first and
        # racing a concurrent upload, and report 409 instead of a 500.
        try:
            return super().destroy(request, *args, **kwargs)
        except ProtectedError:
            monitoring_type = self.get_object()
            return Response(
                {
                    'detail': (
                        'This monitoring type is still used by '
                        'monitoring reports and cannot be deleted.'
                    ),
                    'monitorings': monitoring_type.monitorings.count(),
                },
                status=status.HTTP_409_CONFLICT,
            )


@extend_schema_view(
    list=extend_schema(summary='List monitoring reports'),
    retrieve=extend_schema(
        summary='Retrieve a monitoring report with its parsed rows'),
)
class SaderatBankHealthMonitoringViewSet(viewsets.ModelViewSet):
    queryset = SaderatBankHealthMonitoring.objects.all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return SaderatBankHealthMonitoringRetrieveSerializer
        return SaderatBankHealthMonitoringListSerializer

    # get_serializer_class() above returns the list serializer for every action
    # other than retrieve, so the schema for this endpoint has to be declared.
    @extend_schema(
        summary='Upload an Excel report',
        request={
            'multipart/form-data':
                SaderatBankHealthMonitoringUploadExcelSerializer,
        },
        responses={
            200: inline_serializer(
                name='UploadExcelResponse',
                fields={'message': drf_serializers.CharField()},
            ),
        },
    )
    @action(detail=False, methods=['post'])
    def upload_excel(self, request):
        serializer = SaderatBankHealthMonitoringUploadExcelSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': 'Excel uploaded successfully'})

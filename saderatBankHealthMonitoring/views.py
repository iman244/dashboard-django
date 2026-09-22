from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import ProtectedError
from rest_framework import permissions, status, viewsets
from .models import MonitoringType, PatientEntry, SaderatBankHealthMonitoring
from .s3 import S3Unavailable, build_key, presign_put
from .schema import check_upload, find_field
from .serializers import (
    MonitoringTypeSerializer,
    SaderatBankHealthMonitoringRetrieveSerializer,
    SaderatBankHealthMonitoringUploadExcelSerializer,
)
from .serializers import (
    PresignRequestSerializer,
    SaderatBankHealthMonitoringListSerializer,
)
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


class PatientEntryViewSet(viewsets.ModelViewSet):
    """Per-patient entries against a monitoring's field_schema.

    Nothing here reads `SaderatBankHealthMonitoring.json`. Entries and the
    Excel blob are independent stores that share a key.
    """

    queryset = PatientEntry.objects.prefetch_related('files')
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary='Sign an upload for one file field',
        request=PresignRequestSerializer,
        responses={
            200: inline_serializer(
                name='PresignResponse',
                fields={
                    'upload_url': drf_serializers.CharField(),
                    'key': drf_serializers.CharField(),
                    'headers': drf_serializers.DictField(),
                    'expires_in': drf_serializers.IntegerField(),
                },
            ),
            400: OpenApiResponse(
                description='The schema does not permit this upload.'),
            503: OpenApiResponse(description='Object storage unavailable.'),
        },
    )
    @action(detail=False, methods=['post'])
    def presign(self, request):
        serializer = PresignRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        monitoring = data['monitoring']
        field = find_field(monitoring.type.field_schema, data['field_key'])
        if field is None or field.get('type') != 'file':
            raise drf_serializers.ValidationError(
                {'field_key': [
                    f'{data["field_key"]!r} is not a file field of '
                    f'{monitoring.type.slug!r}.']})

        try:
            check_upload(field, data['content_type'], data['size'])
        except DjangoValidationError as error:
            raise drf_serializers.ValidationError({'file': list(error.messages)})

        key = build_key(
            monitoring.id, data['national_id'],
            data['field_key'], data['filename'])

        try:
            signed = presign_put(key, data['content_type'])
        except S3Unavailable as error:
            return Response(
                {'detail': f'Object storage is unavailable: {error}'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        return Response({
            'upload_url': signed['url'],
            'key': key,
            'headers': signed['headers'],
            'expires_in': signed['expires_in'],
        })

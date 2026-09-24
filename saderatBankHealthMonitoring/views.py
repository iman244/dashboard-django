from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from rest_framework import generics, permissions, status, viewsets
from .models import MonitoringType, PatientEntry, SaderatBankHealthMonitoring
from .s3 import S3Unavailable, build_key, presign_put
from .schema import IMAGE, check_upload, find_field
from .serializers import (
    MonitoringTypeSerializer,
    SaderatBankHealthMonitoringRetrieveSerializer,
    SaderatBankHealthMonitoringUploadExcelSerializer,
)
from .national_id import normalize_national_id
from .serializers import (
    PatientEntrySerializer,
    PatientRecordSerializer,
    PresignRequestSerializer,
    SaderatBankHealthMonitoringListSerializer,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import AnonRateThrottle
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers as drf_serializers


class IsStaffOrReadOnly(permissions.BasePermission):
    """Any signed-in user may read; only staff may write.

    Used for monitoring types, which every report references, and for patient
    records and their uploads. `is_staff` is already on the user payload the
    dashboard receives, so the client can gate the same actions in its UI.
    """

    message = 'Only staff users may make changes.'

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


@extend_schema_view(
    list=extend_schema(summary='List patient entries'),
    retrieve=extend_schema(summary='Retrieve a patient entry'),
    create=extend_schema(
        summary='Create a patient entry',
        responses={
            201: PatientEntrySerializer,
            409: OpenApiResponse(
                description='This patient already has an entry here.'),
        },
    ),
    partial_update=extend_schema(summary='Update a patient entry'),
    destroy=extend_schema(summary='Delete a patient entry'),
)
class PatientEntryViewSet(viewsets.ModelViewSet):
    """Per-patient entries against a monitoring's field_schema.

    Nothing here reads `SaderatBankHealthMonitoring.json`. Entries and the
    Excel blob are independent stores that share a key.
    """

    queryset = PatientEntry.objects.prefetch_related('files')
    serializer_class = PatientEntrySerializer
    authentication_classes = [JWTAuthentication]
    # Reading is open to every signed-in user; adding, editing, deleting and
    # asking for an upload URL (presign is a POST) are staff-only.
    permission_classes = [IsStaffOrReadOnly]

    def get_queryset(self):
        queryset = super().get_queryset()
        monitoring = self.request.query_params.get('monitoring')
        if monitoring:
            queryset = queryset.filter(monitoring_id=monitoring)
        national_id = self.request.query_params.get('national_id')
        if national_id:
            # Folded on the way in, exactly as it was folded on the way to the
            # database, or a Persian-keyboard lookup would find nothing.
            queryset = queryset.filter(
                national_id=normalize_national_id(national_id))
        return queryset

    def create(self, request, *args, **kwargs):
        # The unique constraint is the check; asking first would race a
        # concurrent create. 409 rather than 400 so the client can offer to
        # open the existing entry instead of showing a field error.
        try:
            # A savepoint, so the failed INSERT rolls back on its own and the
            # surrounding transaction stays usable -- without it the lookup
            # below raises TransactionManagementError instead of answering.
            with transaction.atomic():
                return super().create(request, *args, **kwargs)
        except IntegrityError:
            existing = PatientEntry.objects.filter(
                monitoring_id=request.data.get('monitoring'),
                national_id=normalize_national_id(
                    str(request.data.get('national_id', ''))),
            ).first()
            return Response(
                {'detail': 'This patient already has an entry for this '
                           'monitoring.',
                 'id': existing.id if existing else None},
                status=status.HTTP_409_CONFLICT,
            )

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
        field = find_field(monitoring.field_schema, data['field_key'])
        if field is None or field.get('type') != IMAGE:
            raise drf_serializers.ValidationError(
                {'field_key': [
                    f'{data["field_key"]!r} is not an image field of '
                    f'{monitoring.slug!r}.']})

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


class PatientRecordsAnonThrottle(AnonRateThrottle):
    """Caps anonymous reads per IP; signed-in users are not counted at all.

    The rate lives here rather than in settings because it is part of what
    makes this endpoint safe to leave open, not a tunable. Counts are per
    process (the default local-memory cache), so the effective ceiling is
    this times the number of gunicorn workers -- still far too slow to walk
    the national ID space.
    """

    scope = 'patient_records'
    rate = '30/min'


@extend_schema(
    summary="A patient's records across every monitoring",
    parameters=[OpenApiParameter(
        'national_id', str, required=True,
        description='Ten digits; Persian and Arabic-Indic digits accepted.')],
    responses={
        200: PatientRecordSerializer(many=True),
        400: OpenApiResponse(description='Missing or malformed national_id.'),
        429: OpenApiResponse(description='Too many anonymous requests.'),
    },
)
class PatientRecordsView(generics.ListAPIView):
    """Everything recorded for one patient, for display.

    Open to anonymous callers because the patient portal has no sign-in of
    its own (the upstream EHR it fronts takes no credentials either). The
    national ID is therefore the only key: this never answers without a
    well-formed one, and anonymous callers are throttled.
    """

    serializer_class = PatientRecordSerializer
    authentication_classes = [JWTAuthentication]
    permission_classes = [AllowAny]
    throttle_classes = [PatientRecordsAnonThrottle]
    pagination_class = None

    def list(self, request, *args, **kwargs):
        national_id = normalize_national_id(
            request.query_params.get('national_id', ''))
        if not (len(national_id) == 10 and national_id.isdigit()):
            return Response(
                {'national_id': ['A ten-digit national ID is required.']},
                status=status.HTTP_400_BAD_REQUEST)
        records = (PatientEntry.objects
                   .filter(national_id=national_id)
                   .select_related('monitoring')
                   .prefetch_related('files')
                   .order_by('monitoring__name_en'))
        return Response(self.get_serializer(records, many=True).data)

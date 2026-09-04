from django.shortcuts import render
from rest_framework import viewsets
from .models import SaderatBankHealthMonitoring
from .serializers import SaderatBankHealthMonitoringRetrieveSerializer, SaderatBankHealthMonitoringUploadExcelSerializer
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

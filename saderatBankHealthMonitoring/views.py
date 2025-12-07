from django.shortcuts import render
from rest_framework import viewsets
from .models import SaderatBankHealthMonitoring
from .serializers import SaderatBankHealthMonitoringRetrieveSerializer, SaderatBankHealthMonitoringUploadExcelSerializer
from .serializers import SaderatBankHealthMonitoringListSerializer
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework.decorators import action
from rest_framework.response import Response


class SaderatBankHealthMonitoringViewSet(viewsets.ModelViewSet):
    queryset = SaderatBankHealthMonitoring.objects.all()
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        print("self.action", self.action)
        if self.action == 'retrieve':
            return SaderatBankHealthMonitoringRetrieveSerializer
        return SaderatBankHealthMonitoringListSerializer

    @action(detail=False, methods=['post'])
    def upload_excel(self, request):
        serializer = SaderatBankHealthMonitoringUploadExcelSerializer(
            data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({'message': 'Excel uploaded successfully'})

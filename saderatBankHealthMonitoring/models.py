from django.db import models

class SaderatBankHealthMonitoring(models.Model):
    name = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    json = models.JSONField(default=dict, null=True, blank=True)
    
    def __str__(self):
        return self.name
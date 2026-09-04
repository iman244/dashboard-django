from django.db import models

class SaderatBankHealthMonitoring(models.Model):
    class Type(models.TextChoices):
        STEP_1 = 'step_1', 'Step 1'
        STEP_2 = 'step_2', 'Step 2'

    name = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=Type.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    json = models.JSONField(default=dict, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['name', 'type'],
                name='unique_monitoring_name_type',
            ),
        ]

    def __str__(self):
        return f'{self.name} ({self.get_type_display()})'

from django.db import models


class MonitoringType(models.Model):
    """A kind of monitoring report, e.g. step_1.

    This was a TextChoices enum on SaderatBankHealthMonitoring. It is a table
    so types can be managed at runtime through the API, which means `slug` is
    editable: the integer primary key is what the monitorings point at, so a
    renamed slug follows through to every report that uses it.

    `slug` is the only part of this model the monitoring API exposes — a
    monitoring still serializes its type as 'step_1', the way the Next.js
    dashboard reads it.
    """

    slug = models.SlugField(max_length=16, unique=True)
    name_en = models.CharField(max_length=64)
    name_fa = models.CharField(max_length=64)

    class Meta:
        # Creation order, so list responses are stable across requests.
        ordering = ['id']

    def __str__(self):
        return self.name_en


class SaderatBankHealthMonitoring(models.Model):
    name = models.CharField(max_length=255)
    # PROTECT, so deleting a type that still has reports fails loudly instead
    # of taking the reports with it. The viewset turns that into a 409.
    type = models.ForeignKey(
        MonitoringType,
        on_delete=models.PROTECT,
        related_name='monitorings',
    )
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
        return f'{self.name} ({self.type.name_en})'

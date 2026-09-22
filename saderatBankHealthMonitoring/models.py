from django.db import models

from .national_id import normalize_national_id
from .schema import validate_field_schema


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

    # A loose description of the fields an operator fills in per patient for
    # this kind of report. Empty means "no fields declared yet" -- NOT "this is
    # an Excel type". The two capabilities are independent: a type may have a
    # schema, accept Excel uploads, both, or neither, and nothing branches on
    # which. See docs/superpowers/specs/2026-09-22-*-design.md.
    field_schema = models.JSONField(
        default=dict, blank=True, validators=[validate_field_schema])

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


class PatientEntry(models.Model):
    """One patient's answers to a monitoring's field_schema.

    Independent of `SaderatBankHealthMonitoring.json`: an entry may exist for a
    national id the uploaded spreadsheet has never mentioned, and uploading a
    new spreadsheet never invalidates an entry. They are two stores that happen
    to share a key.

    CASCADE rather than PROTECT, unlike MonitoringType: an entry is *part of* a
    monitoring rather than a shared thing the monitoring refers to, so it has
    no meaning once the monitoring is gone.
    """

    monitoring = models.ForeignKey(
        SaderatBankHealthMonitoring,
        on_delete=models.CASCADE,
        related_name='entries',
    )
    national_id = models.CharField(max_length=10, db_index=True)
    # Where non-file field values land in slice 2. The column ships now so
    # adding string/number/date/boolean/choice needs no second migration.
    values = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['national_id']
        constraints = [
            models.UniqueConstraint(
                fields=['monitoring', 'national_id'],
                name='unique_entry_monitoring_national_id',
            ),
        ]

    def save(self, *args, **kwargs):
        # Folded here rather than only in the serializer, so an entry created
        # by a management command or the admin cannot bypass it and split one
        # patient across two rows.
        self.national_id = normalize_national_id(self.national_id)
        return super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.national_id} @ {self.monitoring.name}'


class PatientEntryFile(models.Model):
    """One uploaded object: its metadata here, its bytes in S3.

    `key` is the S3 object key rather than a FileField, because the browser
    writes the bytes directly via a presigned PUT. A FileField would imply
    Django owns that write.
    """

    entry = models.ForeignKey(
        PatientEntry, on_delete=models.CASCADE, related_name='files')
    field_key = models.CharField(max_length=64, db_index=True)
    key = models.CharField(max_length=512, unique=True)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=127)
    size = models.PositiveBigIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f'{self.field_key}: {self.original_name}'

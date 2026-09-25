"""Empty PatientEntry before its foreign key changes table.

`monitoring_id` holds a SaderatBankHealthMonitoring primary key today and a
MonitoringType one after 0007. The two sequences are unrelated, so every
existing row is either a dangling reference or -- worse -- a plausible-looking
one pointing at an unrelated monitoring. There is no correct mapping to
compute, so the rows go.

Separate from 0007 on purpose: Postgres refuses to ALTER a table that still has
pending trigger events from a DELETE in the same transaction.

Objects already in the bucket stay where they are; nothing points at them any
more. See the spec's note on orphans.
"""
from django.db import migrations


def drop_entries(apps, schema_editor):
    apps.get_model('saderatBankHealthMonitoring', 'PatientEntry') \
        .objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('saderatBankHealthMonitoring', '0005_field_schema_and_patient_entries'),
    ]

    operations = [
        migrations.RunPython(drop_entries, migrations.RunPython.noop),
    ]

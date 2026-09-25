"""A record belongs to the monitoring, not to an uploaded spreadsheet."""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('saderatBankHealthMonitoring', '0006_clear_entries_before_remapping'),
    ]

    operations = [
        migrations.AlterField(
            model_name='patiententry',
            name='monitoring',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='entries',
                to='saderatBankHealthMonitoring.monitoringtype',
            ),
        ),
    ]

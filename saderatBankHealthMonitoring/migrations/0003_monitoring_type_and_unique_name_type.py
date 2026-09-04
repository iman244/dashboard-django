from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('saderatBankHealthMonitoring', '0002_saderatbankhealthmonitoring_json'),
    ]

    operations = [
        # default is only used to backfill existing rows; preserve_default=False
        # keeps it out of the model state, so the API still requires `type`.
        migrations.AddField(
            model_name='saderatbankhealthmonitoring',
            name='type',
            field=models.CharField(
                choices=[('step_1', 'Step 1'), ('step_2', 'Step 2')],
                default='step_1',
                max_length=16,
            ),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='saderatbankhealthmonitoring',
            name='name',
            field=models.CharField(max_length=255),
        ),
        migrations.AddConstraint(
            model_name='saderatbankhealthmonitoring',
            constraint=models.UniqueConstraint(
                fields=('name', 'type'),
                name='unique_monitoring_name_type',
            ),
        ),
    ]

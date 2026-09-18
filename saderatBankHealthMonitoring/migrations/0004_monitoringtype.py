import django.db.models.deletion
from django.db import migrations, models

# The two types the TextChoices enum used to declare, with the labels the
# dashboard already shows for them (messages/{en,fa}.json -> Sbhm.Step1/Step2).
SEED_TYPES = {
    'step_1': ('Step 1', 'مرحله ۱'),
    'step_2': ('Step 2', 'مرحله ۲'),
}


def seed_and_link(apps, schema_editor):
    """Create a MonitoringType per type string in use, then point rows at it.

    Any slug found in the data but missing from SEED_TYPES still gets a row —
    a database that picked up an unexpected value must not block the upgrade.
    Such a type is named after its own slug for an operator to correct later.
    """
    MonitoringType = apps.get_model(
        'saderatBankHealthMonitoring', 'MonitoringType')
    Monitoring = apps.get_model(
        'saderatBankHealthMonitoring', 'SaderatBankHealthMonitoring')

    in_use = set(
        Monitoring.objects.values_list('type', flat=True).distinct())

    for slug in sorted(set(SEED_TYPES) | in_use):
        name_en, name_fa = SEED_TYPES.get(slug, (slug, slug))
        monitoring_type, _ = MonitoringType.objects.get_or_create(
            slug=slug,
            defaults={'name_en': name_en, 'name_fa': name_fa},
        )
        Monitoring.objects.filter(type=slug).update(type_fk=monitoring_type)


def restore_strings(apps, schema_editor):
    """Reverse half of seed_and_link: copy each slug back into `type`.

    Reverse runs operations bottom-up, so by the time this executes the `type`
    column has been re-added (nullable) and `type` is named `type_fk` again.
    It sits below the AlterField that made `type` nullable so that the column
    is full again before that AlterField reverses it back to NOT NULL.
    """
    MonitoringType = apps.get_model(
        'saderatBankHealthMonitoring', 'MonitoringType')
    Monitoring = apps.get_model(
        'saderatBankHealthMonitoring', 'SaderatBankHealthMonitoring')

    for monitoring_type in MonitoringType.objects.all():
        Monitoring.objects.filter(type_fk=monitoring_type).update(
            type=monitoring_type.slug)


class Migration(migrations.Migration):

    dependencies = [
        ('saderatBankHealthMonitoring', '0003_monitoring_type_and_unique_name_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonitoringType',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                    verbose_name='ID')),
                ('slug', models.SlugField(max_length=16, unique=True)),
                ('name_en', models.CharField(max_length=64)),
                ('name_fa', models.CharField(max_length=64)),
            ],
            options={'ordering': ['id']},
        ),
        # The constraint covers `type`, so it has to go before the column does.
        migrations.RemoveConstraint(
            model_name='saderatbankhealthmonitoring',
            name='unique_monitoring_name_type',
        ),
        # Nullable while the two columns coexist; tightened below once filled.
        migrations.AddField(
            model_name='saderatbankhealthmonitoring',
            name='type_fk',
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='monitorings',
                to='saderatBankHealthMonitoring.monitoringtype',
            ),
        ),
        migrations.RunPython(seed_and_link, migrations.RunPython.noop),
        # Reversing RemoveField below re-adds `type` as it stands here, so it
        # has to be nullable — nothing can fill it at the moment it reappears.
        migrations.AlterField(
            model_name='saderatbankhealthmonitoring',
            name='type',
            field=models.CharField(max_length=16, null=True),
        ),
        migrations.RunPython(migrations.RunPython.noop, restore_strings),
        migrations.RemoveField(
            model_name='saderatbankhealthmonitoring',
            name='type',
        ),
        migrations.RenameField(
            model_name='saderatbankhealthmonitoring',
            old_name='type_fk',
            new_name='type',
        ),
        migrations.AlterField(
            model_name='saderatbankhealthmonitoring',
            name='type',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='monitorings',
                to='saderatBankHealthMonitoring.monitoringtype',
            ),
        ),
        migrations.AddConstraint(
            model_name='saderatbankhealthmonitoring',
            constraint=models.UniqueConstraint(
                fields=('name', 'type'),
                name='unique_monitoring_name_type',
            ),
        ),
    ]

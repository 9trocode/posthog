from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("warehouse_sources", "0118_scaffold_sevalla_source")]

    operations = [
        migrations.AddField(
            model_name="externaldataschemaoomevent",
            name="self_phase",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
        migrations.AddField(
            model_name="externaldataschemaoomevent",
            name="self_report_age_seconds",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="externaldataschemaoomevent",
            name="self_peak_buffer_bytes",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="externaldataschemaoomevent",
            name="co_tenant_max_peak_buffer_bytes",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="externaldataschemaoomevent",
            name="co_tenant_report_count",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]

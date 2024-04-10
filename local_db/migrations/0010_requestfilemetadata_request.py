# Generated by Django 5.0.3 on 2024-04-04 15:41

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0009_alter_auditlog_type_alter_filereview_status_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestfilemetadata",
            name="request",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="request_files",
                to="local_db.requestmetadata",
            ),
        ),
    ]
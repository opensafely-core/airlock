# Generated by Django 5.0.3 on 2024-04-04 15:40

from django.db import migrations

import airlock.business_logic
import local_db.models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0008_alter_filereview_file"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditlog",
            name="type",
            field=local_db.models.EnumField(enum=airlock.business_logic.AuditEventType),
        ),
        migrations.AlterField(
            model_name="filereview",
            name="status",
            field=local_db.models.EnumField(
                default=airlock.business_logic.FileReviewStatus["REJECTED"],
                enum=airlock.business_logic.FileReviewStatus,
            ),
        ),
        migrations.AlterField(
            model_name="requestfilemetadata",
            name="filetype",
            field=local_db.models.EnumField(
                default=airlock.business_logic.RequestFileType["OUTPUT"],
                enum=airlock.business_logic.RequestFileType,
            ),
        ),
    ]

# Generated by Django 5.0.2 on 2024-03-18 12:58

from django.db import migrations

import airlock.enums
import local_db.models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0004_requestfilemetadata_file_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestfilemetadata",
            name="filetype",
            field=local_db.models.EnumField(
                default=airlock.enums.RequestFileType["OUTPUT"]
            ),
        ),
    ]

# Generated by Django 5.0.2 on 2024-02-13 15:47

from django.db import migrations

import airlock.business_logic
import local_db.models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestmetadata",
            name="status",
            field=local_db.models.EnumField(
                default=airlock.business_logic.Status["PENDING"]
            ),
        ),
    ]

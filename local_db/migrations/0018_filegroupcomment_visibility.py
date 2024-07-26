# Generated by Django 5.0.6 on 2024-07-08 16:56

from django.db import migrations

import airlock.business_logic
import local_db.models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0017_requestmetadata_completed_reviews"),
    ]

    operations = [
        migrations.AddField(
            model_name="filegroupcomment",
            name="visibility",
            field=local_db.models.EnumField(
                default=airlock.business_logic.Visibility.PUBLIC,
                enum=airlock.business_logic.Visibility,
            ),
            preserve_default=False,
        ),
    ]

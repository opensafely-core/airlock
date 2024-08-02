# Generated by Django 5.0.6 on 2024-07-08 16:56

from django.db import migrations

import airlock.enums
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
                default=airlock.enums.Visibility.PUBLIC,
                enum=airlock.enums.Visibility,
            ),
            preserve_default=False,
        ),
    ]

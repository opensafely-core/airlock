# Generated by Django 5.0.2 on 2024-03-19 09:50

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import airlock.enums
import local_db.models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0005_requestfilemetadata_filetype"),
    ]

    operations = [
        migrations.CreateModel(
            name="FileReview",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("reviewer", models.TextField()),
                (
                    "status",
                    local_db.models.EnumField(
                        default=airlock.enums.RequestFileVote["CHANGES_REQUESTED"]
                    ),
                ),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "file",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviews",
                        to="local_db.requestfilemetadata",
                    ),
                ),
            ],
            options={
                "unique_together": {("file", "reviewer")},
            },
        ),
    ]

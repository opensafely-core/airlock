# Generated by Django 5.1.6 on 2025-02-19 11:20

import time

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="User",
            fields=[
                ("password", models.CharField(max_length=128, verbose_name="password")),
                (
                    "last_login",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="last login"
                    ),
                ),
                ("user_id", models.TextField(primary_key=True, serialize=False)),
                ("api_data", models.JSONField(default=dict)),
                ("last_refresh", models.FloatField(default=time.time)),
            ],
            options={
                "abstract": False,
            },
        ),
    ]

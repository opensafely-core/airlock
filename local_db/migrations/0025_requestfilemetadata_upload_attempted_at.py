# Generated by Django 5.1.6 on 2025-02-19 13:48

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0024_requestfilemetadata_upload_attempts_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestfilemetadata",
            name="upload_attempted_at",
            field=models.DateTimeField(default=None, null=True),
        ),
    ]

# Generated by Django 5.0.6 on 2024-06-06 12:19

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0015_requestfilemetadata_repo"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestfilemetadata",
            name="released_at",
            field=models.DateTimeField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="requestfilemetadata",
            name="released_by",
            field=models.TextField(null=True),
        ),
    ]
# Generated by Django 5.0.2 on 2024-03-04 16:07

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0003_filegroupmetadata_requestfilemetadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="requestfilemetadata",
            name="file_id",
            field=models.TextField(default=""),
            preserve_default=False,
        ),
    ]

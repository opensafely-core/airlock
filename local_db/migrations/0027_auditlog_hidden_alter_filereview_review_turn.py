# Generated by Django 5.1.7 on 2025-03-24 11:50

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0026_filereview_review_turn"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="hidden",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="filereview",
            name="review_turn",
            field=models.IntegerField(default=0),
        ),
    ]

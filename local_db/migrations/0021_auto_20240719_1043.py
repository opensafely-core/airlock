# Generated by Django 5.0.6 on 2024-07-19 10:43

from django.db import migrations


# We've changed AuditEventType.REQUEST_FILE_REJECT to
# AuditEventType.REQUEST_FILE_REQUEST_CHANGES, which requires a change to the
# database.
#
# We'd like to have a RunPython migration like the following, but that fails
# because the type parameter is expected to be an instance of AuditEventType
#
# def update_column_values(apps, schema_editor):
#     AuditLog = apps.get_model("local_db", "AuditLog")
#     AuditLog.objects.filter(type="REQUEST_FILE_REJECT").update(
#         type="REQUEST_FILE_REQUEST_CHANGES"
#     )
#
# We could work around that by sequencing the migration across two deploys, but
# it's easier to write some SQL.

sql = "UPDATE local_db_auditlog SET type = %s WHERE type = %s"
old = "REQUEST_FILE_REJECT"
new = "REQUEST_FILE_REQUEST_CHANGES"


class Migration(migrations.Migration):
    dependencies = [
        ("local_db", "0020_requestmetadata_turn_reviewers"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[(sql, [new, old])],
            reverse_sql=[(sql, [old, new])],
        ),
    ]

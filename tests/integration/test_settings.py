import pytest
from django.db import connection, transaction
from django.test.utils import CaptureQueriesContext


@pytest.mark.django_db
def test_database_init_command(settings, tmp_path):
    # Test that we have correctly configured WAL mode
    with connection.cursor() as cursor:
        results = cursor.execute("PRAGMA journal_mode")
        assert results.fetchone()[0] == "wal"


@pytest.mark.django_db(transaction=True)
def test_transaction_mode_immediate():
    with CaptureQueriesContext(connection) as ctx:
        with transaction.atomic():
            pass
    assert ctx.captured_queries[0]["sql"] == "BEGIN IMMEDIATE"

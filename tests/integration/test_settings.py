import pytest
from django.db import connection, transaction
from django.db.utils import ConnectionHandler
from django.test.utils import CaptureQueriesContext


@pytest.mark.django_db
def test_database_init_command(settings, tmp_path):
    # We want to test that we have correctly configured WAL mode, but we can't do this
    # against the standard test database because that runs in memory. So we construct a
    # temporary database using the same configuration as the default and check that.
    connections = ConnectionHandler(
        {
            "default": settings.DATABASES["default"]
            | {
                "NAME": tmp_path / "test.db",
            },
        }
    )
    results = connections["default"].cursor().execute("PRAGMA journal_mode")
    assert results.fetchone()[0] == "wal"


@pytest.mark.django_db(transaction=True)
def test_transaction_mode_immediate():
    with CaptureQueriesContext(connection) as ctx:
        with transaction.atomic():
            pass
    assert ctx.captured_queries[0]["sql"] == "BEGIN IMMEDIATE"

import pytest
from django.db import ConnectionHandler


@pytest.mark.django_db
def test_connection_init_queries(settings, tmp_path):
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

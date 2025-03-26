from io import StringIO

import pytest
from django.core.management import call_command

from airlock.enums import AuditEventType
from tests import factories
from tests.local_db.test_data_access import (
    TEST_PARAMETERS,
    test_audits,
)


# Keep ruff happy
test_audits = test_audits

pytestmark = pytest.mark.django_db


def audit_output(kwargs):
    out = StringIO()
    call_command("audit", stdout=out, stderr=StringIO(), **kwargs)
    return out.getvalue().strip()


@pytest.mark.parametrize("kwargs,expected_audits", TEST_PARAMETERS)
def test_audit_command(test_audits, kwargs, expected_audits):
    output_lines = audit_output(kwargs).split("\n")

    assert output_lines == [str(test_audits[audit]) for audit in expected_audits]


def test_audit_command_shows_hidden(bll):
    factories.create_audit_event(
        AuditEventType.REQUEST_FILE_VIEW, extra=dict(review_turn="0")
    )
    factories.create_audit_event(
        AuditEventType.REQUEST_FILE_VIEW, extra=dict(review_turn="1")
    )
    output_lines = audit_output({}).split("\n")
    assert not any("hidden=True" in output_line for output_line in output_lines)

    bll._dal.hide_audit_events_for_turn(request_id="request", review_turn=1)
    output_lines = audit_output({}).split("\n")
    # logs are in reverse order
    assert "hidden=True" in output_lines[0]
    assert "review_turn=1" in output_lines[0]
    assert "hidden=True" not in output_lines[1]
    assert "review_turn=0" in output_lines[1]

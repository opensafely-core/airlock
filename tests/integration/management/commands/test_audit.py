from io import StringIO

import pytest
from django.core.management import call_command

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

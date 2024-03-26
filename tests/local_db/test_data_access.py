import pytest

from airlock.business_logic import AuditEventType
from local_db import data_access
from tests import factories


dal = data_access.LocalDBDataAccessLayer()

pytestmark = pytest.mark.django_db


@pytest.fixture
def test_audits():
    return {
        "workspace_view": factories.create_audit_event(
            AuditEventType.WORKSPACE_FILE_VIEW, request=None
        ),
        "other_workspace_view": factories.create_audit_event(
            AuditEventType.WORKSPACE_FILE_VIEW, workspace="other", request=None
        ),
        "request_view": factories.create_audit_event(AuditEventType.REQUEST_FILE_VIEW),
        "other_request_view": factories.create_audit_event(
            AuditEventType.REQUEST_FILE_VIEW, request="other"
        ),
        "other_user": factories.create_audit_event(
            AuditEventType.REQUEST_CREATE,
            user="other",
            path=None,
        ),
    }


TEST_PARAMETERS = [
    (
        {},
        [
            "other_user",
            "other_request_view",
            "request_view",
            "other_workspace_view",
            "workspace_view",
        ],
    ),
    (
        {"user": "user"},
        [
            "other_request_view",
            "request_view",
            "other_workspace_view",
            "workspace_view",
        ],
    ),
    (
        {"workspace": "workspace"},
        ["other_user", "other_request_view", "request_view", "workspace_view"],
    ),
    ({"request": "request"}, ["other_user", "request_view"]),
    ({"request": "request", "user": "user"}, ["request_view"]),
]


@pytest.mark.parametrize("kwargs,expected_audits", TEST_PARAMETERS)
def test_get_audit_log(test_audits, kwargs, expected_audits):
    assert dal.get_audit_log(**kwargs) == [test_audits[e] for e in expected_audits]

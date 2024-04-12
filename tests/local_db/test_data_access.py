import pytest

from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    RequestStatus,
)
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


def test_delete_file_from_request_bad_state():
    author = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED, user=author
    )
    audit = AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
    )

    with pytest.raises(AssertionError):
        dal.delete_file_from_request(release_request.id, "foo", audit)


@pytest.mark.parametrize(
    "state",
    [
        RequestStatus.PENDING,
        RequestStatus.WITHDRAWN,
        RequestStatus.REJECTED,
        RequestStatus.APPROVED,
    ],
)
def test_withdraw_file_from_request_bad_state(state):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        status=state,
    )

    with pytest.raises(AssertionError):
        dal.withdraw_file_from_request(
            release_request.id,
            "foo",
            AuditEvent.from_request(
                release_request, AuditEventType.REQUEST_FILE_WITHDRAW, user=author
            ),
        )


def test_delete_file_from_request_bad_path():
    author = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace",
        status=RequestStatus.PENDING,
        user=author,
    )

    audit = AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
    )

    with pytest.raises(BusinessLogicLayer.FileNotFound):
        dal.delete_file_from_request(release_request.id, "bad_path", audit)

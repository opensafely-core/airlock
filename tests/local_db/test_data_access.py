import pytest

from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    RequestStatus,
)
from airlock.types import UrlPath
from local_db import data_access, models
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

    assert dal.get_audit_log(size=1, **kwargs) == [test_audits[expected_audits[0]]]

    exclude = {AuditEventType.WORKSPACE_FILE_VIEW}
    remaining_audits = [
        e for e in expected_audits if test_audits[e].type not in exclude
    ]
    assert dal.get_audit_log(exclude=exclude, **kwargs) == [
        test_audits[e] for e in remaining_audits
    ]


def test_delete_file_from_request_bad_state():
    author = factories.create_user()
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED, user=author
    )
    audit = AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
    )

    with pytest.raises(AssertionError):
        dal.delete_file_from_request(release_request.id, UrlPath("foo"), audit)


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
            UrlPath("foo"),
            AuditEvent.from_request(
                release_request, AuditEventType.REQUEST_FILE_WITHDRAW, user=author
            ),
        )


def test_delete_file_from_request_bad_path():
    author = factories.create_user()
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
        dal.delete_file_from_request(release_request.id, UrlPath("bad_path"), audit)


def test_group_comment_delete_bad_params():
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["other-workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)

    audit = AuditEvent.from_request(
        request=release_request,
        type=AuditEventType.REQUEST_COMMENT,
        user=author,
        group="group",
        comment="author comment",
    )
    dal.group_comment_create(
        release_request.id, "group", "author comment", author, audit
    )

    audit = AuditEvent.from_request(
        request=release_request,
        type=AuditEventType.REQUEST_COMMENT_DELETE,
        user=author,
        group="badgroup",
        comment="author comment",
    )
    with pytest.raises(BusinessLogicLayer.APIException):
        dal.group_comment_delete(release_request.id, "badgroup", "1", author, audit)

    audit = AuditEvent.from_request(
        request=release_request,
        type=AuditEventType.REQUEST_COMMENT_DELETE,
        user=author,
        group="group",
        comment="other comment",
    )
    with pytest.raises(models.FileGroupComment.DoesNotExist):
        dal.group_comment_delete(release_request.id, "group", "50", author, audit)

    audit = AuditEvent.from_request(
        request=release_request,
        type=AuditEventType.REQUEST_COMMENT_DELETE,
        user=author,
        group="group",
        comment="author comment",
    )
    with pytest.raises(BusinessLogicLayer.APIException):
        dal.group_comment_delete(release_request.id, "group", "1", other, audit)

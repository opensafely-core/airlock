import pytest

from airlock import exceptions
from airlock.business_logic import store_file
from airlock.enums import (
    AuditEventType,
    RequestFileType,
    RequestStatus,
    Visibility,
)
from airlock.models import AuditEvent
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
    release_request = factories.create_request_at_status(
        "workspace", status=RequestStatus.SUBMITTED, author=author
    )
    audit = AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
    )

    with pytest.raises(AssertionError):
        dal.delete_file_from_request(release_request.id, UrlPath("foo"), audit)


@pytest.mark.parametrize(
    "status",
    [
        RequestStatus.PENDING,
        RequestStatus.WITHDRAWN,
        RequestStatus.REJECTED,
        RequestStatus.APPROVED,
    ],
)
def test_withdraw_file_from_request_bad_state(status):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[factories.request_file(approved=status != RequestStatus.PENDING)],
        withdrawn_after=RequestStatus.PENDING,
    )

    with pytest.raises(AssertionError):
        dal.withdraw_file_from_request(
            release_request.id,
            UrlPath("foo"),
            AuditEvent.from_request(
                release_request, AuditEventType.REQUEST_FILE_WITHDRAW, user=author
            ),
        )


def test_withdraw_file_from_request_file_does_not_exist():
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(changes_requested=True, path="foo.txt")],
    )

    # foo.txt does not exist and can't be withdrawn
    with pytest.raises(exceptions.FileNotFound):
        dal.withdraw_file_from_request(
            release_request.id,
            UrlPath("bar.txt"),
            AuditEvent.from_request(
                release_request, AuditEventType.REQUEST_FILE_WITHDRAW, user=author
            ),
        )
    # foo.txt can be withdrawn
    dal.withdraw_file_from_request(
        release_request.id,
        UrlPath("foo.txt"),
        AuditEvent.from_request(
            release_request, AuditEventType.REQUEST_FILE_WITHDRAW, user=author
        ),
    )


def test_add_file_to_request_bad_state():
    workspace = factories.create_workspace("workspace")
    author = factories.create_user(username="author", workspaces=["workspace"])
    request_file = factories.request_file()
    relpath = UrlPath(request_file.path)
    factories.write_workspace_file(workspace, relpath, contents="1234")
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[request_file],
    )

    src = workspace.abspath(relpath)
    file_id = store_file(release_request, src)
    manifest = workspace.get_manifest_for_file(relpath)

    with pytest.raises(exceptions.APIException):
        dal.add_file_to_request(
            request_id=release_request.id,
            group_name="group",
            relpath=relpath,
            file_id=file_id,
            filetype=RequestFileType.OUTPUT,
            timestamp=manifest["timestamp"],
            commit=manifest["commit"],
            repo=manifest["repo"],
            size=manifest["size"],
            job_id=manifest["job_id"],
            row_count=manifest["row_count"],
            col_count=manifest["col_count"],
            audit=AuditEvent.from_request(
                release_request, AuditEventType.REQUEST_FILE_ADD, user=author
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

    with pytest.raises(exceptions.FileNotFound):
        dal.delete_file_from_request(release_request.id, UrlPath("bad_path"), audit)


@pytest.mark.parametrize(
    "comment_modify_function,audit_event",
    [
        (dal.group_comment_delete, AuditEventType.REQUEST_COMMENT_DELETE),
        (
            dal.group_comment_visibility_public,
            AuditEventType.REQUEST_COMMENT_VISIBILITY_PUBLIC,
        ),
    ],
)
def test_group_comment_modify_bad_params(comment_modify_function, audit_event):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["other-workspace"], False)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file()],
    )

    audit = AuditEvent.from_request(
        request=release_request,
        type=AuditEventType.REQUEST_COMMENT,
        user=author,
        group="group",
        comment="author comment",
    )
    dal.group_comment_create(
        release_request.id,
        "group",
        "author comment",
        Visibility.PUBLIC,
        release_request.review_turn,
        author.username,
        audit,
    )

    audit = AuditEvent.from_request(
        request=release_request,
        type=audit_event,
        user=author,
        group="badgroup",
        comment="author comment",
    )
    with pytest.raises(exceptions.APIException):
        comment_modify_function(
            release_request.id, "badgroup", "1", author.username, audit
        )

    audit = AuditEvent.from_request(
        request=release_request,
        type=audit_event,
        user=author,
        group="group",
        comment="other comment",
    )
    with pytest.raises(models.FileGroupComment.DoesNotExist):
        comment_modify_function(
            release_request.id, "group", "50", author.username, audit
        )

    audit = AuditEvent.from_request(
        request=release_request,
        type=audit_event,
        user=author,
        group="group",
        comment="author comment",
    )
    with pytest.raises(exceptions.APIException):
        comment_modify_function(release_request.id, "group", "1", other.username, audit)

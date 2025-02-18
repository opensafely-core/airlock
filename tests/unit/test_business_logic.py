import inspect
import json
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils.dateparse import parse_datetime

import old_api
from airlock import exceptions
from airlock.business_logic import DataAccessLayerProtocol
from airlock.enums import (
    AuditEventType,
    NotificationEventType,
    RequestFileDecision,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    Visibility,
    WorkspaceFileStatus,
)
from airlock.models import (
    AuditEvent,
)
from airlock.types import UrlPath
from airlock.visibility import RequestFileStatus
from tests import factories


pytestmark = pytest.mark.django_db


def parse_notification_responses(mock_notifications):
    return {
        "count": len(mock_notifications.calls),
        "request_json": [
            json.loads(call.request.body) for call in mock_notifications.calls
        ],
    }


def assert_no_notifications(mock_notifications):
    assert parse_notification_responses(mock_notifications)["count"] == 0


def get_last_notification(mock_notifications):
    return parse_notification_responses(mock_notifications)["request_json"][-1]


def assert_last_notification(mock_notifications, event_type):
    assert get_last_notification(mock_notifications)["event_type"] == event_type


def setup_empty_release_request():
    author = factories.create_airlock_user("author", ["workspace"], False)
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    return release_request, path, author


@pytest.mark.parametrize("output_checker", [False, True])
def test_provider_get_workspaces_for_user(bll, output_checker):
    factories.create_workspace("foo")
    factories.create_workspace("bar")
    factories.create_workspace("not-allowed")
    workspaces = {
        "foo": factories.create_api_workspace(project="project 1", archived=False),
        "bar": factories.create_api_workspace(project="project 2", archived=True),
        "not-exists": factories.create_api_workspace(project="project 3"),
    }
    user = factories.create_airlock_user(
        username="testuser", workspaces=workspaces, output_checker=output_checker
    )

    assert bll.get_workspaces_for_user(user) == [
        bll.get_workspace("foo", user),
        bll.get_workspace("bar", user),
    ]


def test_provider_request_release_files_request_not_approved(bll, mock_notifications):
    author = factories.create_airlock_user("author", ["workspace"])
    checker = factories.create_airlock_user("checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file()],
    )

    with pytest.raises(exceptions.InvalidStateTransition):
        bll.release_files(release_request, checker)

    # Notification for submitting request only
    # Failed release attempt does not notify
    assert_last_notification(mock_notifications, "request_submitted")


def test_provider_request_release_files_invalid_file_type(bll, mock_notifications):
    # mock the LEVEL4_FILE_TYPES so that we can create this request with an
    # invalid file
    with patch("airlock.utils.LEVEL4_FILE_TYPES", [".foo"]):
        release_request = factories.create_request_at_status(
            "workspace",
            status=RequestStatus.REVIEWED,
            files=[factories.request_file(path="test/file.foo", approved=True)],
        )

    checker = factories.create_airlock_user("checker", [], output_checker=True)
    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.release_files(release_request, checker)
    assert_last_notification(mock_notifications, "request_reviewed")


def test_provider_request_release_files(mock_old_api, mock_notifications, bll, freezer):
    author = factories.create_airlock_user("author", workspaces=["workspace"])
    checkers = factories.get_default_output_checkers()
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group",
                path="test/file.txt",
                contents="test",
                approved=True,
            ),
            # a supporting file, which should NOT be released
            factories.request_file(
                group="group",
                path="test/supporting_file.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
            # An approved but withdrawn file, which should NOT be released
            # Note will withdraw later
            factories.request_file(
                group="group",
                path="test/withdrawn_file.txt",
                approved=True,
            ),
        ],
    )

    bll.withdraw_file_from_request(
        release_request,
        UrlPath("group/test/withdrawn_file.txt"),
        author,
    )
    release_request = factories.refresh_release_request(release_request)
    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    bll.review_request(release_request, checkers[0])
    release_request = factories.refresh_release_request(release_request)
    bll.review_request(release_request, checkers[1])
    release_request = factories.refresh_release_request(release_request)
    bll.set_status(release_request, RequestStatus.APPROVED, checkers[0])
    release_request = factories.refresh_release_request(release_request)

    relpath = UrlPath("test/file.txt")
    abspath = release_request.abspath("group" / relpath)

    freezer.move_to("2022-01-01T12:34:56")
    bll.release_files(release_request, checkers[0])

    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.filegroups["group"].files[relpath]
    assert request_file.released_by == checkers[0].username
    assert request_file.released_at == parse_datetime("2022-01-01T12:34:56Z")
    assert not request_file.uploaded

    expected_json = {
        "files": [
            {
                "name": "test/file.txt",
                "url": "test/file.txt",
                "size": 4,
                "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "date": old_api.modified_time(abspath),
                "metadata": {"tool": "airlock", "airlock_id": release_request.id},
                "review": None,
            }
        ],
        "metadata": {"tool": "airlock", "airlock_id": release_request.id},
        "review": None,
    }

    old_api.get_or_create_release.assert_called_once_with(  # type: ignore
        "workspace", release_request.id, json.dumps(expected_json), checkers[0].username
    )
    # upload file is not called on release; it will be called by the file uploader
    # asynchronously
    old_api.upload_file.assert_not_called()  # type: ignore

    notification_responses = parse_notification_responses(mock_notifications)
    assert notification_responses["count"] == 8
    request_json = notification_responses["request_json"]
    expected_notifications = [
        "request_submitted",
        "request_partially_reviewed",
        "request_reviewed",
        "request_returned",
        "request_resubmitted",
        "request_partially_reviewed",
        "request_reviewed",
        "request_approved",
    ]
    assert [event["event_type"] for event in request_json] == expected_notifications

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    expected_audit_logs = [
        # create request
        AuditEventType.REQUEST_CREATE,
        # add 3 files
        AuditEventType.REQUEST_FILE_ADD,
        AuditEventType.REQUEST_FILE_ADD,
        AuditEventType.REQUEST_FILE_ADD,
        # add default context & controls
        AuditEventType.REQUEST_EDIT,
        # submit request
        AuditEventType.REQUEST_SUBMIT,
        # initial reviews
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_REVIEW,
        AuditEventType.REQUEST_REVIEW,
        # return request
        AuditEventType.REQUEST_RETURN,
        # withdraw and resubmit
        AuditEventType.REQUEST_FILE_WITHDRAW,
        AuditEventType.REQUEST_SUBMIT,
        # re-review
        AuditEventType.REQUEST_REVIEW,
        AuditEventType.REQUEST_REVIEW,
        # appprove, release 1 output file, request NOT change to released yet
        # (changes only when all files are uploaded)
        AuditEventType.REQUEST_APPROVE,
        AuditEventType.REQUEST_FILE_RELEASE,
    ]
    assert [log.type for log in audit_log] == expected_audit_logs


def test_provider_request_release_files_retry(mock_old_api, bll, freezer):
    author = factories.create_airlock_user("author", workspaces=["workspace"])
    checkers = factories.get_default_output_checkers()
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(
                group="group",
                path="test/file.txt",
                contents="test",
                approved=True,
                uploaded=True,
            ),
            factories.request_file(
                group="group",
                path="test/file1.txt",
                contents="test",
                approved=True,
            ),
            factories.request_file(
                group="group",
                path="test/file2.txt",
                contents="test",
                approved=True,
            ),
        ],
    )

    uploaded_relpath = UrlPath("test/file.txt")
    not_uploaded_relpath = UrlPath("test/file1.txt")
    not_uploaded1_relpath = UrlPath("test/file2.txt")
    freezer.move_to("2022-01-01T12:34:56")

    uploaded_file = release_request.filegroups["group"].files[uploaded_relpath]
    assert uploaded_file.released_by == checkers[0].username
    assert uploaded_file.uploaded

    for relpath in [not_uploaded_relpath, not_uploaded1_relpath]:
        not_uploaded_file = release_request.filegroups["group"].files[relpath]
        assert not_uploaded_file.released_by == checkers[0].username
        assert not not_uploaded_file.uploaded
        assert not_uploaded_file.upload_attempts == 0

    # set max attempts to upload file2
    for _ in range(settings.UPLOAD_MAX_ATTEMPTS):
        bll.register_file_upload_attempt(release_request, not_uploaded1_relpath)

    release_request = factories.refresh_release_request(release_request)
    assert (
        release_request.filegroups["group"].files[relpath].upload_attempts
        == settings.UPLOAD_MAX_ATTEMPTS
    )

    # try to re-release
    bll.release_files(release_request, checkers[1])

    release_request = factories.refresh_release_request(release_request)

    # uploaded file hasn't changed
    uploaded_request_file = release_request.filegroups["group"].files[uploaded_relpath]
    assert uploaded_request_file.released_by == checkers[0].username

    # non-uploaded file has been updated and attempts on
    # file with max previous attempts has been reset
    for relpath in [not_uploaded_relpath, not_uploaded1_relpath]:
        not_uploaded_file = release_request.filegroups["group"].files[relpath]
        assert not_uploaded_file.released_by == checkers[1].username
        assert not_uploaded_file.upload_attempts == 0

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    expected_audit_logs = [
        # create request
        AuditEventType.REQUEST_CREATE,
        # add 3 files
        AuditEventType.REQUEST_FILE_ADD,
        AuditEventType.REQUEST_FILE_ADD,
        AuditEventType.REQUEST_FILE_ADD,
        # add default context & controls
        AuditEventType.REQUEST_EDIT,
        # submit request
        AuditEventType.REQUEST_SUBMIT,
        # initial reviews
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_REVIEW,
        AuditEventType.REQUEST_REVIEW,
        # approve, change request to released
        AuditEventType.REQUEST_APPROVE,
        AuditEventType.REQUEST_FILE_RELEASE,
        AuditEventType.REQUEST_FILE_RELEASE,
        AuditEventType.REQUEST_FILE_RELEASE,
        # upload one file
        AuditEventType.REQUEST_FILE_UPLOAD,
        # re-try only releases two un-uploaded files
        AuditEventType.REQUEST_FILE_RELEASE,
        AuditEventType.REQUEST_FILE_RELEASE,
    ]
    assert [log.type for log in audit_log] == expected_audit_logs


def test_provider_register_file_upload(mock_old_api, bll, freezer):
    author = factories.create_airlock_user("author", workspaces=["workspace"])
    checkers = factories.get_default_output_checkers()
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(
                group="group",
                path="test/file.txt",
                contents="test",
                approved=True,
            ),
            # a supporting file, which should NOT be released
            factories.request_file(
                group="group",
                path="test/supporting_file.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )

    relpath = UrlPath("test/file.txt")
    abspath = release_request.abspath("group" / relpath)
    freezer.move_to("2022-01-01T12:34:56")

    bll.register_file_upload(release_request, relpath, checkers[0])

    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert request_file.uploaded
    assert request_file.uploaded_at == parse_datetime("2022-01-01T12:34:56Z")

    expected_json = {
        "files": [
            {
                "name": "test/file.txt",
                "url": "test/file.txt",
                "size": 4,
                "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "date": old_api.modified_time(abspath),
                "metadata": {"tool": "airlock", "airlock_id": release_request.id},
                "review": None,
            }
        ],
        "metadata": {"tool": "airlock", "airlock_id": release_request.id},
        "review": None,
    }

    old_api.get_or_create_release.assert_called_once_with(  # type: ignore
        "workspace", release_request.id, json.dumps(expected_json), checkers[0].username
    )
    # in the real workflow, upload_file is called asynchronously and triggers
    # register_file_upload, so it isn't called in this test
    old_api.upload_file.assert_not_called()  # type: ignore

    audit_log = bll._dal.get_audit_log(request=release_request.id)

    expected_audit_logs = [
        # create request
        AuditEventType.REQUEST_CREATE,
        # add 2 files
        AuditEventType.REQUEST_FILE_ADD,
        AuditEventType.REQUEST_FILE_ADD,
        # add default context & controls
        AuditEventType.REQUEST_EDIT,
        # submit request
        AuditEventType.REQUEST_SUBMIT,
        # initial reviews
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_FILE_APPROVE,
        AuditEventType.REQUEST_REVIEW,
        AuditEventType.REQUEST_REVIEW,
        # appprove, release 1 output file, change request to released
        AuditEventType.REQUEST_APPROVE,
        AuditEventType.REQUEST_FILE_RELEASE,
        # upload 1 file
        AuditEventType.REQUEST_FILE_UPLOAD,
    ]
    assert [log.type for log in audit_log] == expected_audit_logs


def test_provider_register_and_reset_file_upload_attempt(mock_old_api, bll, freezer):
    author = factories.create_airlock_user("author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(
                group="group",
                path="test/file.txt",
                contents="test",
                approved=True,
            )
        ],
    )

    relpath = UrlPath("test/file.txt")
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert not request_file.uploaded
    assert request_file.upload_attempts == 0

    bll.register_file_upload_attempt(release_request, relpath)
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert not request_file.uploaded
    assert request_file.upload_attempts == 1

    bll.reset_file_upload_attempts(release_request, relpath)
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert not request_file.uploaded
    assert request_file.upload_attempts == 0


def test_provider_get_requests_for_workspace(bll):
    user = factories.create_airlock_user("test", ["workspace", "workspace2"])
    other_user = factories.create_airlock_user("other", ["workspace"])
    r1 = factories.create_release_request("workspace", user)
    factories.create_release_request("workspace2", user)
    r3 = factories.create_release_request("workspace", other_user)

    assert [r.id for r in bll.get_requests_for_workspace("workspace", user)] == [
        r1.id,
        r3.id,
    ]


def test_provider_get_requests_for_workspace_bad_user(bll):
    user = factories.create_airlock_user("test", ["workspace"])
    other_user = factories.create_airlock_user("other", ["workspace_2"])
    factories.create_release_request("workspace", user)
    factories.create_release_request("workspace_2", other_user)

    with pytest.raises(exceptions.WorkspacePermissionDenied):
        bll.get_requests_for_workspace("workspace", other_user)


def test_provider_get_requests_for_workspace_output_checker(bll):
    user = factories.create_airlock_user("test", ["workspace"])
    other_user = factories.create_airlock_user("other", [], True)
    r1 = factories.create_release_request("workspace", user)

    assert [r.id for r in bll.get_requests_for_workspace("workspace", other_user)] == [
        r1.id,
    ]


def test_provider_get_requests_authored_by_user(bll):
    user = factories.create_airlock_user("test", ["workspace"])
    other_user = factories.create_airlock_user("other", ["workspace"])
    r1 = factories.create_release_request("workspace", user)
    factories.create_release_request("workspace", other_user)

    assert [r.id for r in bll.get_requests_authored_by_user(user)] == [r1.id]


@pytest.mark.parametrize(
    "output_checker",
    [
        # A non-output checker never sees outstanding requests
        False,
        # An output checker only sees outstanding requests that
        # they did not author
        True,
    ],
)
def test_provider_get_outstanding_requests_for_review(
    mock_old_api, output_checker, bll
):
    user = factories.create_airlock_user("test", ["workspace"], output_checker)
    other_user = factories.create_airlock_user("other", ["workspace"], False)
    # request created by another user, status submitted
    r1 = factories.create_request_at_status(
        "workspace",
        author=other_user,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file()],
    )

    # requests not visible to output checker
    # status submitted, but authored by output checker
    factories.create_request_at_status(
        "workspace",
        author=user,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file()],
    )
    # requests authored by other users, status other than pending
    for i, status in enumerate(
        [
            RequestStatus.PENDING,
            RequestStatus.WITHDRAWN,
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.RELEASED,
        ]
    ):
        ws = f"workspace{i}"
        user_n = factories.create_airlock_user(f"test_{i}", [ws])
        factories.create_request_at_status(
            ws,
            author=user_n,
            status=status,
            files=[factories.request_file(approved=status != RequestStatus.PENDING)],
            withdrawn_after=RequestStatus.PENDING,
        )

    if output_checker:
        assert set(r.id for r in bll.get_outstanding_requests_for_review(user)) == set(
            [r1.id]
        )
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.get_outstanding_requests_for_review(user)


@pytest.mark.parametrize(
    "output_checker",
    [
        # A non-output checker never sees outstanding requests
        False,
        # An output checker only sees outstanding requests that
        # they did not author
        True,
    ],
)
def test_provider_get_returned_requests(mock_old_api, output_checker, bll):
    user = factories.create_airlock_user("test", ["workspace"], output_checker)
    other_user = factories.create_airlock_user("other", ["workspace"], False)

    # request created by another user, status returned
    r1 = factories.create_request_at_status(
        "workspace",
        author=other_user,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path="file.txt", approved=True)],
    )

    # requests not visible to output checker
    # status returned, but authored by output checker
    factories.create_request_at_status(
        "workspace",
        author=user,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path="file.txt", approved=True)],
    )

    # requests authored by other users, status other than returned
    for i, status in enumerate(
        [
            RequestStatus.PENDING,
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.RELEASED,
        ]
    ):
        ws = f"workspace{i}"
        user_n = factories.create_airlock_user(f"test_{i}", [ws])
        factories.create_request_at_status(
            ws,
            author=user_n,
            status=status,
            withdrawn_after=RequestStatus.PENDING,
            files=[factories.request_file(approved=True)],
        )

    if output_checker:
        assert set(r.id for r in bll.get_returned_requests(user)) == set([r1.id])
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.get_returned_requests(user)


@pytest.mark.parametrize(
    "output_checker",
    [
        # A non-output checker never sees outstanding requests
        False,
        # An output checker only sees outstanding requests that
        # they did not author
        True,
    ],
)
def test_provider_get_approved_requests(mock_old_api, output_checker, bll):
    user = factories.create_airlock_user("test", ["workspace"], output_checker)
    other_user = factories.create_airlock_user("other", ["workspace"], False)

    # request created by another user, status approved
    r1 = factories.create_request_at_status(
        "workspace",
        author=other_user,
        status=RequestStatus.APPROVED,
        files=[factories.request_file(path="file.txt", contents="test", approved=True)],
    )

    # requests not visible to output checker
    # status approved, but authored by output checker
    factories.create_request_at_status(
        "workspace",
        author=user,
        status=RequestStatus.APPROVED,
        files=[
            factories.request_file(path="file1.txt", contents="test1", approved=True)
        ],
    )

    # requests authored by other users, status other than approved
    for i, status in enumerate(
        [
            RequestStatus.PENDING,
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
            RequestStatus.RETURNED,
            RequestStatus.REJECTED,
            RequestStatus.RELEASED,
        ]
    ):
        ws = f"workspace{i}"
        user_n = factories.create_airlock_user(f"test_{i}", [ws])
        factories.create_request_at_status(
            ws,
            author=user_n,
            status=status,
            withdrawn_after=RequestStatus.PENDING,
            files=[factories.request_file(approved=True)],
        )

    if output_checker:
        assert set(r.id for r in bll.get_approved_requests(user)) == set([r1.id])
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.get_approved_requests(user)


@pytest.mark.parametrize(
    "status,is_current",
    [
        # Until released, rejected or withdrawn, all of these
        # statuses are considered active and should be the current
        # request. They are either editable by the author, or can be
        # returned to an editable status
        (RequestStatus.PENDING, True),
        (RequestStatus.SUBMITTED, True),
        (RequestStatus.PARTIALLY_REVIEWED, True),
        (RequestStatus.REVIEWED, True),
        (RequestStatus.RETURNED, True),
        # Requests in these statuses cannot move back into an editable
        # state
        (RequestStatus.APPROVED, False),
        (RequestStatus.RELEASED, False),
        (RequestStatus.REJECTED, False),
        (RequestStatus.WITHDRAWN, False),
    ],
)
def test_provider_get_current_request_for_user(mock_old_api, bll, status, is_current):
    user = factories.create_airlock_user(workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=user,
        status=status,
        files=[factories.request_file(approved=True)],
        withdrawn_after=RequestStatus.PENDING
        if status == RequestStatus.WITHDRAWN
        else None,
    )

    current_request = bll.get_current_request("workspace", user)

    assert (current_request == release_request) == is_current


def test_provider_get_or_create_current_request_for_user(bll):
    workspace = factories.create_workspace("workspace")
    user = factories.create_airlock_user("testuser", ["workspace"], False)
    other_user = factories.create_airlock_user("otheruser", ["workspace"], False)

    assert bll.get_current_request("workspace", user) is None

    factories.create_release_request(workspace, other_user)
    assert bll.get_current_request("workspace", user) is None

    release_request = bll.get_or_create_current_request("workspace", user)
    assert release_request.workspace == "workspace"
    assert release_request.author == user.username

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log == [
        AuditEvent(
            type=AuditEventType.REQUEST_CREATE,
            user=user.username,
            workspace=workspace.name,
            request=release_request.id,
        )
    ]

    # reach around an simulate 2 active requests for same user
    bll._dal.create_release_request(
        workspace="workspace",
        author=user.username,
        status=RequestStatus.PENDING,
        audit=AuditEvent(
            type=AuditEventType.REQUEST_CREATE,
            user=user.username,
            workspace=workspace.name,
        ),
    )

    with pytest.raises(Exception):
        bll.get_current_request("workspace", user)


def test_provider_get_current_request_for_former_user(bll):
    factories.create_workspace("workspace")
    user = factories.create_airlock_user("testuser", ["workspace"], False)

    assert bll.get_current_request("workspace", user) is None

    release_request = bll.get_or_create_current_request("workspace", user)
    assert release_request.workspace == "workspace"
    assert release_request.author == user.username

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log == [
        AuditEvent(
            type=AuditEventType.REQUEST_CREATE,
            user=user.username,
            workspace="workspace",
            request=release_request.id,
        )
    ]

    # let's pretend the user no longer has permission to access the workspace
    former_user = factories.create_airlock_user("testuser", [], False)

    with pytest.raises(Exception):
        bll.get_current_request("workspace", former_user)


def test_provider_get_current_request_for_user_output_checker(bll):
    """Output checker must have explict workspace permissions to create requests."""
    factories.create_workspace("workspace")
    user = factories.create_airlock_user("output_checker", [], True)

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.get_or_create_current_request("workspace", user)


@pytest.mark.parametrize(
    "current,future,valid_author,valid_checker,withdrawn_after",
    # valid_author: author can set status of their own request
    # valid_checker: checker can set status of another author's request
    [
        (RequestStatus.PENDING, RequestStatus.SUBMITTED, True, False, None),
        (RequestStatus.PENDING, RequestStatus.WITHDRAWN, True, False, None),
        (RequestStatus.PENDING, RequestStatus.PARTIALLY_REVIEWED, False, False, None),
        (RequestStatus.PENDING, RequestStatus.REVIEWED, False, False, None),
        (RequestStatus.PENDING, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.PENDING, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.PENDING, RequestStatus.RELEASED, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.PARTIALLY_REVIEWED, False, True, None),
        (RequestStatus.SUBMITTED, RequestStatus.REVIEWED, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.WITHDRAWN, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.RETURNED, False, True, None),
        (RequestStatus.SUBMITTED, RequestStatus.RELEASED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.SUBMITTED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.REVIEWED, False, True, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.RELEASED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.RETURNED, False, True, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.WITHDRAWN, False, False, None),
        (RequestStatus.REVIEWED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.REVIEWED, RequestStatus.SUBMITTED, False, False, None),
        (RequestStatus.REVIEWED, RequestStatus.PARTIALLY_REVIEWED, False, False, None),
        (RequestStatus.REVIEWED, RequestStatus.RETURNED, False, True, None),
        (RequestStatus.REVIEWED, RequestStatus.APPROVED, False, True, None),
        (RequestStatus.REVIEWED, RequestStatus.REJECTED, False, True, None),
        (RequestStatus.REVIEWED, RequestStatus.WITHDRAWN, False, False, None),
        (RequestStatus.RETURNED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.RETURNED, RequestStatus.SUBMITTED, True, False, None),
        (RequestStatus.RETURNED, RequestStatus.PARTIALLY_REVIEWED, False, False, None),
        (RequestStatus.RETURNED, RequestStatus.REVIEWED, False, False, None),
        (RequestStatus.RETURNED, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.RETURNED, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.RETURNED, RequestStatus.WITHDRAWN, True, False, None),
        (RequestStatus.APPROVED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.APPROVED, RequestStatus.SUBMITTED, False, False, None),
        (RequestStatus.APPROVED, RequestStatus.PARTIALLY_REVIEWED, False, False, None),
        (RequestStatus.APPROVED, RequestStatus.REVIEWED, False, False, None),
        (RequestStatus.APPROVED, RequestStatus.RELEASED, False, True, None),
        (RequestStatus.APPROVED, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.APPROVED, RequestStatus.WITHDRAWN, False, False, None),
        (RequestStatus.REJECTED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.REJECTED, RequestStatus.SUBMITTED, False, False, None),
        (RequestStatus.REJECTED, RequestStatus.PARTIALLY_REVIEWED, False, False, None),
        (RequestStatus.REJECTED, RequestStatus.REVIEWED, False, False, None),
        (RequestStatus.REJECTED, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.REJECTED, RequestStatus.WITHDRAWN, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.SUBMITTED, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.PARTIALLY_REVIEWED, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.REVIEWED, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.RELEASED, RequestStatus.WITHDRAWN, False, False, None),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.PENDING,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.SUBMITTED,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.PARTIALLY_REVIEWED,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.REVIEWED,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.APPROVED,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.REJECTED,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.RETURNED,
            False,
            False,
            RequestStatus.PENDING,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.PENDING,
            False,
            False,
            RequestStatus.RETURNED,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.SUBMITTED,
            False,
            False,
            RequestStatus.RETURNED,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.PARTIALLY_REVIEWED,
            False,
            False,
            RequestStatus.RETURNED,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.REVIEWED,
            False,
            False,
            RequestStatus.RETURNED,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.APPROVED,
            False,
            False,
            RequestStatus.RETURNED,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.REJECTED,
            False,
            False,
            RequestStatus.RETURNED,
        ),
        (
            RequestStatus.WITHDRAWN,
            RequestStatus.RETURNED,
            False,
            False,
            RequestStatus.RETURNED,
        ),
    ],
)
def test_set_status(
    current, future, valid_author, valid_checker, withdrawn_after, bll, mock_old_api
):
    author = factories.create_airlock_user(
        "author", ["workspace1", "workspace2"], False
    )
    checker = factories.create_airlock_user(output_checker=True)
    file_reviewers = [checker, factories.create_airlock_user("checker1", [], True)]
    audit_type = bll.STATUS_AUDIT_EVENT[future]

    release_request1 = factories.create_request_at_status(
        "workspace1",
        status=current,
        author=author,
        checker=checker,
        withdrawn_after=withdrawn_after,
        files=[factories.request_file(approved=True, checkers=file_reviewers)],
    )
    release_request2 = factories.create_request_at_status(
        "workspace2",
        status=current,
        author=author,
        checker=checker,
        withdrawn_after=withdrawn_after,
        files=[factories.request_file(approved=True, checkers=file_reviewers)],
    )

    if valid_author:
        bll.set_status(release_request1, future, user=author)
        assert release_request1.status == future
        audit_log = bll._dal.get_audit_log(request=release_request1.id)
        assert audit_log[0].type == audit_type
        assert audit_log[0].user == author.username
        assert audit_log[0].request == release_request1.id
        assert audit_log[0].workspace == "workspace1"
    else:
        with pytest.raises(
            (exceptions.InvalidStateTransition, exceptions.RequestPermissionDenied)
        ):
            bll.set_status(release_request1, future, user=author)

    if valid_checker:
        bll.set_status(release_request2, future, user=checker)
        assert release_request2.status == future
        audit_log = bll._dal.get_audit_log(request=release_request2.id)
        assert audit_log[0].type == audit_type
        assert audit_log[0].user == checker.username
        assert audit_log[0].request == release_request2.id
        assert audit_log[0].workspace == "workspace2"
    else:
        with pytest.raises(
            (exceptions.InvalidStateTransition, exceptions.RequestPermissionDenied)
        ):
            bll.set_status(release_request2, future, user=checker)


@pytest.mark.parametrize(
    "current,future,user,notification_event_type",
    [
        (RequestStatus.PENDING, RequestStatus.SUBMITTED, "author", "request_submitted"),
        (RequestStatus.PENDING, RequestStatus.WITHDRAWN, "author", "request_withdrawn"),
        (
            RequestStatus.SUBMITTED,
            RequestStatus.PARTIALLY_REVIEWED,
            "checker",
            "request_partially_reviewed",
        ),
        (
            RequestStatus.PARTIALLY_REVIEWED,
            RequestStatus.REVIEWED,
            "checker",
            "request_reviewed",
        ),
        (
            RequestStatus.REVIEWED,
            RequestStatus.REJECTED,
            "checker",
            "request_rejected",
        ),
        (
            RequestStatus.REVIEWED,
            RequestStatus.APPROVED,
            "checker",
            "request_approved",
        ),
        (
            RequestStatus.REVIEWED,
            RequestStatus.RETURNED,
            "checker",
            "request_returned",
        ),
        (
            RequestStatus.RETURNED,
            RequestStatus.SUBMITTED,
            "author",
            "request_resubmitted",
        ),
        (
            RequestStatus.RETURNED,
            RequestStatus.WITHDRAWN,
            "author",
            "request_withdrawn",
        ),
        (RequestStatus.APPROVED, RequestStatus.RELEASED, "checker", "request_released"),
    ],
)
def test_set_status_notifications(
    current,
    future,
    user,
    notification_event_type,
    bll,
    mock_notifications,
    mock_old_api,
):
    users = {
        "author": factories.create_airlock_user("author", ["workspace"], False),
        "checker": factories.create_airlock_user(output_checker=True),
    }
    release_request = factories.create_request_at_status(
        "workspace",
        status=current,
        files=[factories.request_file(approved=True)],
        author=users["author"],
    )
    bll.set_status(release_request, future, users[user])
    assert_last_notification(mock_notifications, notification_event_type)


@pytest.mark.parametrize(
    "updates,success,expected_error",
    [
        ([], True, None),
        ([{"update": "updated a thing"}, {"update": "another update"}], True, None),
        ([{"update": "updated a thing", "user": "test"}], True, None),
        ([{"update": "updated a thing", "group": "test"}], True, None),
        ([{"update": "updated a thing", "user": "test", "group": "test"}], True, None),
        ([{}], False, "must include an `update` key"),
        ([{"user": "test"}], False, "must include an `update` key"),
        ([{"update": "an update", "foo": "bar"}], False, "Unexpected keys"),
        (
            [
                {"update": "updated a thing"},
                {
                    "update": "updated a thing",
                    "user": "test",
                    "group": "test",
                    "foo": "bar",
                },
            ],
            False,
            "Unexpected keys",
        ),
    ],
)
def test_notification_updates(
    bll, mock_notifications, updates, success, expected_error
):
    author = factories.create_airlock_user()
    release_request = factories.create_release_request("workspace", author)
    if success:
        bll.send_notification(
            release_request, NotificationEventType.REQUEST_SUBMITTED, author, updates
        )
    else:
        with pytest.raises(AssertionError, match=expected_error):
            bll.send_notification(
                release_request,
                NotificationEventType.REQUEST_SUBMITTED,
                author,
                updates,
            )


def test_notification_error(bll, notifications_stubber, caplog):
    mock_notifications = notifications_stubber(
        json={"status": "error", "message": "something went wrong"}
    )
    author = factories.create_airlock_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "test/file.txt")
    bll.group_edit(release_request, "group", "foo", "bar", author)
    release_request = factories.refresh_release_request(release_request)
    bll.set_status(release_request, RequestStatus.SUBMITTED, author)
    notifications_responses = parse_notification_responses(mock_notifications)
    assert (
        notifications_responses["request_json"][-1]["event_type"] == "request_submitted"
    )
    # Nothing errors, but we log the notification error message
    assert caplog.records[-1].levelname == "ERROR"
    assert caplog.records[-1].message == "something went wrong"


@pytest.mark.parametrize("all_files_approved", (True, False))
def test_set_status_approved(all_files_approved, bll, mock_notifications):
    author = factories.create_airlock_user("author", ["workspace"], False)
    checker = factories.create_airlock_user(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(
                approved=all_files_approved, changes_requested=not all_files_approved
            )
        ],
    )

    if all_files_approved:
        bll.set_status(release_request, RequestStatus.APPROVED, user=checker)
        assert release_request.status == RequestStatus.APPROVED
        assert_last_notification(mock_notifications, "request_approved")
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.set_status(release_request, RequestStatus.APPROVED, user=checker)
        assert_last_notification(mock_notifications, "request_reviewed")


def test_set_status_cannot_action_own_request(bll, mock_old_api):
    user = factories.create_airlock_user(
        workspaces=["workspace", "workspace1"], output_checker=True
    )
    release_request1 = factories.create_request_at_status(
        "workspace",
        author=user,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file()],
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.set_status(release_request1, RequestStatus.PARTIALLY_REVIEWED, user=user)

    release_request2 = factories.create_request_at_status(
        "workspace1",
        author=user,
        status=RequestStatus.APPROVED,
        files=[factories.request_file(approved=True)],
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.set_status(release_request2, RequestStatus.RELEASED, user=user)


def test_submit_request(bll, mock_notifications):
    """
    From pending
    """
    author = factories.create_airlock_user("author", ["workspace"], False)
    release_request = factories.create_release_request(
        "workspace", user=author, status=RequestStatus.PENDING
    )
    factories.add_request_file(release_request, "group", "test/file.txt")
    bll.group_edit(release_request, "group", "foo", "bar", author)
    release_request = bll.get_release_request(release_request.id, author)
    bll.submit_request(release_request, author)
    assert release_request.status == RequestStatus.SUBMITTED
    assert_last_notification(mock_notifications, "request_submitted")


def test_resubmit_request(bll, mock_notifications):
    """
    From returned
    Files with changes requested status are moved to undecided
    """
    author = factories.create_airlock_user("author", ["workspace"], False)
    # Returned request with two files, one is approved by both reviewers, one has changes requested
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(group="test", path="file.txt", approved=True),
            factories.request_file(
                group="test", path="file1.txt", changes_requested=True
            ),
        ],
    )
    assert release_request.submitted_reviews_count() == 2

    # author re-submits with no changes to files
    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.SUBMITTED
    assert_last_notification(mock_notifications, "request_resubmitted")
    for i in range(2):
        user = factories.create_airlock_user(f"output-checker-{i}", output_checker=True)
        # approved file review is still approved
        approved_file = release_request.get_request_file_from_output_path(
            UrlPath("file.txt")
        )
        assert approved_file.get_file_vote_for_user(user) == RequestFileVote.APPROVED

        # changes_requested file review is now undecided
        changes_requested_file = release_request.get_request_file_from_output_path(
            UrlPath("file1.txt")
        )
        assert (
            changes_requested_file.get_file_vote_for_user(user)
            == RequestFileVote.UNDECIDED
        )
        assert not release_request.all_files_reviewed_by_reviewer(user)
        # submitted reviews have been reset
        assert release_request.submitted_reviews == {}


def test_add_file_to_request_not_author(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    other = factories.create_airlock_user("other", ["workspace"], True)

    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.add_file_to_request(release_request, path, other)


@pytest.mark.parametrize(
    "workspaces,exception",
    [
        # no access; possible if a user has been removed from a
        # project/workspace on job-server, or has had their roles
        # updated since a release was created
        ({}, exceptions.WorkspacePermissionDenied),
        # workspace archived
        (
            {
                "workspace": factories.create_api_workspace(
                    project="p1", archived=True
                ),
            },
            exceptions.RequestPermissionDenied,
        ),
        # project inactive
        (
            {
                "workspace": factories.create_api_workspace(
                    project="p1", ongoing=False
                ),
            },
            exceptions.RequestPermissionDenied,
        ),
    ],
)
def test_add_file_to_request_no_permission(bll, workspaces, exception):
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=factories.create_airlock_user("author", ["workspace"], False),
    )

    # create duplicate user with test workspaces
    author = factories.create_airlock_user("author", workspaces, False)
    with pytest.raises(exception):
        bll.add_file_to_request(release_request, path, author)


def test_add_file_to_request_invalid_file_type(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)

    path = UrlPath("path/file.foo")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        workspace,
        user=author,
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.add_file_to_request(release_request, path, author)


@pytest.mark.parametrize(
    "status,success",
    [
        (RequestStatus.PENDING, True),
        (RequestStatus.SUBMITTED, False),
        (RequestStatus.PARTIALLY_REVIEWED, False),
        (RequestStatus.REVIEWED, False),
        (RequestStatus.RETURNED, True),
        (RequestStatus.APPROVED, False),
        (RequestStatus.REJECTED, False),
        (RequestStatus.RELEASED, False),
        (RequestStatus.WITHDRAWN, False),
    ],
)
def test_add_file_to_request_states(status, success, bll, mock_old_api):
    author = factories.create_airlock_user("author", ["workspace"], False)

    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)

    release_request = factories.create_request_at_status(
        workspace,
        author=author,
        status=status,
        files=[factories.request_file(path="file.txt", approved=True)],
        withdrawn_after=RequestStatus.PENDING
        if status == RequestStatus.WITHDRAWN
        else None,
    )

    if success:
        bll.add_file_to_request(release_request, path, author)
        assert release_request.abspath("default" / path).exists()

        audit_log = bll._dal.get_audit_log(request=release_request.id)
        assert audit_log[0] == AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_FILE_ADD,
            user=author,
            path=path,
            group="default",
            filetype="OUTPUT",
        )
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.add_file_to_request(release_request, path, author)


def test_add_file_to_request_default_filetype(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        workspace,
        user=author,
    )
    bll.add_file_to_request(release_request, path, author)
    request_file = release_request.filegroups["default"].files[path]
    assert request_file.filetype == RequestFileType.OUTPUT


@pytest.mark.parametrize(
    "filetype,success",
    [
        (RequestFileType.OUTPUT, True),
        (RequestFileType.SUPPORTING, True),
        ("unknown", False),
    ],
)
def test_add_file_to_request_with_filetype(bll, filetype, success):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        workspace,
        user=author,
    )

    if success:
        bll.add_file_to_request(release_request, path, author, filetype=filetype)
        request_file = release_request.filegroups["default"].files[UrlPath(path)]
        assert request_file.filetype == filetype
    else:
        with pytest.raises(AttributeError):
            bll.add_file_to_request(release_request, path, author, filetype=filetype)


def test_add_file_to_request_already_released(bll, mock_old_api):
    user = factories.create_airlock_user(workspaces=["workspace"])
    # release one file, include one supporting file
    factories.create_request_at_status(
        "workspace",
        RequestStatus.RELEASED,
        author=user,
        files=[
            factories.request_file(path="file.txt", contents="foo", approved=True),
            factories.request_file(
                path="supporting.txt",
                contents="bar",
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )
    # user has no current request
    assert bll.get_current_request("workspace", user) is None

    # create a new pending request
    release_request = factories.create_release_request("workspace", user)
    # can add the supporting file as an output file to this new request
    bll.add_file_to_request(
        release_request, "supporting.txt", user, filetype=RequestFileType.OUTPUT
    )
    # Can't add the released file
    with pytest.raises(
        exceptions.RequestPermissionDenied, match=r"Cannot add released file"
    ):
        bll.add_file_to_request(
            release_request, "file.txt", user, filetype=RequestFileType.OUTPUT
        )


def test_update_file_in_request_invalid_file_type(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)

    relpath = UrlPath("path/file.foo")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    with patch("airlock.utils.LEVEL4_FILE_TYPES", [".foo"]):
        release_request = factories.create_request_at_status(
            "workspace",
            author=author,
            files=[factories.request_file(path=relpath)],
            status=RequestStatus.PENDING,
        )

    with pytest.raises(
        exceptions.RequestPermissionDenied, match=r"Cannot update file of type"
    ):
        bll.update_file_in_request(release_request, relpath, author)


def test_update_file_in_request_not_updated(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)

    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path=relpath, approved=True)],
    )
    with pytest.raises(exceptions.RequestPermissionDenied, match=r"not updated"):
        bll.update_file_in_request(release_request, relpath, author)


@pytest.mark.parametrize(
    "status,approved,changes_requested,success,notification_sent",
    [
        (RequestStatus.PENDING, False, False, True, False),
        (RequestStatus.SUBMITTED, False, False, False, False),
        (RequestStatus.WITHDRAWN, False, True, False, False),
        (RequestStatus.APPROVED, True, False, False, False),
        (RequestStatus.REJECTED, False, True, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, True, False, False, False),
        (RequestStatus.REVIEWED, True, False, False, False),
        (RequestStatus.RETURNED, False, True, True, True),
        (RequestStatus.RELEASED, True, False, False, False),
    ],
)
def test_update_file_to_request_states(
    status,
    approved,
    changes_requested,
    success,
    notification_sent,
    bll,
    mock_notifications,
    mock_old_api,
):
    author = factories.create_airlock_user("author", ["workspace"], False)
    checkers = factories.get_default_output_checkers()
    workspace = factories.create_workspace("workspace")
    path = UrlPath("path/file.txt")

    if status == RequestStatus.WITHDRAWN:
        withdrawn_after = RequestStatus.RETURNED
    else:
        withdrawn_after = None

    workspace_file = factories.request_file(
        path=path,
        user=author,
        approved=approved,
        changes_requested=changes_requested,
    )

    release_request = factories.create_request_at_status(
        workspace.name,
        author=author,
        status=status,
        withdrawn_after=withdrawn_after,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    if success:
        assert (
            workspace.get_workspace_file_status(path)
            == WorkspaceFileStatus.UNDER_REVIEW
        )

    factories.write_workspace_file(workspace, path, contents="changed")

    if success:
        assert (
            workspace.get_workspace_file_status(path)
            == WorkspaceFileStatus.CONTENT_UPDATED
        )
        bll.update_file_in_request(release_request, path, author)
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.update_file_in_request(release_request, path, author)
        return

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW

    status1 = release_request.get_request_file_status("group" / path, checkers[0])
    assert isinstance(status1, RequestFileStatus)
    assert status1.vote is None
    assert status1.decision == RequestFileDecision.INCOMPLETE
    status2 = release_request.get_request_file_status("group" / path, checkers[1])
    assert isinstance(status2, RequestFileStatus)
    assert status2.vote is None
    assert status2.decision == RequestFileDecision.INCOMPLETE

    assert release_request.abspath("group" / path).exists()

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_UPDATE,
        user=author,
        path=path,
        group="group",
        filetype="OUTPUT",
    )
    if status == RequestStatus.RETURNED:
        assert audit_log[1] == AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_FILE_WITHDRAW,
            user=author,
            path=path,
            group="group",
            filetype="OUTPUT",
        )
        assert audit_log[2] == AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_FILE_RESET_REVIEW,
            user=author,
            path=path,
            group="group",
            filetype="OUTPUT",
            reviewer="output-checker-1",
        )
        assert audit_log[3] == AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_FILE_RESET_REVIEW,
            user=author,
            path=path,
            group="group",
            filetype="OUTPUT",
            reviewer="output-checker-0",
        )

    if notification_sent:
        last_notification = get_last_notification(mock_notifications)
        # this notification is an artefact of our setting up the test
        assert last_notification["event_type"] == "request_returned"
    else:
        assert_no_notifications(mock_notifications)


@pytest.mark.parametrize(
    "request_status",
    [
        RequestStatus.PENDING,
        RequestStatus.RETURNED,
    ],
)
def test_replace_file_with_different_file_type(bll, request_status):
    author = factories.create_airlock_user("author", ["workspace"], False)
    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=request_status,
        files=[
            factories.request_file(
                path=relpath,
                group="group",
                filetype=RequestFileType.OUTPUT,
                approved=True,
            )
        ],
    )
    # No change to file content, only to file type
    bll.replace_file_in_request(
        release_request, relpath, author, "group", RequestFileType.SUPPORTING
    )
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert request_file.filetype == RequestFileType.SUPPORTING


def test_cannot_replace_unchanged_file_with_same_file_type(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                path=relpath,
                group="group",
                filetype=RequestFileType.OUTPUT,
                approved=True,
            )
        ],
    )
    # No change to file content, same file type
    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.replace_file_in_request(
            release_request, relpath, author, "group", RequestFileType.OUTPUT
        )


def test_cannot_replace_file_with_invalid_file_type(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                path=relpath,
                group="group",
                filetype=RequestFileType.OUTPUT,
                approved=True,
            )
        ],
    )
    # No change to file content, bad file type
    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.replace_file_in_request(
            release_request, relpath, author, "group", RequestFileType.CODE
        )


@pytest.mark.parametrize(
    "request_status",
    [
        RequestStatus.PENDING,
        RequestStatus.RETURNED,
    ],
)
def test_replace_updated_file_with_different_file_type(bll, request_status):
    author = factories.create_airlock_user("author", ["workspace"], False)
    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=request_status,
        files=[
            factories.request_file(
                path=relpath,
                group="group",
                filetype=RequestFileType.OUTPUT,
                approved=True,
            )
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    # update file
    factories.write_workspace_file(workspace, relpath, contents="changed")
    assert (
        workspace.get_workspace_file_status(relpath)
        == WorkspaceFileStatus.CONTENT_UPDATED
    )

    # Replace updated file with different file type
    bll.replace_file_in_request(
        release_request, relpath, author, "group", RequestFileType.SUPPORTING
    )
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(relpath)
    assert request_file.filetype == RequestFileType.SUPPORTING


def test_withdraw_file_from_request_pending(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path1 = UrlPath("path/file1.txt")
    path2 = UrlPath("path/file2.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="group", path=path1, contents="1", user=author
            ),
            factories.request_file(
                group="group", path=path2, contents="2", user=author
            ),
        ],
    )
    assert release_request.filegroups["group"].files.keys() == {path1, path2}

    bll.withdraw_file_from_request(release_request, "group" / path1, user=author)

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path1,
        group="group",
    )

    assert release_request.filegroups["group"].files.keys() == {path2}

    bll.withdraw_file_from_request(release_request, "group" / path2, user=author)

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path2,
        group="group",
    )

    assert release_request.filegroups["group"].files.keys() == set()


def test_withdraw_file_from_request_returned(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path1 = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group",
                path=path1,
                contents="1",
                user=author,
                changes_requested=True,
            ),
        ],
    )
    assert [f.filetype for f in release_request.filegroups["group"].files.values()] == [
        RequestFileType.OUTPUT,
    ]
    bll.withdraw_file_from_request(release_request, "group" / path1, user=author)
    release_request = factories.refresh_release_request(release_request)

    assert [f.filetype for f in release_request.filegroups["group"].files.values()] == [
        RequestFileType.WITHDRAWN,
    ]

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path1,
        group="group",
    )

    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path1) == WorkspaceFileStatus.WITHDRAWN


def test_readd_withdrawn_file_to_request_returned(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group",
                path=path,
                contents="1",
                user=author,
                approved=True,
            ),
        ],
    )

    bll.withdraw_file_from_request(release_request, "group" / path, user=author)

    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.WITHDRAWN

    bll.add_withdrawn_file_to_request(
        release_request, path, group_name="group", user=author
    )

    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW


def test_readd_withdrawn_file_to_request_returned_new_group(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group",
                path=path,
                contents="1",
                user=author,
                approved=True,
                filetype=RequestFileType.OUTPUT,
            ),
        ],
    )

    request_file = release_request.get_request_file_from_output_path(path)
    assert request_file.group == "group"
    assert request_file.filetype == RequestFileType.OUTPUT

    bll.withdraw_file_from_request(release_request, "group" / path, user=author)

    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.WITHDRAWN

    bll.add_withdrawn_file_to_request(
        release_request,
        path,
        group_name="new-group",
        user=author,
        filetype=RequestFileType.SUPPORTING,
    )

    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW

    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(path)

    assert request_file.group == "new-group"
    assert request_file.filetype == RequestFileType.SUPPORTING


@pytest.mark.parametrize(
    "status",
    [
        RequestStatus.SUBMITTED,
        RequestStatus.PARTIALLY_REVIEWED,
        RequestStatus.REVIEWED,
        RequestStatus.APPROVED,
        RequestStatus.REJECTED,
        RequestStatus.WITHDRAWN,
        RequestStatus.RELEASED,
    ],
)
def test_withdraw_file_from_request_not_editable_state(bll, mock_old_api, status):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[
            factories.request_file(
                group="group", path="foo.txt", user=author, approved=True
            ),
        ],
        withdrawn_after=RequestStatus.PENDING
        if status == RequestStatus.WITHDRAWN
        else None,
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.withdraw_file_from_request(
            release_request, UrlPath("group/foo.txt"), author
        )


@pytest.mark.parametrize("status", [RequestStatus.PENDING, RequestStatus.RETURNED])
def test_withdraw_file_from_request_bad_file(bll, status):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[
            factories.request_file(
                group="group", path="foo.txt", user=author, approved=True
            ),
        ],
    )

    with pytest.raises(exceptions.FileNotFound):
        bll.withdraw_file_from_request(
            release_request, UrlPath("bad/path"), user=author
        )


@pytest.mark.parametrize("status", [RequestStatus.PENDING, RequestStatus.SUBMITTED])
def test_withdraw_file_from_request_not_author(bll, status):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[
            factories.request_file(
                group="group", path="foo.txt", user=author, approved=True
            ),
        ],
    )

    other = factories.create_airlock_user(username="other", workspaces=["workspace"])

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.withdraw_file_from_request(
            release_request, UrlPath("group/foo.txt"), user=other
        )


def test_withdraw_file_from_request_already_withdrawn(bll):
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(group="group", path="foo.txt", approved=True),
            factories.request_file(
                group="group", path="withdrawn.txt", filetype=RequestFileType.WITHDRAWN
            ),
        ],
    )

    with pytest.raises(
        exceptions.RequestPermissionDenied, match="already been withdrawn"
    ):
        bll.withdraw_file_from_request(
            release_request, UrlPath("group/withdrawn.txt"), user=author
        )


def _get_request_file(release_request, path):
    """Syntactic sugar to make the tests a little more readable"""
    # refresh
    release_request = factories.refresh_release_request(release_request)
    return release_request.get_request_file_from_output_path(path)


def test_approve_file_not_submitted(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_airlock_user(output_checker=True)

    bll.add_file_to_request(release_request, path, author)
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(exceptions.RequestReviewDenied):
        bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_approve_file_not_your_own(bll):
    release_request, path, author = setup_empty_release_request()

    bll.add_file_to_request(release_request, path, author)
    bll.group_edit(release_request, "default", "foo", "bar", author)
    release_request = bll.get_release_request(release_request.id, author)

    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(exceptions.RequestReviewDenied):
        bll.approve_file(release_request, request_file, author)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(author) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_approve_file_not_checker(bll):
    release_request, path, author = setup_empty_release_request()
    author2 = factories.create_airlock_user("author2", [], False)

    bll.add_file_to_request(release_request, path, author)
    bll.group_edit(release_request, "default", "foo", "bar", author)
    release_request = bll.get_release_request(release_request.id, author)

    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(exceptions.RequestReviewDenied):
        bll.approve_file(release_request, request_file, author2)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(author) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_approve_file_not_part_of_request(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_airlock_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)
    bad_path = UrlPath("path/file2.txt")
    bad_request_file = factories.create_request_file_bad_path(request_file, bad_path)

    with pytest.raises(exceptions.RequestReviewDenied):
        bll.approve_file(release_request, bad_request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_approve_supporting_file(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(),
            factories.request_file(path=path, filetype=RequestFileType.SUPPORTING),
        ],
    )
    checker = factories.create_airlock_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(exceptions.RequestReviewDenied):
        bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_approve_file(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    assert "group" in release_request.filegroups

    checker = factories.create_airlock_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.APPROVED
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_APPROVE,
        user=checker,
        path=request_file.relpath,
        group="group",
    )


def test_approve_file_requires_two_plus_submitted_reviews(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker1 = factories.create_airlock_user("checker1", [], True)
    checker2 = factories.create_airlock_user("checker2", [], True)
    checker3 = factories.create_airlock_user("checker3", [], True)

    request_file = release_request.get_request_file_from_output_path(path)

    bll.approve_file(release_request, request_file, checker1)
    bll.request_changes_to_file(release_request, request_file, checker2)

    # Reviewers must submit their independent review before we can assess
    # a file's decision
    factories.submit_independent_review(release_request, checker1, checker2)
    release_request = factories.refresh_release_request(release_request)
    rfile = _get_request_file(release_request, path)
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.CONFLICTED
    )

    # A 3rd reviewer can tie-break, but they need to also submit their full review
    bll.approve_file(release_request, request_file, checker3)
    rfile = _get_request_file(release_request, path)
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.CONFLICTED
    )

    factories.submit_independent_review(release_request, checker3)
    release_request = factories.refresh_release_request(release_request)
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.APPROVED
    )


def test_request_changes_to_file(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    assert "group" in release_request.filegroups

    checker = factories.create_airlock_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    bll.request_changes_to_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.CHANGES_REQUESTED
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_REQUEST_CHANGES,
        user=checker,
        path=path,
        group="group",
    )


def test_approve_then_request_changes_to_file(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_airlock_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.APPROVED
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    bll.request_changes_to_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.CHANGES_REQUESTED
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


@pytest.mark.parametrize(
    "review", [RequestFileVote.APPROVED, RequestFileVote.CHANGES_REQUESTED]
)
def test_review_then_reset_review_file(bll, review):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_airlock_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    if review == RequestFileVote.APPROVED:
        bll.approve_file(release_request, request_file, checker)
    elif review == RequestFileVote.CHANGES_REQUESTED:
        bll.request_changes_to_file(release_request, request_file, checker)
    else:
        assert False

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == review
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    bll.reset_review_file(release_request, path, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_reset_review_file_no_reviews(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_airlock_user(output_checker=True)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    with pytest.raises(exceptions.FileReviewNotFound):
        bll.reset_review_file(release_request, path, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )


def test_reset_review_file_after_review_submitted(bll):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_airlock_user(output_checker=True)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert (
        rfile.get_decision(release_request.submitted_reviews.keys())
        == RequestFileDecision.INCOMPLETE
    )

    bll.approve_file(release_request, rfile, checker)
    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is RequestFileVote.APPROVED
    factories.submit_independent_review(release_request, checker)
    release_request = factories.refresh_release_request(release_request)

    # After submitting a review, the user can't reset it
    rfile = _get_request_file(release_request, path)
    with pytest.raises(exceptions.RequestReviewDenied):
        bll.reset_review_file(release_request, path, checker)
    assert rfile.get_file_vote_for_user(checker) is RequestFileVote.APPROVED

    # but they can still change their vote
    bll.request_changes_to_file(release_request, rfile, checker)
    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is RequestFileVote.CHANGES_REQUESTED


@pytest.mark.parametrize(
    "votes, decision",
    [
        (["APPROVED", "APPROVED"], "APPROVED"),
        (["CHANGES_REQUESTED", "CHANGES_REQUESTED"], "CHANGES_REQUESTED"),
        (["APPROVED", "CHANGES_REQUESTED"], "CONFLICTED"),
    ],
)
def test_request_file_status_decision(bll, votes, decision):
    path = UrlPath("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )

    for i, vote in enumerate(votes):
        checker = factories.create_airlock_user(f"checker{i}", [], True)
        request_file = release_request.get_request_file_from_output_path(path)

        if vote == "APPROVED":
            bll.approve_file(release_request, request_file, checker)
        else:
            bll.request_changes_to_file(release_request, request_file, checker)

        rfile = _get_request_file(release_request, path)
        assert rfile.get_file_vote_for_user(checker) == RequestFileVote[vote]

        # reviewer must submit review before we can determine a decision
        release_request = factories.refresh_release_request(release_request)
        assert (
            rfile.get_decision(release_request.submitted_reviews.keys())
            == RequestFileDecision.INCOMPLETE
        )

        factories.submit_independent_review(release_request, checker)
        release_request = factories.refresh_release_request(release_request)
        if i == 0:
            assert (
                rfile.get_decision(release_request.submitted_reviews.keys())
                == RequestFileDecision.INCOMPLETE
            )
        else:
            assert (
                rfile.get_decision(release_request.submitted_reviews.keys())
                == RequestFileDecision[decision]
            )


def test_mark_file_undecided(bll):
    # Set up submitted request
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path="file.txt", changes_requested=True)],
    )

    # first default output-checker
    checker = factories.create_airlock_user("output-checker-0")

    # mark file review as undecided
    review = release_request.get_request_file_from_output_path("file.txt").reviews[
        checker.username
    ]
    bll.mark_file_undecided(release_request, review, "file.txt", user=checker)
    release_request = factories.refresh_release_request(release_request)
    review = release_request.get_request_file_from_output_path("file.txt").reviews[
        checker.username
    ]
    assert review.status == RequestFileVote.UNDECIDED


@pytest.mark.parametrize(
    "request_status,file_status,allowed",
    [
        # can only mark undecided for a changes_requested file on a returned request
        (RequestStatus.SUBMITTED, RequestFileVote.CHANGES_REQUESTED, False),
        (RequestStatus.RETURNED, RequestFileVote.APPROVED, False),
        (RequestStatus.RETURNED, RequestFileVote.CHANGES_REQUESTED, True),
    ],
)
def test_mark_file_undecided_permission_errors(
    bll, request_status, file_status, allowed
):
    # Set up file that already has 2 reviews; these are both changes_requested for
    # requests that we want to be in RETURNED status, and approved
    # for SUBMITTED/APPROVED/RELEASED, so we can set the request status
    path = "path/file.txt"
    checkers = factories.get_default_output_checkers()
    release_request = factories.create_request_at_status(
        "workspace",
        status=request_status,
        files=[
            factories.request_file(
                path=path,
                changes_requested=file_status == RequestFileVote.CHANGES_REQUESTED,
                approved=file_status == RequestFileVote.APPROVED,
                checkers=checkers,
            )
        ],
    )

    review = release_request.get_request_file_from_output_path(path).reviews[
        checkers[0].username
    ]
    assert review.status == file_status
    if allowed:
        bll.mark_file_undecided(release_request, review, path, checkers[0])
    else:
        with pytest.raises(exceptions.RequestReviewDenied):
            bll.mark_file_undecided(release_request, review, path, checkers[0])


def test_review_request(bll):
    checker = factories.create_airlock_user("checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(
                path="test.txt", changes_requested=True, checkers=[checker]
            ),
            factories.request_file(path="test1.txt"),
        ],
    )
    # first file already has changed requested, second file is not reviewed
    with pytest.raises(
        exceptions.RequestReviewDenied,
        match="You must review all files to submit your review",
    ):
        bll.review_request(release_request, checker)
    assert "checker" not in release_request.submitted_reviews
    assert release_request.status == RequestStatus.SUBMITTED

    # approved second file
    factories.review_file(
        release_request, UrlPath("test1.txt"), RequestFileVote.APPROVED, checker
    )
    release_request = factories.refresh_release_request(release_request)
    bll.review_request(release_request, checker)
    release_request = factories.refresh_release_request(release_request)
    assert "checker" in release_request.submitted_reviews
    assert release_request.status == RequestStatus.PARTIALLY_REVIEWED

    # re-review
    with pytest.raises(
        exceptions.RequestReviewDenied, match="You have already submitted your review"
    ):
        bll.review_request(release_request, checker)


def test_submit_request_no_output_files(bll):
    checker = factories.create_airlock_user("checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                path="test.txt", filetype=RequestFileType.SUPPORTING
            ),
        ],
    )
    # first file already has changed requested, second file is not reviewed
    with pytest.raises(
        exceptions.RequestPermissionDenied,
        match="Cannot submit request with no output files",
    ):
        bll.submit_request(release_request, checker)


def test_review_request_non_submitted_status(bll):
    checker = factories.create_airlock_user(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.WITHDRAWN,
        withdrawn_after=RequestStatus.PENDING,
        files=[
            factories.request_file(
                path="test.txt",
                checkers=[checker, factories.create_airlock_user(output_checker=True)],
                approved=True,
            ),
        ],
    )
    with pytest.raises(
        exceptions.RequestReviewDenied,
        match="cannot review request in state WITHDRAWN",
    ):
        bll.review_request(release_request, checker)


def test_review_request_non_output_checker(bll):
    user = factories.create_airlock_user("non-output-checker")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="test.txt", approved=True),
        ],
    )
    with pytest.raises(
        exceptions.RequestPermissionDenied,
        match="You do not have permission to review this request",
    ):
        bll.review_request(release_request, user)


def test_review_request_more_than_2_checkers(bll):
    checkers = [
        factories.create_airlock_user(f"checker_{i}", [], True) for i in range(3)
    ]
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="test.txt", approved=True, checkers=checkers),
        ],
    )
    bll.review_request(release_request, checkers[0])
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.PARTIALLY_REVIEWED

    bll.review_request(release_request, checkers[1])
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.REVIEWED

    bll.review_request(release_request, checkers[2])
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.REVIEWED
    assert len(release_request.submitted_reviews) == 3


def test_review_request_race_condition(bll):
    """
    In a potential race condition, a
    """
    checkers = [
        factories.create_airlock_user("checker", output_checker=True),
        factories.create_airlock_user("checker1", output_checker=True),
        factories.create_airlock_user("checker2", output_checker=True),
        factories.create_airlock_user("checker3", output_checker=True),
    ]
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="test.txt", approved=True, checkers=checkers),
            factories.request_file(
                path="test1.txt", changes_requested=True, checkers=checkers
            ),
        ],
    )
    # first checker submits review
    bll.review_request(release_request, checkers[0])

    # mock race condition by patching the number of submitted reviews to return 1 initially
    # This is called AFTER recording the review. If it's a first review, we expect there to
    # be 1 submitted reivew, if it's a second review we expect there to be 2.
    # However in a race condition, this could be review 2, but the count is incorrectly
    # retrieved as 1
    with patch(
        "airlock.business_logic.ReleaseRequest.submitted_reviews_count"
    ) as submitted_reviews:
        submitted_reviews.side_effect = [1, 2]
        bll.review_request(release_request, checkers[1])

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.REVIEWED

    # Similarly, there could be a race between reviewer 2 and 3. For reviewer 2, we want to move the
    # status to REVIEWED, for review 3 we should do nothing
    with patch(
        "airlock.business_logic.ReleaseRequest.submitted_reviews_count"
    ) as submitted_reviews:
        submitted_reviews.side_effect = [2, 3]
        bll.review_request(release_request, checkers[2])
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.REVIEWED

    # The request must be in a reviewed state (part or fully)
    # Set status to submitted (mock check status to allow the invalid transition)
    with patch("airlock.business_logic.BusinessLogicLayer.check_status"):
        bll.set_status(release_request, RequestStatus.SUBMITTED, checkers[0])

    with pytest.raises(exceptions.InvalidStateTransition):
        with patch(
            "airlock.business_logic.ReleaseRequest.submitted_reviews_count"
        ) as submitted_reviews:
            submitted_reviews.side_effect = [2, 4]
            bll.review_request(release_request, checkers[3])


# add DAL method names to this if they do not require auditing
DAL_AUDIT_EXCLUDED = {
    "get_release_request",
    "get_requests_for_workspace",
    "get_active_requests_for_workspace_by_user",
    "get_audit_log",
    "get_requests_by_status",
    "get_requests_authored_by_user",
    "get_approved_requests",
    "delete_file_from_request",
    "record_review",
    "start_new_turn",
    "get_released_files_for_workspace",
    "get_released_files_for_request",
    "register_file_upload_attempt",
    "reset_file_upload_attempts",
}


def test_dal_methods_have_audit_event_parameter():
    """Ensure all our DAL methods take an AuditEvent parameter by default."""

    dal_functions = {
        name: func
        for name, func in inspect.getmembers(
            DataAccessLayerProtocol, predicate=inspect.isfunction
        )
        if not name.startswith("__") and name not in DAL_AUDIT_EXCLUDED
    }

    for name, func in dal_functions.items():
        signature = inspect.signature(func)
        arg_annotations = set(p.annotation for p in signature.parameters.values())
        assert "AuditEvent" in arg_annotations, (
            f"DataAccessLayerProtocol method {name} does not have an AuditEvent parameter"
        )


def test_group_edit_author(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)

    assert release_request.filegroups["group"].context == ""
    assert release_request.filegroups["group"].controls == ""

    bll.group_edit(release_request, "group", "foo", "bar", author)

    release_request = factories.refresh_release_request(release_request)
    assert release_request.filegroups["group"].context == "foo"
    assert release_request.filegroups["group"].controls == "bar"

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[0].request == release_request.id
    assert audit_log[0].type == AuditEventType.REQUEST_EDIT
    assert audit_log[0].user == author.username
    assert audit_log[0].extra["group"] == "group"
    assert audit_log[0].extra["context"] == "foo"
    assert audit_log[0].extra["controls"] == "bar"


@pytest.mark.parametrize(
    "org,repo,sent_in_notification", [(None, None, False), ("test", "foo", True)]
)
def test_notifications_org_repo(
    bll, mock_notifications, settings, org, repo, sent_in_notification
):
    settings.AIRLOCK_OUTPUT_CHECKING_ORG = org
    settings.AIRLOCK_OUTPUT_CHECKING_REPO = repo
    author = factories.create_airlock_user("author", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    # notifications endpoint called when request submitted
    notification_responses = parse_notification_responses(mock_notifications)
    assert notification_responses["count"] == 1
    (notification,) = notification_responses["request_json"]
    expected_notification = {
        "event_type": "request_submitted",
        "workspace": "workspace",
        "request": release_request.id,
        "request_author": "author",
        "user": "author",
        "updates": None,
    }
    if sent_in_notification:
        expected_notification.update({"org": "test", "repo": "foo"})
    assert notification == expected_notification


def test_group_edit_not_author(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    other = factories.create_airlock_user("other", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.group_edit(release_request, "group", "foo", "bar", other)


@pytest.mark.parametrize(
    "status",
    [
        RequestStatus.SUBMITTED,
        RequestStatus.PARTIALLY_REVIEWED,
        RequestStatus.REVIEWED,
        RequestStatus.APPROVED,
        RequestStatus.REJECTED,
        RequestStatus.WITHDRAWN,
    ],
)
def test_group_edit_not_editable_by_author(bll, status, mock_old_api):
    author = factories.create_airlock_user("author", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[factories.request_file(approved=True)],
        withdrawn_after=RequestStatus.PENDING,
    )

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.group_edit(release_request, "group", "foo", "bar", author)


def test_group_edit_bad_group(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    with pytest.raises(exceptions.FileNotFound):
        bll.group_edit(release_request, "notexist", "foo", "bar", author)


@pytest.mark.parametrize(
    "status,notification_count",
    [
        (RequestStatus.PENDING, 0),
        # Currently no notifications are sent for comments. The only notification
        # sent in this test is for summitting request
        (RequestStatus.SUBMITTED, 1),
    ],
)
def test_group_comment_create_success(
    bll, mock_notifications, status, notification_count
):
    author = factories.create_airlock_user("author", ["workspace"], False)
    checker = factories.create_airlock_user("checker", ["workspace"], True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert release_request.filegroups["group"].comments == []

    # add author comment while request is PENDING (author can't comment in SUBMITTED)
    bll.group_comment_create(
        release_request, "group", "public", Visibility.PUBLIC, author
    )

    # move to submitted if necessary
    if status == RequestStatus.SUBMITTED:
        bll.set_status(release_request, status, author)

    # check all visibilities
    bll.group_comment_create(
        release_request, "group", "private", Visibility.PRIVATE, checker
    )

    release_request = factories.refresh_release_request(release_request)

    notification_responses = parse_notification_responses(mock_notifications)
    assert notification_responses["count"] == notification_count
    if notification_count > 0:
        assert (
            notification_responses["request_json"][0]["event_type"]
            == "request_submitted"
        )

    comments = release_request.filegroups["group"].comments
    assert comments[0].comment == "public"
    assert comments[0].visibility == Visibility.PUBLIC
    assert comments[0].author == "author"
    assert comments[1].comment == "private"
    assert comments[1].visibility == Visibility.PRIVATE
    assert comments[1].author == "checker"

    audit_log = bll._dal.get_audit_log(request=release_request.id)

    if status == RequestStatus.PENDING:
        author_log = audit_log[0]
        checker_log = audit_log[1]
    else:
        author_log = audit_log[0]
        checker_log = audit_log[2]

    assert author_log.request == release_request.id
    assert author_log.type == AuditEventType.REQUEST_COMMENT
    assert author_log.user == checker.username
    assert author_log.extra["group"] == "group"
    assert author_log.extra["comment"] == "private"

    assert checker_log.request == release_request.id
    assert checker_log.type == AuditEventType.REQUEST_COMMENT
    assert checker_log.user == "author"
    assert checker_log.extra["group"] == "group"
    assert checker_log.extra["comment"] == "public"


def test_group_comment_create_permissions_pending_request(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    collaborator = factories.create_airlock_user("collaborator", ["workspace"], False)
    other = factories.create_airlock_user("other", ["other"], False)
    checker = factories.create_airlock_user("checker", ["other"], True)
    collaborator_checker = factories.create_airlock_user(
        "collaborator_checker", ["workspace"], True
    )

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert len(release_request.filegroups["group"].comments) == 0

    # other user with no access to workspace can never comment
    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.group_comment_create(
            release_request, "group", "question?", Visibility.PUBLIC, other
        )

    # output-checker with no access to workspace can only comment in review statuses
    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.group_comment_create(
            release_request, "group", "checker", Visibility.PUBLIC, checker
        )

    # author can comment
    bll.group_comment_create(
        release_request, "group", "author comment", Visibility.PUBLIC, author
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1

    # collaborator can comment as if author
    bll.group_comment_create(
        release_request,
        "group",
        "collaborator comment",
        Visibility.PUBLIC,
        collaborator,
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 2

    # collaborator checker can comment as if author
    bll.group_comment_create(
        release_request,
        "group",
        "collaborator comment",
        Visibility.PUBLIC,
        collaborator_checker,
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 3


def test_group_comment_delete_success(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    other = factories.create_airlock_user("other", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert release_request.filegroups["group"].comments == []

    bll.group_comment_create(
        release_request, "group", "typo comment", Visibility.PUBLIC, other
    )
    bll.group_comment_create(
        release_request, "group", "not-a-typo comment", Visibility.PUBLIC, other
    )

    release_request = factories.refresh_release_request(release_request)

    bad_comment = release_request.filegroups["group"].comments[0]
    good_comment = release_request.filegroups["group"].comments[1]

    assert bad_comment.comment == "typo comment"
    assert bad_comment.author == "other"
    assert good_comment.comment == "not-a-typo comment"
    assert good_comment.author == "other"

    bll.group_comment_delete(release_request, "group", bad_comment.id, other)

    release_request = factories.refresh_release_request(release_request)

    current_comment = release_request.filegroups["group"].comments[0]
    assert current_comment.comment == "not-a-typo comment"
    assert current_comment.author == "other"

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[2].request == release_request.id
    assert audit_log[2].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[2].user == other.username
    assert audit_log[2].extra["group"] == "group"
    assert audit_log[2].extra["comment"] == "typo comment"

    assert audit_log[1].request == release_request.id
    assert audit_log[1].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[1].user == other.username
    assert audit_log[1].extra["group"] == "group"
    assert audit_log[1].extra["comment"] == "not-a-typo comment"

    assert audit_log[0].request == release_request.id
    assert audit_log[0].type == AuditEventType.REQUEST_COMMENT_DELETE
    assert audit_log[0].user == other.username
    assert audit_log[0].extra["group"] == "group"
    assert audit_log[0].extra["comment"] == "typo comment"


def test_group_comment_visibility_public_success(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    output_checker = factories.create_airlock_user("checker", [], True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert release_request.filegroups["group"].comments == []

    bll.group_comment_create(
        release_request, "group", "private comment", Visibility.PRIVATE, output_checker
    )
    bll.group_comment_create(
        release_request,
        "group",
        "to-be-public comment",
        Visibility.PRIVATE,
        output_checker,
    )

    release_request = factories.refresh_release_request(release_request)

    private_comment = release_request.filegroups["group"].comments[0]
    public_comment = release_request.filegroups["group"].comments[1]

    assert private_comment.comment == "private comment"
    assert private_comment.author == "checker"
    assert public_comment.comment == "to-be-public comment"
    assert public_comment.author == "checker"

    bll.group_comment_visibility_public(
        release_request, "group", public_comment.id, output_checker
    )

    release_request = factories.refresh_release_request(release_request)

    current_comments = release_request.filegroups["group"].comments
    assert current_comments[0].comment == "private comment"
    assert current_comments[0].visibility == Visibility.PRIVATE
    assert current_comments[1].comment == "to-be-public comment"
    assert current_comments[1].visibility == Visibility.PUBLIC

    audit_log = bll._dal.get_audit_log(request=release_request.id)
    assert audit_log[2].request == release_request.id
    assert audit_log[2].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[2].user == output_checker.username
    assert audit_log[2].extra["group"] == "group"
    assert audit_log[2].extra["comment"] == "private comment"

    assert audit_log[1].request == release_request.id
    assert audit_log[1].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[1].user == output_checker.username
    assert audit_log[1].extra["group"] == "group"
    assert audit_log[1].extra["comment"] == "to-be-public comment"

    assert audit_log[0].request == release_request.id
    assert audit_log[0].type == AuditEventType.REQUEST_COMMENT_VISIBILITY_PUBLIC
    assert audit_log[0].user == output_checker.username
    assert audit_log[0].extra["group"] == "group"
    assert audit_log[0].extra["comment"] == "to-be-public comment"


def test_group_comment_visibility_public_bad_user(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    # checker who does not have access to workspace
    checker = factories.create_airlock_user("checker1", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt", approved=True)],
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
        withdrawn_after=RequestStatus.PENDING,
    )

    checker_comment = release_request.filegroups["group"].comments[0]
    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.group_comment_visibility_public(
            release_request, "group", checker_comment.id, author
        )


def test_group_comment_visibility_public_bad_round(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)
    # checker who does not have access to workspace
    checker = factories.create_airlock_user("checker1", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[factories.request_file("group", "test/file.txt", approved=True)],
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
        withdrawn_after=RequestStatus.PENDING,
    )

    assert release_request.review_turn == 1
    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    assert release_request.review_turn == 2

    checker_comment = release_request.filegroups["group"].comments[0]
    assert checker_comment.review_turn == 1

    with pytest.raises(exceptions.RequestPermissionDenied):
        bll.group_comment_visibility_public(
            release_request, "group", checker_comment.id, checker
        )


@pytest.mark.parametrize(
    "status,checker_can_change_visibility",
    [
        (RequestStatus.PENDING, False),
        (RequestStatus.SUBMITTED, True),
        (RequestStatus.PARTIALLY_REVIEWED, True),
        (RequestStatus.REVIEWED, True),
        (RequestStatus.RETURNED, False),
        (RequestStatus.APPROVED, False),
        (RequestStatus.WITHDRAWN, False),
        (RequestStatus.REJECTED, False),
    ],
)
def test_group_comment_visibility_public_permissions(
    bll, mock_old_api, status, checker_can_change_visibility
):
    author = factories.create_airlock_user("author", ["workspace"], False)
    # checker who does not have access to workspace
    checker = factories.create_airlock_user("checker1", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[factories.request_file("group", "test/file.txt", approved=True)],
        checker=checker,
        checker_comments=[("group", "checker comment", Visibility.PRIVATE)],
        withdrawn_after=RequestStatus.PENDING,
    )

    if not release_request.filegroups["group"].comments:
        # there is no comment as it's not possible for an output checker to have
        # commented yet
        return

    checker_comment = release_request.filegroups["group"].comments[0]

    release_request = factories.refresh_release_request(release_request)

    if checker_can_change_visibility:
        bll.group_comment_visibility_public(
            release_request, "group", checker_comment.id, checker
        )
        release_request = factories.refresh_release_request(release_request)
        assert (
            release_request.filegroups["group"].comments[0].visibility
            == Visibility.PUBLIC
        )
    else:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.group_comment_visibility_public(
                release_request, "group", checker_comment.id, checker
            )


@pytest.mark.parametrize(
    "status,author_can_delete,checker_can_delete",
    [
        (RequestStatus.PENDING, True, False),
        (RequestStatus.SUBMITTED, False, True),
        (RequestStatus.PARTIALLY_REVIEWED, False, True),
        (RequestStatus.REVIEWED, False, True),
        (RequestStatus.RETURNED, True, False),
        (RequestStatus.APPROVED, False, False),
        (RequestStatus.WITHDRAWN, False, False),
        (RequestStatus.REJECTED, False, False),
    ],
)
def test_group_comment_delete_permissions(
    bll, mock_old_api, status, author_can_delete, checker_can_delete
):
    author = factories.create_airlock_user("author", ["workspace"], False)
    collaborator = factories.create_airlock_user("collaborator", ["workspace"], False)
    other = factories.create_airlock_user("other", ["other"], False)
    # checker who does not have access to workspace
    checker = factories.create_airlock_user("checker", [], True)

    # users can never delete someone else's comment
    not_permitted_to_delete_author = [collaborator, other, checker]
    not_permitted_to_delete_checker = [author, collaborator, other]
    # depending on status, user may not be able to delete their own
    if not author_can_delete:
        not_permitted_to_delete_author.append(author)
    if not checker_can_delete:
        not_permitted_to_delete_checker.append(checker)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[factories.request_file("group", "test/file.txt", approved=True)],
        withdrawn_after=RequestStatus.PENDING,
    )
    # patch the comment creation permissions so we can set up comments for both
    # author and checker
    with patch("airlock.business_logic.permissions.check_user_can_comment_on_group"):
        bll.group_comment_create(
            release_request, "group", "author comment", Visibility.PUBLIC, author
        )
        bll.group_comment_create(
            release_request, "group", "checker comment", Visibility.PUBLIC, checker
        )

    release_request = factories.refresh_release_request(release_request)
    test_comment, checker_comment = release_request.filegroups["group"].comments

    for user in not_permitted_to_delete_author:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.group_comment_delete(release_request, "group", test_comment.id, user)
    release_request = factories.refresh_release_request(release_request)
    assert len(release_request.filegroups["group"].comments) == 2

    for user in not_permitted_to_delete_checker:
        with pytest.raises(exceptions.RequestPermissionDenied):
            bll.group_comment_delete(release_request, "group", checker_comment.id, user)
    release_request = factories.refresh_release_request(release_request)
    comment_count = len(release_request.filegroups["group"].comments)
    assert comment_count == 2

    if author_can_delete:
        bll.group_comment_delete(release_request, "group", test_comment.id, author)
        release_request = factories.refresh_release_request(release_request)
        assert len(release_request.filegroups["group"].comments) == comment_count - 1
        comment_count -= 1

    if checker_can_delete:
        bll.group_comment_delete(release_request, "group", checker_comment.id, checker)
        release_request = factories.refresh_release_request(release_request)
        assert len(release_request.filegroups["group"].comments) == comment_count - 1


def test_group_comment_delete_invalid_params(bll):
    author = factories.create_airlock_user("author", ["workspace"], False)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    # delete a comment that doesn't exist yet
    with pytest.raises(exceptions.APIException):
        bll.group_comment_delete(release_request, "group", 1, author)

    bll.group_comment_create(
        release_request, "group", "author comment", Visibility.PUBLIC, author
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1
    test_comment = release_request.filegroups["group"].comments[0]

    # delete a comment with a bad group
    with pytest.raises(exceptions.APIException):
        bll.group_comment_delete(release_request, "badgroup", test_comment.id, author)

    assert len(release_request.filegroups["group"].comments) == 1

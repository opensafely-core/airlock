import inspect
import json
from hashlib import file_digest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.conf import settings

import old_api
from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    CodeRepo,
    DataAccessLayerProtocol,
    FileReview,
    FileReviewStatus,
    RequestFileType,
    RequestStatus,
    UrlPath,
    Workspace,
)
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


def test_workspace_container():
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.html")

    assert workspace.root() == settings.WORKSPACE_DIR / "workspace"
    assert workspace.get_id() == "workspace"
    assert (
        workspace.get_url("foo/bar.html") == "/workspaces/view/workspace/foo/bar.html"
    )
    assert (
        "/workspaces/content/workspace/foo/bar.html?cache_id="
        in workspace.get_contents_url(UrlPath("foo/bar.html"))
    )

    assert workspace.request_filetype("path") is None


def test_workspace_from_directory_errors():
    with pytest.raises(BusinessLogicLayer.WorkspaceNotFound):
        Workspace.from_directory("workspace", {})

    (settings.WORKSPACE_DIR / "workspace").mkdir()
    with pytest.raises(BusinessLogicLayer.ManifestFileError):
        Workspace.from_directory("workspace")

    manifest_path = settings.WORKSPACE_DIR / "workspace/metadata/manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(":")
    with pytest.raises(BusinessLogicLayer.ManifestFileError):
        Workspace.from_directory("workspace")


def test_workspace_request_filetype(bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.txt")
    assert workspace.request_filetype("foo/bar.txt") is None


def test_workspace_manifest_for_file():
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.csv", "c1,c2,c3\n1,2,3\n4,5,6")

    file_manifest = workspace.get_manifest_for_file(UrlPath("foo/bar.csv"))
    assert file_manifest["row_count"] == 2
    assert file_manifest["col_count"] == 3


def test_workspace_manifest_for_file_not_found(bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.txt")
    manifest_path = workspace.root() / "metadata/manifest.json"
    manifest_data = json.loads(manifest_path.read_text())
    manifest_data["outputs"] = {}
    manifest_path.write_text(json.dumps(manifest_data))

    workspace = bll.get_workspace(
        "workspace", factories.create_user(workspaces=["workspace"])
    )
    with pytest.raises(BusinessLogicLayer.ManifestFileError):
        workspace.get_manifest_for_file(UrlPath("foo/bar.txt"))


def test_request_container(mock_notifications):
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(release_request, "group", "bar.html")

    assert release_request.root() == settings.REQUEST_DIR / "workspace/id"
    assert release_request.get_id() == "id"
    assert (
        release_request.get_url("group/bar.html") == "/requests/view/id/group/bar.html"
    )
    assert (
        "/requests/content/id/group/bar.html?cache_id="
        in release_request.get_contents_url(UrlPath("group/bar.html"))
    )
    assert_no_notifications(mock_notifications)


def test_request_file_manifest_data(mock_notifications, bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "bar.txt")
    user = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_release_request(workspace, user=user)

    # modify the manifest data to known values for asserts
    manifest_path = workspace.root() / "metadata/manifest.json"
    manifest_data = json.loads(manifest_path.read_text())
    file_manifest = manifest_data["outputs"]["bar.txt"]
    file_manifest.update(
        {
            "job_id": "job-bar",
            "size": 10,
            "commit": "abcd",
            "timestamp": 1715000000,
        }
    )
    manifest_path.write_text(json.dumps(manifest_data))

    bll.add_file_to_request(release_request, UrlPath("bar.txt"), user, "group")

    request_file = release_request.filegroups["group"].files[UrlPath("bar.txt")]
    assert request_file.timestamp == 1715000000
    assert request_file.commit == "abcd"
    assert request_file.job_id == "job-bar"
    assert request_file.size == 10
    assert request_file.row_count is None
    assert request_file.col_count is None


def test_request_file_manifest_data_content_hash_mismatch(mock_notifications, bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "bar.txt")
    user = factories.create_user(workspaces=["workspace"])
    release_request = factories.create_release_request(workspace, user=user)

    # modify the manifest data to known values for asserts
    manifest = workspace.root() / "metadata/manifest.json"
    manifest_data = json.loads(manifest.read_text())
    file_manifest = manifest_data["outputs"]["bar.txt"]
    file_manifest.update(
        {
            "content_hash": file_digest(BytesIO(b"foo"), "sha256").hexdigest(),
        }
    )
    manifest.write_text(json.dumps(manifest_data))

    with pytest.raises(AssertionError):
        bll.add_file_to_request(release_request, UrlPath("bar.txt"), user, "group")


def test_code_repo_container():
    repo = factories.create_repo("workspace")

    assert repo.get_id() == f"workspace@{repo.commit[:7]}"
    assert (
        repo.get_url(UrlPath("project.yaml"))
        == f"/code/view/workspace/{repo.commit}/project.yaml"
    )
    assert (
        f"/code/contents/workspace/{repo.commit}/project.yaml?cache_id="
        in repo.get_contents_url(UrlPath("project.yaml"))
    )

    assert repo.request_filetype("path") == RequestFileType.CODE


@pytest.mark.parametrize("output_checker", [False, True])
def test_provider_get_workspaces_for_user(bll, output_checker):
    factories.create_workspace("foo")
    factories.create_workspace("bar")
    factories.create_workspace("not-allowed")
    workspaces = {
        "foo": {"project": "project 1"},
        "bar": {"project": "project 2"},
        "not-exists": {"project": "project 3"},
    }
    user = factories.create_user(workspaces=workspaces, output_checker=output_checker)

    assert bll.get_workspaces_for_user(user) == [
        bll.get_workspace("foo", user),
        bll.get_workspace("bar", user),
    ]


@pytest.fixture
def mock_old_api(monkeypatch):
    monkeypatch.setattr(
        old_api, "create_release", MagicMock(autospec=old_api.create_release)
    )
    monkeypatch.setattr(old_api, "upload_file", MagicMock(autospec=old_api.upload_file))


def test_provider_request_release_files_request_not_approved(bll, mock_notifications):
    author = factories.create_user("author", ["workspace"])
    checker = factories.create_user("checker", [], output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )

    with pytest.raises(bll.InvalidStateTransition):
        bll.release_files(release_request, checker)

    # Note factories.create_release_request bypasses bll.set_status, so
    # doesn't trigger notifications
    # Failed release attempt does not notify
    assert_no_notifications(mock_notifications)


def test_provider_request_release_files_invalid_file_type(bll, mock_notifications):
    author = factories.create_user("author", ["workspace"])
    checker = factories.create_user("checker", [], output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )

    # mock the LEVEL4_FILE_TYPES so that we can add this invalid file to the
    # request
    relpath = Path("test/file.foo")
    with patch("airlock.utils.LEVEL4_FILE_TYPES", [".foo"]):
        factories.write_request_file(
            release_request, "group", relpath, "test", approved=True
        )

    release_request = factories.refresh_release_request(release_request)
    factories.bll.set_status(release_request, RequestStatus.APPROVED, checker)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.release_files(release_request, checker)
    assert_last_notification(mock_notifications, "request_approved")


def test_provider_request_release_files(mock_old_api, mock_notifications):
    old_api.create_release.return_value = "jobserver_id"
    author = factories.create_user("author", ["workspace"])
    checker = factories.create_user("checker", [], output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    relpath = Path("test/file.txt")
    factories.write_request_file(
        release_request, "group", relpath, "test", approved=True
    )
    # Add a supporting file, which should NOT be released
    supporting_relpath = Path("test/supporting_file.txt")
    factories.write_request_file(
        release_request,
        "group",
        supporting_relpath,
        "test",
        filetype=RequestFileType.SUPPORTING,
    )
    factories.bll.set_status(release_request, RequestStatus.APPROVED, checker)

    abspath = release_request.abspath("group" / relpath)

    bll = BusinessLogicLayer(data_access_layer=Mock())
    bll.release_files(release_request, checker)

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

    old_api.create_release.assert_called_once_with(
        "workspace", json.dumps(expected_json), checker.username
    )
    old_api.upload_file.assert_called_once_with(
        "jobserver_id", relpath, abspath, checker.username
    )

    notification_responses = parse_notification_responses(mock_notifications)
    # Notifications expected for:
    # - write file x2
    # - set status to test_set_status_approved
    # - set status to released
    assert notification_responses["count"] == 4
    request_json = notification_responses["request_json"]
    assert request_json[0]["event_type"] == "request_updated"
    assert request_json[1]["event_type"] == "request_updated"
    assert request_json[2]["event_type"] == "request_approved"
    assert request_json[3]["event_type"] == "request_released"


def test_provider_get_requests_for_workspace(bll):
    user = factories.create_user("test", ["workspace", "workspace2"])
    other_user = factories.create_user("other", ["workspace"])
    factories.create_release_request("workspace", user, id="r1")
    factories.create_release_request("workspace2", user, id="r2")
    factories.create_release_request("workspace", other_user, id="r3")

    assert [r.id for r in bll.get_requests_for_workspace("workspace", user)] == [
        "r1",
        "r3",
    ]


def test_provider_get_requests_for_workspace_bad_user(bll):
    user = factories.create_user("test", ["workspace"])
    other_user = factories.create_user("other", ["workspace_2"])
    factories.create_release_request("workspace", user, id="r1")
    factories.create_release_request("workspace_2", other_user, id="r2")

    with pytest.raises(bll.RequestPermissionDenied):
        bll.get_requests_for_workspace("workspace", other_user)


def test_provider_get_requests_for_workspace_output_checker(bll):
    user = factories.create_user("test", ["workspace"])
    other_user = factories.create_user("other", [], True)
    factories.create_release_request("workspace", user, id="r1")

    assert [r.id for r in bll.get_requests_for_workspace("workspace", other_user)] == [
        "r1",
    ]


def test_provider_get_requests_authored_by_user(bll):
    user = factories.create_user("test", ["workspace"])
    other_user = factories.create_user("other", ["workspace"])
    factories.create_release_request("workspace", user, id="r1")
    factories.create_release_request("workspace", other_user, id="r2")

    assert [r.id for r in bll.get_requests_authored_by_user(user)] == ["r1"]


@pytest.mark.parametrize(
    "output_checker, expected",
    [
        # A non-output checker never sees outstanding requests
        (False, []),
        # An output checker only sees outstanding requests that
        # they did not author
        (True, ["r1"]),
    ],
)
def test_provider_get_outstanding_requests_for_review(output_checker, expected, bll):
    user = factories.create_user("test", ["workspace"], output_checker)
    other_user = factories.create_user("other", ["workspace"], False)
    # request created by another user, status submitted
    factories.create_release_request(
        "workspace", other_user, id="r1", status=RequestStatus.SUBMITTED
    )

    # requests not visible to output checker
    # status submitted, but authored by output checker
    factories.create_release_request(
        "workspace", user, id="r2", status=RequestStatus.SUBMITTED
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
        user_n = factories.create_user(f"test_{i}", [ws])
        factories.create_release_request(ws, user_n, status=status)

    assert set(r.id for r in bll.get_outstanding_requests_for_review(user)) == set(
        expected
    )


def test_provider_get_current_request_for_user(bll):
    workspace = factories.create_workspace("workspace")
    user = factories.create_user("testuser", ["workspace"], False)
    other_user = factories.create_user("otheruser", ["workspace"], False)

    assert bll.get_current_request("workspace", user) is None

    factories.create_release_request(workspace, other_user)
    assert bll.get_current_request("workspace", user) is None

    release_request = bll.get_or_create_current_request("workspace", user)
    assert release_request.workspace == "workspace"
    assert release_request.author == user.username

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log == [
        AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_CREATE,
            user=user,
        )
    ]

    # reach around an simulate 2 active requests for same user
    bll._create_release_request(author=user, workspace="workspace")

    with pytest.raises(Exception):
        bll.get_current_request("workspace", user)


def test_provider_get_current_request_for_former_user(bll):
    factories.create_workspace("workspace")
    user = factories.create_user("testuser", ["workspace"], False)

    assert bll.get_current_request("workspace", user) is None

    release_request = bll.get_or_create_current_request("workspace", user)
    assert release_request.workspace == "workspace"
    assert release_request.author == user.username

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log == [
        AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_CREATE,
            user=user,
        )
    ]

    # let's pretend the user no longer has permission to access the workspace
    former_user = factories.create_user("testuser", [], False)

    with pytest.raises(Exception):
        bll.get_current_request("workspace", former_user)


def test_provider_get_current_request_for_user_output_checker(bll):
    """Output checker must have explict workspace permissions to create requests."""
    factories.create_workspace("workspace")
    user = factories.create_user("output_checker", [], True)

    with pytest.raises(bll.RequestPermissionDenied):
        bll.get_or_create_current_request("workspace", user)


@pytest.mark.parametrize(
    "current,future,valid_author,valid_checker",
    [
        (RequestStatus.PENDING, RequestStatus.SUBMITTED, True, False),
        (RequestStatus.PENDING, RequestStatus.WITHDRAWN, True, False),
        (RequestStatus.PENDING, RequestStatus.APPROVED, False, False),
        (RequestStatus.PENDING, RequestStatus.REJECTED, False, False),
        (RequestStatus.PENDING, RequestStatus.RELEASED, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.APPROVED, False, True),
        (RequestStatus.SUBMITTED, RequestStatus.REJECTED, False, True),
        (RequestStatus.SUBMITTED, RequestStatus.WITHDRAWN, True, False),
        (RequestStatus.SUBMITTED, RequestStatus.PENDING, True, False),
        (RequestStatus.SUBMITTED, RequestStatus.RELEASED, False, False),
        (RequestStatus.APPROVED, RequestStatus.RELEASED, False, True),
        (RequestStatus.APPROVED, RequestStatus.REJECTED, False, False),
        (RequestStatus.APPROVED, RequestStatus.WITHDRAWN, False, False),
        (RequestStatus.REJECTED, RequestStatus.PENDING, False, False),
        (RequestStatus.REJECTED, RequestStatus.SUBMITTED, False, False),
        (RequestStatus.REJECTED, RequestStatus.APPROVED, False, True),
        (RequestStatus.REJECTED, RequestStatus.WITHDRAWN, False, False),
        (RequestStatus.RELEASED, RequestStatus.REJECTED, False, False),
        (RequestStatus.RELEASED, RequestStatus.PENDING, False, False),
        (RequestStatus.RELEASED, RequestStatus.SUBMITTED, False, False),
        (RequestStatus.RELEASED, RequestStatus.APPROVED, False, False),
        (RequestStatus.RELEASED, RequestStatus.REJECTED, False, False),
        (RequestStatus.RELEASED, RequestStatus.WITHDRAWN, False, False),
    ],
)
def test_set_status(current, future, valid_author, valid_checker, bll):
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user("checker", [], True)
    audit_type = bll.STATUS_AUDIT_EVENT[future]
    release_request1 = factories.create_release_request(
        "workspace1", user=author, status=current
    )
    release_request2 = factories.create_release_request(
        "workspace2", user=author, status=current
    )

    if valid_author:
        bll.set_status(release_request1, future, user=author)
        assert release_request1.status == future
        audit_log = bll.get_audit_log(request=release_request1.id)
        assert audit_log[0].type == audit_type
        assert audit_log[0].user == author.username
        assert audit_log[0].request == release_request1.id
        assert audit_log[0].workspace == "workspace1"
    else:
        with pytest.raises((bll.InvalidStateTransition, bll.RequestPermissionDenied)):
            bll.set_status(release_request1, future, user=author)

    if valid_checker:
        if current == RequestStatus.SUBMITTED:
            factories.write_request_file(
                release_request2, "group", "test/file.txt", approved=True
            )
            release_request2 = factories.refresh_release_request(release_request2)

        if current == RequestStatus.REJECTED:
            # We cannot add files to a rejected request, so re-create the request
            release_request2 = factories.create_release_request(
                "workspace2", user=author, status=RequestStatus.SUBMITTED
            )
            factories.write_request_file(
                release_request2, "group", "test/file.txt", approved=True
            )
            bll.set_status(release_request2, current, user=checker)
            release_request2 = factories.refresh_release_request(release_request2)

        bll.set_status(release_request2, future, user=checker)
        assert release_request2.status == future

        audit_log = bll.get_audit_log(request=release_request2.id)
        assert audit_log[0].type == audit_type
        assert audit_log[0].user == checker.username
        assert audit_log[0].request == release_request2.id
        assert audit_log[0].workspace == "workspace2"
    else:
        with pytest.raises((bll.InvalidStateTransition, bll.RequestPermissionDenied)):
            bll.set_status(release_request2, future, user=checker)


@pytest.mark.parametrize(
    "current,future,user,notification_event_type",
    [
        (RequestStatus.PENDING, RequestStatus.SUBMITTED, "author", "request_submitted"),
        (
            RequestStatus.SUBMITTED,
            RequestStatus.APPROVED,
            "checker",
            "request_approved",
        ),
        (
            RequestStatus.SUBMITTED,
            RequestStatus.REJECTED,
            "checker",
            "request_rejected",
        ),
        (
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
            "author",
            "request_withdrawn",
        ),
        (RequestStatus.APPROVED, RequestStatus.RELEASED, "checker", "request_released"),
    ],
)
def test_set_status_notifications(
    current, future, user, notification_event_type, bll, mock_notifications
):
    users = {
        "author": factories.create_user("author", ["workspace"], False),
        "checker": factories.create_user("checker", [], True),
    }
    release_request = factories.create_release_request(
        "workspace", user=users["author"], status=RequestStatus.PENDING
    )
    factories.write_request_file(
        release_request, "group", "test/file.txt", approved=True
    )
    release_request = factories.refresh_release_request(release_request)

    if current == RequestStatus.SUBMITTED:
        bll.set_status(release_request, RequestStatus.SUBMITTED, user=users["author"])
    elif current == RequestStatus.APPROVED:
        bll.set_status(release_request, RequestStatus.SUBMITTED, user=users["author"])
        bll.set_status(release_request, RequestStatus.APPROVED, user=users["checker"])

    bll.set_status(release_request, future, users[user])
    assert_last_notification(mock_notifications, notification_event_type)


def test_notification_error(bll, notifications_stubber, caplog):
    mock_notifications = notifications_stubber(
        json={"status": "error", "message": "something went wrong"}
    )
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request, "group", "test/file.txt", approved=True
    )
    release_request = factories.refresh_release_request(release_request)
    bll.set_status(release_request, RequestStatus.SUBMITTED, author)
    notifications_responses = parse_notification_responses(mock_notifications)
    assert (
        notifications_responses["request_json"][-1]["event_type"] == "request_submitted"
    )
    # Nothing errors, but we log the notification error message
    assert caplog.records[-1].levelname == "ERROR"
    assert (
        caplog.records[-1].message == "Error sending notification: something went wrong"
    )


@pytest.mark.parametrize("files_approved", (True, False))
def test_set_status_approved(files_approved, bll, mock_notifications):
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user("checker", [], True)
    release_request = factories.create_release_request(
        "workspace", user=author, status=RequestStatus.SUBMITTED
    )
    factories.write_request_file(
        release_request, "group", "test/file.txt", approved=files_approved
    )
    release_request = factories.refresh_release_request(release_request)

    if files_approved:
        bll.set_status(release_request, RequestStatus.APPROVED, user=checker)
        assert release_request.status == RequestStatus.APPROVED
        assert_last_notification(mock_notifications, "request_approved")
    else:
        with pytest.raises((bll.InvalidStateTransition, bll.RequestPermissionDenied)):
            bll.set_status(release_request, RequestStatus.APPROVED, user=checker)
        assert_last_notification(mock_notifications, "request_updated")


def test_set_status_cannot_action_own_request(bll):
    user = factories.create_user("checker", [], True)
    release_request1 = factories.create_release_request(
        "workspace", user=user, status=RequestStatus.SUBMITTED
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request1, RequestStatus.APPROVED, user=user)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request1, RequestStatus.REJECTED, user=user)

    release_request2 = factories.create_release_request(
        "workspace",
        user=user,
        status=RequestStatus.APPROVED,
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request2, RequestStatus.RELEASED, user=user)


def test_set_status_approved_no_files_denied(bll):
    user = factories.create_user("checker", [], True)
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request, RequestStatus.APPROVED, user=user)


def test_set_status_approved_only_supporting_file_denied(bll):
    user = factories.create_user("checker", [], True)
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED
    )
    factories.write_request_file(
        release_request, "group", "test/file.txt", filetype=RequestFileType.SUPPORTING
    )
    release_request = factories.refresh_release_request(release_request)

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request, RequestStatus.APPROVED, user=user)


def test_add_file_to_request_not_author(bll):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], True)

    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.add_file_to_request(release_request, path, other)


def test_add_file_to_request_invalid_file_type(bll):
    author = factories.create_user("author", ["workspace"], False)

    path = Path("path/file.foo")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.add_file_to_request(release_request, path, author)


@pytest.mark.parametrize(
    "status,success,notification_sent",
    [
        (RequestStatus.PENDING, True, False),
        (RequestStatus.SUBMITTED, True, True),
        (RequestStatus.APPROVED, False, False),
        (RequestStatus.REJECTED, False, False),
        (RequestStatus.RELEASED, False, False),
        (RequestStatus.WITHDRAWN, False, False),
    ],
)
def test_add_file_to_request_states(
    status, success, notification_sent, bll, mock_notifications
):
    author = factories.create_user("author", ["workspace"], False)

    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        status=status,
    )

    if success:
        bll.add_file_to_request(release_request, path, author)
        assert release_request.abspath("default" / path).exists()

        audit_log = bll.get_audit_log(request=release_request.id)
        assert audit_log[0] == AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_FILE_ADD,
            user=author,
            path=path,
            group="default",
            filetype="OUTPUT",
        )

        if notification_sent:
            last_notification = get_last_notification(mock_notifications)
            assert last_notification["updates"][0]["update_type"] == "file added"
        else:
            assert_no_notifications(mock_notifications)
    else:
        with pytest.raises(bll.RequestPermissionDenied):
            bll.add_file_to_request(release_request, path, author)


def test_add_file_to_request_default_filetype(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
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
    author = factories.create_user(username="author", workspaces=["workspace"])
    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )

    if success:
        bll.add_file_to_request(release_request, path, author, filetype=filetype)
        request_file = release_request.filegroups["default"].files[UrlPath(path)]
        assert request_file.filetype == filetype
    else:
        with pytest.raises(AttributeError):
            bll.add_file_to_request(release_request, path, author, filetype=filetype)


def test_withdraw_file_from_request_pending(bll, mock_notifications):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        status=RequestStatus.PENDING,
    )
    path1 = Path("path/file1.txt")
    path2 = Path("path/file2.txt")
    factories.write_request_file(
        release_request, "group", path1, contents="1", user=author
    )
    factories.write_request_file(
        release_request, "group", path2, contents="2", user=author
    )
    release_request = factories.refresh_release_request(release_request)

    assert release_request.filegroups["group"].files.keys() == {path1, path2}

    bll.withdraw_file_from_request(release_request, "group" / path1, user=author)

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path1,
        group="group",
    )

    assert release_request.filegroups["group"].files.keys() == {path2}

    bll.withdraw_file_from_request(release_request, "group" / path2, user=author)

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path2,
        group="group",
    )

    assert release_request.filegroups["group"].files.keys() == set()
    assert_no_notifications(mock_notifications)


def test_withdraw_file_from_request_submitted(bll, mock_notifications):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        status=RequestStatus.SUBMITTED,
    )
    path1 = Path("path/file1.txt")
    factories.write_request_file(release_request, "group", path1, user=author)
    release_request = factories.refresh_release_request(release_request)

    assert [f.filetype for f in release_request.filegroups["group"].files.values()] == [
        RequestFileType.OUTPUT,
    ]

    bll.withdraw_file_from_request(release_request, "group" / path1, user=author)

    assert [f.filetype for f in release_request.filegroups["group"].files.values()] == [
        RequestFileType.WITHDRAWN,
    ]

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path1,
        group="group",
    )
    last_notification = get_last_notification(mock_notifications)
    assert last_notification["updates"][0]["update_type"] == "file withdrawn"


@pytest.mark.parametrize(
    "state",
    [
        RequestStatus.APPROVED,
        RequestStatus.REJECTED,
        RequestStatus.WITHDRAWN,
        RequestStatus.RELEASED,
    ],
)
def test_withdraw_file_from_request_not_editable_state(bll, state):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        status=state,
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.withdraw_file_from_request(
            release_request, UrlPath("group/foo.txt"), author
        )


@pytest.mark.parametrize("state", [RequestStatus.PENDING, RequestStatus.SUBMITTED])
def test_withdraw_file_from_request_bad_file(bll, state):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace", status=state, user=author
    )

    with pytest.raises(bll.FileNotFound):
        bll.withdraw_file_from_request(
            release_request, UrlPath("bad/path"), user=author
        )


@pytest.mark.parametrize("state", [RequestStatus.PENDING, RequestStatus.SUBMITTED])
def test_withdraw_file_from_request_not_author(bll, state):
    author = factories.create_user(username="author", workspaces=["workspace"])
    release_request = factories.create_release_request(
        "workspace", status=state, user=author
    )

    other = factories.create_user(username="other", workspaces=["workspace"])

    with pytest.raises(bll.RequestPermissionDenied):
        bll.withdraw_file_from_request(release_request, UrlPath("bad/path"), user=other)


def test_request_all_files_set(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
    path = Path("path/file.txt")
    supporting_path = Path("path/supporting_file.txt")
    workspace = factories.create_workspace("workspace")
    for fp in [path, supporting_path]:
        factories.write_workspace_file(workspace, fp)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    bll.add_file_to_request(
        release_request, path, author, filetype=RequestFileType.OUTPUT
    )
    bll.add_file_to_request(
        release_request, supporting_path, author, filetype=RequestFileType.SUPPORTING
    )

    # all_files_set consists of output files and supporting files
    assert release_request.all_files_set() == {path, supporting_path}

    filegroup = release_request.filegroups["default"]
    assert len(filegroup.files) == 2
    assert len(filegroup.output_files) == 1
    assert len(filegroup.supporting_files) == 1


def test_request_release_get_request_file(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_release_request("id")
    factories.write_request_file(release_request, "default", path)
    factories.write_request_file(
        release_request, "default", supporting_path, filetype=RequestFileType.SUPPORTING
    )

    with pytest.raises(bll.FileNotFound):
        release_request.get_request_file("badgroup" / path)

    with pytest.raises(bll.FileNotFound):
        release_request.get_request_file("default/does/not/exist")

    request_file = release_request.get_request_file("default" / path)
    assert request_file.relpath == path


def test_request_release_abspath(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_release_request("id")
    factories.write_request_file(release_request, "default", path)
    factories.write_request_file(
        release_request, "default", supporting_path, filetype=RequestFileType.SUPPORTING
    )

    assert release_request.abspath("default" / path).exists()
    assert release_request.abspath("default" / supporting_path).exists()


def test_request_release_request_filetype(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_release_request("id")
    factories.write_request_file(release_request, "default", path)
    factories.write_request_file(
        release_request, "default", supporting_path, filetype=RequestFileType.SUPPORTING
    )

    assert release_request.request_filetype("default" / path) == RequestFileType.OUTPUT
    assert (
        release_request.request_filetype("default" / supporting_path)
        == RequestFileType.SUPPORTING
    )


def setup_empty_release_request():
    author = factories.create_user("author", ["workspace"], False)
    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    return release_request, path, author


def test_release_request_filegroups_with_no_files(bll):
    release_request, _, _ = setup_empty_release_request()
    assert release_request.filegroups == {}


def test_release_request_filegroups_default_filegroup(bll):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    bll.add_file_to_request(release_request, path, author)
    assert len(release_request.filegroups) == 1
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert len(filegroup.files) == 1
    assert path in filegroup.files


def test_release_request_filegroups_named_filegroup(bll):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    bll.add_file_to_request(release_request, path, author, "test_group")
    assert len(release_request.filegroups) == 1
    filegroup = release_request.filegroups["test_group"]
    assert filegroup.name == "test_group"
    assert len(filegroup.files) == 1
    assert path in filegroup.files


def test_release_request_filegroups_multiple_filegroups(bll):
    release_request, path, author = setup_empty_release_request()
    bll.add_file_to_request(release_request, path, author, "test_group")
    assert len(release_request.filegroups) == 1

    workspace = bll.get_workspace("workspace", author)
    path1 = Path("path/file1.txt")
    path2 = Path("path/file2.txt")
    factories.write_workspace_file(workspace, path1)
    factories.write_workspace_file(workspace, path2)
    bll.add_file_to_request(release_request, path1, author, "test_group")
    bll.add_file_to_request(release_request, path2, author, "test_group1")

    release_request = bll.get_release_request(release_request.id, author)
    assert len(release_request.filegroups) == 2

    release_request_files = {
        filegroup.name: list(filegroup.files)
        for filegroup in release_request.filegroups.values()
    }

    assert release_request_files == {
        "test_group": [Path("path/file.txt"), Path("path/file1.txt")],
        "test_group1": [Path("path/file2.txt")],
    }


def test_release_request_add_same_file(bll):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    bll.add_file_to_request(release_request, path, author)
    assert len(release_request.filegroups) == 1
    assert len(release_request.filegroups["default"].files) == 1

    # Adding the same file again should not create a new RequestFile
    with pytest.raises(bll.APIException):
        bll.add_file_to_request(release_request, path, author)

    # We also can't add the same file to a different group
    with pytest.raises(bll.APIException):
        bll.add_file_to_request(release_request, path, author, "new_group")

    release_request = bll.get_release_request(release_request.id, author)
    # No additional files or groups have been created
    assert len(release_request.filegroups) == 1
    assert len(release_request.filegroups["default"].files) == 1


def _get_current_file_reviews(bll, release_request, path, author):
    """Syntactic sugar to make the tests a little more readable"""
    return (
        bll.get_release_request(release_request.id, author)
        .filegroups["default"]
        .files[path]
        .reviews
    )


def test_approve_file_not_submitted(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, checker)

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0


def test_approve_file_not_your_own(bll):
    release_request, path, author = setup_empty_release_request()

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, author)

    assert len(_get_current_file_reviews(bll, release_request, path, author)) == 0


def test_approve_file_not_checker(bll):
    release_request, path, author = setup_empty_release_request()
    author2 = factories.create_user("author2", [], False)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, author2)

    assert len(_get_current_file_reviews(bll, release_request, path, author)) == 0


def test_approve_file_not_part_of_request(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    bad_path = Path("path/file2.txt")
    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, bad_path, checker)

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0


def test_approve_supporting_file(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(
        release_request, path, author, filetype=RequestFileType.SUPPORTING
    )
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )
    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, checker)

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0


def test_approve_file(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0

    bll.approve_file(release_request, path, checker)

    current_reviews = _get_current_file_reviews(bll, release_request, path, checker)
    assert len(current_reviews) == 1
    assert current_reviews[0].reviewer == "checker"
    assert current_reviews[0].status == FileReviewStatus.APPROVED
    assert type(current_reviews[0]) == FileReview

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_APPROVE,
        user=checker,
        path=path,
    )


def test_reject_file(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0

    bll.reject_file(release_request, path, checker)

    current_reviews = _get_current_file_reviews(bll, release_request, path, checker)
    assert len(current_reviews) == 1
    assert current_reviews[0].reviewer == "checker"
    assert current_reviews[0].status == FileReviewStatus.REJECTED
    assert type(current_reviews[0]) == FileReview
    assert len(current_reviews) == 1

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_REJECT,
        user=checker,
        path=path,
    )


def test_approve_then_reject_file(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0

    bll.approve_file(release_request, path, checker)

    current_reviews = _get_current_file_reviews(bll, release_request, path, checker)
    print(current_reviews)
    assert len(current_reviews) == 1
    assert current_reviews[0].reviewer == "checker"
    assert current_reviews[0].status == FileReviewStatus.APPROVED
    assert type(current_reviews[0]) == FileReview

    bll.reject_file(release_request, path, checker)

    current_reviews = _get_current_file_reviews(bll, release_request, path, checker)
    assert len(current_reviews) == 1
    assert current_reviews[0].reviewer == "checker"
    assert current_reviews[0].status == FileReviewStatus.REJECTED
    assert type(current_reviews[0]) == FileReview
    assert len(current_reviews) == 1


def test_get_file_review_for_reviewer(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)
    checker2 = factories.create_user("checker2", [], True)

    bll.add_file_to_request(release_request, path, author, "default")
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    assert len(_get_current_file_reviews(bll, release_request, path, checker)) == 0
    fullpath = "default" / path
    assert release_request.get_file_review_for_reviewer(fullpath, "checker") is None

    bll.approve_file(release_request, path, checker)
    bll.reject_file(release_request, path, checker2)
    release_request = factories.refresh_release_request(release_request, author)

    assert (
        release_request.get_file_review_for_reviewer(fullpath, "checker").status
        is FileReviewStatus.APPROVED
    )
    assert (
        release_request.get_file_review_for_reviewer(fullpath, "checker2").status
        is FileReviewStatus.REJECTED
    )

    bll.reject_file(release_request, path, checker)
    bll.approve_file(release_request, path, checker2)
    release_request = factories.refresh_release_request(release_request, author)

    assert (
        release_request.get_file_review_for_reviewer(fullpath, "checker").status
        is FileReviewStatus.REJECTED
    )
    assert (
        release_request.get_file_review_for_reviewer(fullpath, "checker2").status
        is FileReviewStatus.APPROVED
    )


# add DAL method names to this if they do not require auditing
DAL_AUDIT_EXCLUDED = {
    "get_release_request",
    "get_requests_for_workspace",
    "get_active_requests_for_workspace_by_user",
    "get_audit_log",
    "get_outstanding_requests_for_review",
    "get_requests_authored_by_user",
    "delete_file_from_request",
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
        assert (
            "AuditEvent" in arg_annotations
        ), f"DataAccessLayerProtocol method {name} does not have an AuditEvent parameter"


def test_group_edit_author(bll, mock_notifications):
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
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

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0].request == release_request.id
    assert audit_log[0].type == AuditEventType.REQUEST_EDIT
    assert audit_log[0].user == author.username
    assert audit_log[0].extra["group"] == "group"
    assert audit_log[0].extra["context"] == "foo"
    assert audit_log[0].extra["controls"] == "bar"

    # Request is in PENDING, so no notifications sent
    assert_no_notifications(mock_notifications)


@pytest.mark.parametrize(
    "new_context,new_controls,expected_updates",
    [
        (
            "",
            "bar",
            [{"update_type": "controls edited", "group": "group", "user": "author"}],
        ),
        (
            "foo",
            "",
            [{"update_type": "context edited", "group": "group", "user": "author"}],
        ),
        (
            "foo",
            "bar",
            [
                {"update_type": "context edited", "group": "group", "user": "author"},
                {"update_type": "controls edited", "group": "group", "user": "author"},
            ],
        ),
        (
            "",
            "",
            [],
        ),
    ],
)
def test_group_edit_notifications(
    bll, mock_notifications, new_context, new_controls, expected_updates
):
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)
    bll.set_status(release_request, RequestStatus.SUBMITTED, author)

    # group is always created with no context/controls initially
    assert release_request.filegroups["group"].context == ""
    assert release_request.filegroups["group"].controls == ""

    bll.group_edit(release_request, "group", new_context, new_controls, author)

    # notifications endpoint called when request submitted, and again for group edit
    notification_responses = parse_notification_responses(mock_notifications)
    if expected_updates:
        assert notification_responses["count"] == 2
        submitted_notification, edit_notification = notification_responses[
            "request_json"
        ]
        assert submitted_notification["event_type"] == "request_submitted"
        assert edit_notification == {
            "event_type": "request_updated",
            "workspace": "workspace",
            "request": release_request.id,
            "request_author": "author",
            "user": "author",
            "updates": expected_updates,
            "org": settings.AIRLOCK_OUTPUT_CHECKING_ORG,
            "repo": settings.AIRLOCK_OUTPUT_CHECKING_REPO,
        }
    else:
        assert notification_responses["count"] == 1


def test_group_edit_not_author(bll):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_edit(release_request, "group", "foo", "bar", other)


@pytest.mark.parametrize(
    "state", [RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.WITHDRAWN]
)
def test_group_edit_not_editable(bll, state):
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request(
        "workspace", user=author, status=state
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_edit(release_request, "group", "foo", "bar", author)


def test_group_edit_bad_group(bll):
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )

    with pytest.raises(bll.FileNotFound):
        bll.group_edit(release_request, "notexist", "foo", "bar", author)


@pytest.mark.parametrize(
    "status,notification_count",
    [
        (RequestStatus.PENDING, 0),
        # Currently no notifications are sent for comments. The only notification
        # sent in this test is for adding a file to the submitted request
        (RequestStatus.SUBMITTED, 1),
    ],
)
def test_group_comment_success(bll, mock_notifications, status, notification_count):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)
    release_request = factories.create_release_request(
        "workspace", user=author, status=status
    )
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)

    assert release_request.filegroups["group"].comments == []

    bll.group_comment(release_request, "group", "question?", other)
    bll.group_comment(release_request, "group", "answer!", author)

    notification_responses = parse_notification_responses(mock_notifications)
    assert notification_responses["count"] == notification_count
    if notification_count > 0:
        file_added = notification_responses["request_json"][0]
        assert file_added["event_type"] == "request_updated"
        assert file_added["updates"][0]["update_type"] == "file added"

    release_request = factories.refresh_release_request(release_request)

    assert release_request.filegroups["group"].comments[0].comment == "question?"
    assert release_request.filegroups["group"].comments[0].author == "other"
    assert release_request.filegroups["group"].comments[1].comment == "answer!"
    assert release_request.filegroups["group"].comments[1].author == "author"

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[1].request == release_request.id
    assert audit_log[1].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[1].user == other.username
    assert audit_log[1].extra["group"] == "group"
    assert audit_log[1].extra["comment"] == "question?"

    assert audit_log[0].request == release_request.id
    assert audit_log[0].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[0].user == author.username
    assert audit_log[0].extra["group"] == "group"
    assert audit_log[0].extra["comment"] == "answer!"


def test_group_comment_permissions(bll):
    author = factories.create_user("author", ["workspace"], False)
    collaborator = factories.create_user("collaboratorr", ["workspace"], False)
    other = factories.create_user("other", ["other"], False)
    checker = factories.create_user("checker", ["other"], True)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 0

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_comment(release_request, "group", "question?", other)

    bll.group_comment(release_request, "group", "collaborator", collaborator)
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1

    bll.group_comment(release_request, "group", "checker", checker)
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 2


def test_coderepo_from_workspace_bad_json(bll):
    workspace = factories.create_workspace("workspace")
    workspace.manifest = {}

    with pytest.raises(CodeRepo.RepoNotFound):
        CodeRepo.from_workspace(workspace, "commit")

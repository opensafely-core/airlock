import hashlib
import inspect
import json
from hashlib import file_digest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings
from django.utils.dateparse import parse_datetime

import old_api
from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
    CodeRepo,
    CommentVisibility,
    DataAccessLayerProtocol,
    NotificationEventType,
    RequestFileDecision,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    Workspace,
)
from airlock.types import UrlPath, WorkspaceFileStatus
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
    assert workspace.released_files == set()
    assert (
        workspace.get_url("foo/bar.html") == "/workspaces/view/workspace/foo/bar.html"
    )
    assert (
        "/workspaces/content/workspace/foo/bar.html?cache_id="
        in workspace.get_contents_url(UrlPath("foo/bar.html"))
    )
    plaintext_contents_url = workspace.get_contents_url(
        UrlPath("foo/bar.html"), plaintext=True
    )
    assert (
        "/workspaces/content/workspace/foo/bar.html?cache_id=" in plaintext_contents_url
    )
    assert "&plaintext=true" in plaintext_contents_url

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


def test_get_file_metadata():
    workspace = factories.create_workspace("workspace")

    # non existant file
    assert workspace.get_file_metadata(UrlPath("metadata/foo.log")) is None

    # directory
    (workspace.root() / "directory").mkdir()
    with pytest.raises(AssertionError):
        workspace.get_file_metadata(UrlPath("directory")) is None

    # small log file
    factories.write_workspace_file(
        workspace, "metadata/foo.log", contents="foo", manifest=False
    )

    from_file = workspace.get_file_metadata(UrlPath("metadata/foo.log"))
    assert from_file.size == 3
    assert from_file.timestamp is not None
    assert from_file.content_hash == hashlib.sha256(b"foo").hexdigest()

    # larger output file
    contents = "x," * 1024 * 1024
    factories.write_workspace_file(
        workspace, "output/bar.csv", contents=contents, manifest=True
    )

    from_metadata = workspace.get_file_metadata(UrlPath("output/bar.csv"))
    assert from_metadata.size == len(contents)
    assert from_metadata.timestamp is not None
    assert (
        from_metadata.content_hash
        == hashlib.sha256(contents.encode("utf8")).hexdigest()
    )


def test_workspace_get_workspace_archived_ongoing(bll):
    factories.create_workspace("workspace")
    factories.create_workspace("archived_workspace")
    factories.create_workspace("not_ongoing")
    user = factories.create_user(
        "user",
        workspaces={
            "workspace": {
                "project": "project-1",
                "project_details": {"name": "project-1", "ongoing": True},
                "archived": False,
            },
            "archived_workspace": {
                "project": "project-1",
                "project_details": {"name": "project-1", "ongoing": True},
                "archived": True,
            },
            "not_ongoing": {
                "project": "project-2",
                "project_details": {"name": "project-2", "ongoing": False},
                "archived": False,
            },
        },
    )
    checker = factories.create_user("checker", output_checker=True)

    active_workspace = bll.get_workspace("workspace", user)
    archived_workspace = bll.get_workspace("archived_workspace", user)
    inactive_project = bll.get_workspace("not_ongoing", user)
    assert not active_workspace.is_archived()
    assert active_workspace.project().is_ongoing
    assert active_workspace.is_active()
    assert active_workspace.display_name() == "workspace"
    assert active_workspace.project().display_name() == "project-1"

    assert archived_workspace.is_archived()
    assert archived_workspace.project().is_ongoing
    assert not archived_workspace.is_active()
    assert archived_workspace.display_name() == "archived_workspace (ARCHIVED)"
    assert archived_workspace.project().display_name() == "project-1"

    assert not inactive_project.is_archived()
    assert not inactive_project.project().is_ongoing
    assert not inactive_project.is_active()
    assert inactive_project.display_name() == "not_ongoing"
    assert inactive_project.project().display_name() == "project-2 (INACTIVE)"

    for workspace_name in ["workspace", "archived_workspace", "not_ongoing"]:
        workspace = bll.get_workspace(workspace_name, checker)
        assert workspace.is_archived() is None
        assert bll.get_workspace(workspace_name, checker).project().is_ongoing
        assert workspace.display_name() == workspace_name
        assert "INACTIVE" not in workspace.project().display_name()


def test_workspace_get_workspace_file_status(bll):
    path = UrlPath("foo/bar.txt")
    workspace = factories.create_workspace("workspace")
    user = factories.create_user(workspaces=["workspace"])

    assert workspace.get_workspace_file_status(path) is None

    factories.write_workspace_file(workspace, path, contents="foo")
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED

    release_request = factories.create_release_request(workspace, user=user)
    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED

    factories.add_request_file(release_request, "group", path)
    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW

    factories.write_workspace_file(workspace, path, contents="changed")
    assert (
        workspace.get_workspace_file_status(path) == WorkspaceFileStatus.CONTENT_UPDATED
    )


def test_workspace_get_released_files(bll):
    path = UrlPath("foo/bar.txt")
    path1 = UrlPath("foo/supporting_bar.txt")
    factories.create_request_at_status(
        "workspace",
        RequestStatus.RELEASED,
        files=[
            factories.request_file(
                path=path,
                contents="foo",
                approved=True,
                filetype=RequestFileType.OUTPUT,
            ),
            factories.request_file(
                path=path1,
                contents="bar",
                approved=True,
                filetype=RequestFileType.SUPPORTING,
            ),
        ],
    )
    user = factories.create_user("test", workspaces=["workspace"])
    workspace = bll.get_workspace("workspace", user)
    # supporting file is not considered a released file
    assert len(workspace.released_files) == 1
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.RELEASED
    assert workspace.get_workspace_file_status(path1) == WorkspaceFileStatus.UNRELEASED


def test_request_returned_get_workspace_file_status(bll):
    user = factories.create_user(workspaces=["workspace"])
    path = "file1.txt"
    workspace_file = factories.request_file(
        group="group",
        contents="1",
        approved=True,
        path=path,
    )
    factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED


def test_request_pending_not_author_get_workspace_file_status(bll):
    user = factories.create_user(workspaces=["workspace"])
    path = "file1.txt"
    workspace_file = factories.request_file(
        group="group",
        contents="1",
        path=path,
    )
    factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNRELEASED


def test_request_pending_author_get_workspace_file_status(bll):
    status = RequestStatus.PENDING

    author = factories.create_user("author", ["workspace"], False)
    workspace = factories.create_workspace("workspace")
    path = UrlPath("path/file.txt")

    workspace_file = factories.request_file(
        group="group",
        path=path,
        contents="1",
        user=author,
        rejected=True,
    )

    factories.create_request_at_status(
        workspace.name,
        author=author,
        status=status,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW


def test_request_returned_author_get_workspace_file_status(bll):
    status = RequestStatus.RETURNED

    author = factories.create_user("author", ["workspace"], False)
    workspace = factories.create_workspace("workspace")
    path = UrlPath("path/file.txt")

    workspace_file = factories.request_file(
        group="group",
        path=path,
        contents="1",
        user=author,
        rejected=True,
    )

    factories.create_request_at_status(
        workspace.name,
        author=author,
        status=status,
        files=[
            workspace_file,
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW


def test_request_container(mock_notifications):
    release_request = factories.create_release_request("workspace", id="id")
    factories.add_request_file(release_request, "group", "bar.html")

    assert release_request.root() == settings.REQUEST_DIR / "workspace/id"
    assert release_request.get_id() == "id"
    assert (
        release_request.get_url("group/bar.html") == "/requests/view/id/group/bar.html"
    )
    assert (
        "/requests/content/id/group/bar.html?cache_id="
        in release_request.get_contents_url(UrlPath("group/bar.html"))
    )
    plaintext_contents_url = release_request.get_contents_url(
        UrlPath("group/bar.html"), plaintext=True
    )
    assert "/requests/content/id/group/bar.html?cache_id=" in plaintext_contents_url
    assert "&plaintext=true" in plaintext_contents_url

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
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo.txt")
    repo = factories.create_repo(workspace)

    assert repo.get_id() == f"workspace@{repo.commit[:7]}"
    assert (
        repo.get_url(UrlPath("project.yaml"))
        == f"/code/view/workspace/{repo.commit}/project.yaml"
    )
    assert (
        f"/code/contents/workspace/{repo.commit}/project.yaml?cache_id="
        in repo.get_contents_url(UrlPath("project.yaml"))
    )

    plaintext_contents_url = repo.get_contents_url(
        UrlPath("project.yaml"), plaintext=True
    )
    assert (
        f"/code/contents/workspace/{repo.commit}/project.yaml?cache_id="
        in plaintext_contents_url
    )
    assert "&plaintext=true" in plaintext_contents_url

    assert repo.request_filetype("path") == RequestFileType.CODE


@pytest.mark.parametrize("output_checker", [False, True])
def test_provider_get_workspaces_for_user(bll, output_checker):
    factories.create_workspace("foo")
    factories.create_workspace("bar")
    factories.create_workspace("not-allowed")
    workspaces = {
        "foo": {
            "project_details": {"name": "project 1", "ongoing": True},
            "archived": False,
        },
        "bar": {
            "project_details": {"name": "project 2", "ongoing": True},
            "archived": True,
        },
        "not-exists": {
            "project_details": {"name": "project 2", "ongoing": True},
            "archived": False,
        },
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
    checker = factories.create_user("checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )

    with pytest.raises(bll.InvalidStateTransition):
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
            id="request_id",
            status=RequestStatus.APPROVED,
            files=[factories.request_file(path="test/file.foo", approved=True)],
        )

    checker = factories.create_user("checker", [], output_checker=True)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.release_files(release_request, checker)
    assert_last_notification(mock_notifications, "request_approved")


def test_provider_request_release_files(mock_old_api, mock_notifications, bll, freezer):
    old_api.create_release.return_value = "jobserver_id"
    checker = factories.create_user("checker", [], output_checker=True)
    checker1 = factories.create_user("checker1", [], output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        id="request_id",
        status=RequestStatus.APPROVED,
        checker=checker,
        files=[
            factories.request_file(
                group="group",
                path="test/file.txt",
                contents="test",
                approved=True,
                checkers=[checker, checker1],
            ),
            # a supporting file, which should NOT be released
            factories.request_file(
                group="group",
                path="test/supporting_file.txt",
                filetype=RequestFileType.SUPPORTING,
            ),
            # An approved but withdrawn file, which should NOT be released
            factories.request_file(
                group="group",
                path="test/withdrawn_file.txt",
                filetype=RequestFileType.WITHDRAWN,
                approved=True,
            ),
        ],
    )
    relpath = Path("test/file.txt")
    abspath = release_request.abspath("group" / relpath)

    freezer.move_to("2022-01-01T12:34:56")
    bll.release_files(release_request, checker)

    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.filegroups["group"].files[relpath]
    assert request_file.released_by == checker.username
    assert request_file.released_at == parse_datetime("2022-01-01T12:34:56Z")

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
        "workspace", "request_id", json.dumps(expected_json), checker.username
    )
    old_api.upload_file.assert_called_once_with(
        "jobserver_id", relpath, abspath, checker.username
    )

    notification_responses = parse_notification_responses(mock_notifications)
    # Notifications expected for:
    # - set status to submitted
    # - set status to partially reviewed
    # - set status to reviewed
    # - set status to approved
    # - set status to released
    # (Note: files are added to the request when it is in pending status, so no notifications sent.)
    assert notification_responses["count"] == 5
    request_json = notification_responses["request_json"]
    expected_notifications = [
        "request_submitted",
        "request_partially_reviewed",
        "request_reviewed",
        "request_approved",
        "request_released",
    ]
    assert [event["event_type"] for event in request_json] == expected_notifications

    audit_log = bll.get_audit_log(request=release_request.id)
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
        # checker reviews
        AuditEventType.REQUEST_REVIEW,
        # checker1 reviews
        AuditEventType.REQUEST_REVIEW,
        # appprove, release 1 output file, change request to released
        AuditEventType.REQUEST_APPROVE,
        AuditEventType.REQUEST_FILE_RELEASE,
        AuditEventType.REQUEST_RELEASE,
    ]
    assert [log.type for log in audit_log] == expected_audit_logs

    checker_review_log = audit_log[6]
    checker1_review_log = audit_log[7]
    approve_log = audit_log[8]
    release_file_log = audit_log[9]
    release_log = audit_log[10]

    assert checker_review_log.type == AuditEventType.REQUEST_REVIEW
    assert checker_review_log.user == checker.username
    assert checker_review_log.request == release_request.id
    assert checker_review_log.workspace == "workspace"

    assert checker1_review_log.type == AuditEventType.REQUEST_REVIEW
    assert checker1_review_log.user == checker1.username
    assert checker1_review_log.request == release_request.id
    assert checker1_review_log.workspace == "workspace"

    assert approve_log.type == AuditEventType.REQUEST_APPROVE
    assert approve_log.user == checker.username
    assert approve_log.request == release_request.id
    assert approve_log.workspace == "workspace"

    assert release_file_log.type == AuditEventType.REQUEST_FILE_RELEASE
    assert release_file_log.user == checker.username
    assert release_file_log.request == release_request.id
    assert release_file_log.workspace == "workspace"
    assert release_file_log.path == Path("test/file.txt")

    assert release_log.type == AuditEventType.REQUEST_RELEASE
    assert release_log.user == checker.username
    assert release_log.request == release_request.id
    assert release_log.workspace == "workspace"


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
    factories.create_request_at_status(
        "workspace", author=other_user, id="r1", status=RequestStatus.SUBMITTED
    )

    # requests not visible to output checker
    # status submitted, but authored by output checker
    factories.create_request_at_status(
        "workspace", author=user, id="r2", status=RequestStatus.SUBMITTED
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
        factories.create_request_at_status(
            ws,
            author=user_n,
            status=status,
            files=[factories.request_file(approved=status != RequestStatus.PENDING)],
            withdrawn_after=RequestStatus.PENDING,
        )

    assert set(r.id for r in bll.get_outstanding_requests_for_review(user)) == set(
        expected
    )


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
def test_provider_get_returned_requests(output_checker, expected, bll):
    user = factories.create_user("test", ["workspace"], output_checker)
    other_user = factories.create_user("other", ["workspace"], False)
    output_checker = factories.create_user("other-checker", ["workspace"], True)
    # request created by another user, status returned
    factories.create_request_at_status(
        "workspace",
        author=other_user,
        id="r1",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path="file.txt", approved=True)],
    )

    # requests not visible to output checker
    # status returned, but authored by output checker
    factories.create_request_at_status(
        "workspace",
        author=user,
        id="r2",
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
        user_n = factories.create_user(f"test_{i}", [ws])
        factories.create_request_at_status(
            ws,
            author=user_n,
            status=status,
            withdrawn_after=RequestStatus.PENDING,
            files=[factories.request_file(approved=True)],
        )

    assert set(r.id for r in bll.get_returned_requests(user)) == set(expected)


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
def test_provider_get_approved_requests(output_checker, expected, bll):
    user = factories.create_user("test", ["workspace"], output_checker)
    other_user = factories.create_user("other", ["workspace"], False)
    output_checker = factories.create_user("other-checker", ["workspace"], True)

    # request created by another user, status approved
    factories.create_request_at_status(
        "workspace",
        author=other_user,
        id="r1",
        status=RequestStatus.APPROVED,
        files=[factories.request_file(path="file.txt", approved=True)],
    )

    # requests not visible to output checker
    # status approved, but authored by output checker
    factories.create_request_at_status(
        "workspace",
        author=user,
        id="r2",
        status=RequestStatus.APPROVED,
        files=[factories.request_file(path="file.txt", approved=True)],
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
        user_n = factories.create_user(f"test_{i}", [ws])
        factories.create_request_at_status(
            ws,
            author=user_n,
            status=status,
            withdrawn_after=RequestStatus.PENDING,
            files=[factories.request_file(approved=True)],
        )
    assert set(r.id for r in bll.get_approved_requests(user)) == set(expected)


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
def test_provider_get_current_request_for_user(bll, status, is_current):
    user = factories.create_user(workspaces=["workspace"])
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


@pytest.mark.parametrize(
    "workspaces",
    [
        # no access
        {},
        # workspace archived
        {
            "workspace": {
                "project_details": {"name": "p1", "ongoing": True},
                "archived": True,
            }
        },
        # project inactive
        {
            "workspace": {
                "project_details": {"name": "p1", "ongoing": False},
                "archived": False,
            }
        },
    ],
)
def test_provider_get_or_create_current_request_for_user_no_permissions(
    bll, workspaces
):
    workspace = factories.create_workspace("workspace")
    # create the request with a user who has permission
    factories.create_release_request(
        workspace, factories.create_user("testuser", ["workspace"])
    )

    # Duplicate user who has the test permissions/workspace status
    user = factories.create_user("testuser", workspaces, False)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.get_or_create_current_request("workspace", user)


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
        (RequestStatus.SUBMITTED, RequestStatus.RETURNED, False, False, None),
        (RequestStatus.SUBMITTED, RequestStatus.RELEASED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.PENDING, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.SUBMITTED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.REVIEWED, False, True, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.APPROVED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.REJECTED, False, False, None),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.RELEASED, False, False, None),
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
def test_set_status(current, future, valid_author, valid_checker, withdrawn_after, bll):
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user(output_checker=True)
    file_reviewers = [checker, factories.create_user("checker1", [], True)]
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
        audit_log = bll.get_audit_log(request=release_request1.id)
        assert audit_log[0].type == audit_type
        assert audit_log[0].user == author.username
        assert audit_log[0].request == release_request1.id
        assert audit_log[0].workspace == "workspace1"
    else:
        with pytest.raises((bll.InvalidStateTransition, bll.RequestPermissionDenied)):
            bll.set_status(release_request1, future, user=author)

    if valid_checker:
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


def test_request_status_ownership(bll):
    """Test every RequestStatus has been assigned an ownership"""
    missing_states = set(RequestStatus) - BusinessLogicLayer.STATUS_OWNERS.keys()
    assert missing_states == set()


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
    current, future, user, notification_event_type, bll, mock_notifications
):
    users = {
        "author": factories.create_user("author", ["workspace"], False),
        "checker": factories.create_user(output_checker=True),
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
    author = factories.create_user()
    release_request = factories.create_release_request("test", author)
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
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.add_request_file(release_request, "group", "test/file.txt", approved=True)
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
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(
                approved=all_files_approved, rejected=not all_files_approved
            )
        ],
    )

    if all_files_approved:
        bll.set_status(release_request, RequestStatus.APPROVED, user=checker)
        assert release_request.status == RequestStatus.APPROVED
        assert_last_notification(mock_notifications, "request_approved")
    else:
        with pytest.raises(bll.RequestPermissionDenied):
            bll.set_status(release_request, RequestStatus.APPROVED, user=checker)
        assert_last_notification(mock_notifications, "request_reviewed")


def test_set_status_cannot_action_own_request(bll):
    user = factories.create_user(output_checker=True)
    release_request1 = factories.create_request_at_status(
        "workspace", author=user, status=RequestStatus.SUBMITTED
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request1, RequestStatus.PARTIALLY_REVIEWED, user=user)

    release_request2 = factories.create_request_at_status(
        "workspace1",
        author=user,
        status=RequestStatus.APPROVED,
        files=[factories.request_file(approved=True)],
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request2, RequestStatus.RELEASED, user=user)


def test_set_status_approved_no_files_denied(bll):
    user = factories.create_user(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace", status=RequestStatus.REVIEWED
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request, RequestStatus.APPROVED, user=user)


def test_set_status_approved_only_supporting_file_denied(bll):
    user = factories.create_user(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.REVIEWED,
        files=[factories.request_file(filetype=RequestFileType.SUPPORTING)],
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.set_status(release_request, RequestStatus.APPROVED, user=user)


def test_submit_request(bll, mock_notifications):
    """
    From pending
    """
    author = factories.create_user("author", ["workspace"], False)
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
    Files with rejected status are moved to undecided
    """
    author = factories.create_user("author", ["workspace"], False)
    # Returned request with two files, one is approved by both reviewers, one is rejected
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(group="test", path="file.txt", approved=True),
            factories.request_file(group="test", path="file1.txt", rejected=True),
        ],
    )
    assert release_request.completed_reviews_count() == 2

    # author re-submits with no changes to files
    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.SUBMITTED
    assert_last_notification(mock_notifications, "request_resubmitted")
    for i in range(2):
        user = factories.create_user(f"output-checker-{i}", output_checker=True)
        # approved file review is still approved
        approved_file = release_request.get_request_file_from_output_path(
            UrlPath("file.txt")
        )
        assert approved_file.get_file_vote_for_user(user) == RequestFileVote.APPROVED

        # rejected file review is now undecided
        rejected_file = release_request.get_request_file_from_output_path(
            UrlPath("file1.txt")
        )
        assert rejected_file.get_file_vote_for_user(user) == RequestFileVote.UNDECIDED
        assert not release_request.all_files_reviewed_by_reviewer(user)
        # completed reviews have been reset
        assert release_request.completed_reviews == {}


def test_add_file_to_request_not_author(bll):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], True)

    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.add_file_to_request(release_request, path, other)


@pytest.mark.parametrize(
    "workspaces",
    [
        # no access; possible if a user has been removed from a
        # project/workspace on job-server, or has had their roles
        # updated since a release was created
        {},
        # workspace archived
        {
            "workspace": {
                "project_details": {"name": "p1", "ongoing": True},
                "archived": True,
            }
        },
        # project inactive
        {
            "workspace": {
                "project_details": {"name": "p1", "ongoing": False},
                "archived": False,
            }
        },
    ],
)
def test_add_file_to_request_no_permission(bll, workspaces):
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=factories.create_user("author", ["workspace"], False),
    )

    # create duplicate user with test workspaces
    author = factories.create_user("author", workspaces, False)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.add_file_to_request(release_request, path, author)


def test_add_file_to_request_invalid_file_type(bll):
    author = factories.create_user("author", ["workspace"], False)

    path = UrlPath("path/file.foo")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        workspace,
        user=author,
    )

    with pytest.raises(bll.RequestPermissionDenied):
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
def test_add_file_to_request_states(status, success, bll):
    author = factories.create_user("author", ["workspace"], False)

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

        audit_log = bll.get_audit_log(request=release_request.id)
        assert audit_log[0] == AuditEvent.from_request(
            release_request,
            AuditEventType.REQUEST_FILE_ADD,
            user=author,
            path=path,
            group="default",
            filetype="OUTPUT",
        )
    else:
        with pytest.raises(bll.RequestPermissionDenied):
            bll.add_file_to_request(release_request, path, author)


def test_add_file_to_request_default_filetype(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
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
    author = factories.create_user(username="author", workspaces=["workspace"])
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


def test_add_file_to_request_already_released(bll):
    user = factories.create_user(workspaces=["workspace"])
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
    with pytest.raises(bll.RequestPermissionDenied, match=r"Cannot add released file"):
        bll.add_file_to_request(
            release_request, "file.txt", user, filetype=RequestFileType.OUTPUT
        )


def test_update_file_in_request_invalid_file_type(bll):
    author = factories.create_user("author", ["workspace"], False)

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
        bll.RequestPermissionDenied, match=r"Cannot update file of type"
    ):
        bll.update_file_in_request(release_request, relpath, author)


def test_update_file_in_request_not_updated(bll):
    author = factories.create_user("author", ["workspace"], False)

    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
    )

    with pytest.raises(bll.RequestPermissionDenied, match=r"not updated"):
        bll.update_file_in_request(release_request, relpath, author)


@pytest.mark.parametrize(
    "status,approved,rejected,success,notification_sent",
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
    status, approved, rejected, success, notification_sent, bll, mock_notifications
):
    author = factories.create_user("author", ["workspace"], False)
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
        rejected=rejected,
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
        bll.update_file_in_request(release_request, path, author, "group")
    else:
        with pytest.raises(bll.RequestPermissionDenied):
            bll.update_file_in_request(release_request, path, author)
        return

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)
    assert workspace.get_workspace_file_status(path) == WorkspaceFileStatus.UNDER_REVIEW

    status1 = release_request.get_request_file_status("group" / path, checkers[0])
    assert status1.vote is None
    assert status1.decision == RequestFileDecision.INCOMPLETE
    status2 = release_request.get_request_file_status("group" / path, checkers[1])
    assert status2.vote is None
    assert status2.decision == RequestFileDecision.INCOMPLETE

    assert release_request.abspath("group" / path).exists()

    audit_log = bll.get_audit_log(request=release_request.id)
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


def test_withdraw_file_from_request_pending(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
    path1 = Path("path/file1.txt")
    path2 = Path("path/file2.txt")
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


def test_withdraw_file_from_request_returned(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
    path1 = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                group="group", path=path1, contents="1", user=author, rejected=True
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

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_WITHDRAW,
        user=author,
        path=path1,
        group="group",
    )


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
def test_withdraw_file_from_request_not_editable_state(bll, status):
    author = factories.create_user(username="author", workspaces=["workspace"])
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

    with pytest.raises(bll.RequestPermissionDenied):
        bll.withdraw_file_from_request(
            release_request, UrlPath("group/foo.txt"), author
        )


@pytest.mark.parametrize("status", [RequestStatus.PENDING, RequestStatus.RETURNED])
def test_withdraw_file_from_request_bad_file(bll, status):
    author = factories.create_user(username="author", workspaces=["workspace"])
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

    with pytest.raises(bll.FileNotFound):
        bll.withdraw_file_from_request(
            release_request, UrlPath("bad/path"), user=author
        )


@pytest.mark.parametrize("status", [RequestStatus.PENDING, RequestStatus.SUBMITTED])
def test_withdraw_file_from_request_not_author(bll, status):
    author = factories.create_user(username="author", workspaces=["workspace"])
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

    other = factories.create_user(username="other", workspaces=["workspace"])

    with pytest.raises(bll.RequestPermissionDenied):
        bll.withdraw_file_from_request(
            release_request, UrlPath("group/foo.txt"), user=other
        )


def test_request_all_files_by_name(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
    path = Path("path/file.txt")
    supporting_path = Path("path/supporting_file.txt")

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    # all_files_by_name consists of output files and supporting files
    assert release_request.all_files_by_name.keys() == {path, supporting_path}

    filegroup = release_request.filegroups["default"]
    assert len(filegroup.files) == 2
    assert len(filegroup.output_files) == 1
    assert len(filegroup.supporting_files) == 1


def test_request_release_get_request_file_from_urlpath(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")

    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        id="id",
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    with pytest.raises(bll.FileNotFound):
        release_request.get_request_file_from_urlpath("badgroup" / path)

    with pytest.raises(bll.FileNotFound):
        release_request.get_request_file_from_urlpath("default/does/not/exist")

    request_file = release_request.get_request_file_from_urlpath("default" / path)
    assert request_file.relpath == path


def test_request_release_abspath(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        id="id",
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    assert release_request.abspath("default" / path).exists()
    assert release_request.abspath("default" / supporting_path).exists()


def test_request_release_request_filetype(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.PENDING,
        id="id",
        files=[
            factories.request_file(
                group="default",
                path=supporting_path,
                filetype=RequestFileType.SUPPORTING,
            ),
            factories.request_file(group="default", path=path),
        ],
    )

    assert release_request.request_filetype("default" / path) == RequestFileType.OUTPUT
    assert (
        release_request.request_filetype("default" / supporting_path)
        == RequestFileType.SUPPORTING
    )


def setup_empty_release_request():
    author = factories.create_user("author", ["workspace"], False)
    path = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    return release_request, path, author


def test_get_visible_comments_for_group(bll):
    # This test is long and complex.
    #
    # It walks through a couple of rounds of back and forth review, validating
    # that the comments that are visibile to various users at different points
    # in the process are correct.
    #
    author = factories.create_user("author1", workspaces=["workspace"])
    checkers = factories.get_default_output_checkers()

    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        author=author,
        files=[factories.request_file(group="group", approved=True)],
    )

    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 0 private",
        CommentVisibility.PRIVATE,
        checkers[0],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 1 private",
        CommentVisibility.PRIVATE,
        checkers[1],
    )

    # helper function to fetch the current comments for a user
    def get_comments(user):
        nonlocal release_request
        release_request = factories.refresh_release_request(release_request)
        return [
            c[0].comment
            for c in release_request.get_visible_comments_for_group("group", user)
        ]

    # in RequestPhase.INDEPENDENT, can only see own comments
    assert get_comments(checkers[0]) == ["turn 1 checker 0 private"]
    assert get_comments(checkers[1]) == ["turn 1 checker 1 private"]
    assert get_comments(author) == []
    assert release_request.review_turn == 1

    factories.complete_independent_review(release_request)

    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 0 public",
        CommentVisibility.PUBLIC,
        checkers[0],
    )

    # in RequestPhase.CONSOLIDATING, checkers should see all private comments
    # and pending public comments, but author should not see any yet
    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
    ]
    assert get_comments(author) == []
    assert release_request.review_turn == 1

    bll.return_request(release_request, checkers[0])

    # in RequestPhase.COMPLETE, checkers should see all private comments
    # and pending public comments, but author should not see any yet
    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
    ]
    assert get_comments(author) == ["turn 1 checker 0 public"]
    assert release_request.review_turn == 2

    bll.group_comment_create(
        release_request,
        "group",
        "turn 2 author public",
        CommentVisibility.PUBLIC,
        author,
    )

    # in RequestPhase.AUTHOR, checkers should see all public/private comments
    # they've made, but not any the author has made, as it hasn't yet been
    # returned.
    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
    ]
    assert get_comments(author) == ["turn 1 checker 0 public", "turn 2 author public"]

    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)

    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 0 private",
        CommentVisibility.PRIVATE,
        checkers[0],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 1 private",
        CommentVisibility.PRIVATE,
        checkers[1],
    )

    # in RequestPhase.INDEPENDENT for a 2nd round
    # Checkers should see previous round's private comments, but not this rounds
    # Author should see previous round's public comments, but not any this round
    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 1 private",
    ]
    assert get_comments(author) == ["turn 1 checker 0 public", "turn 2 author public"]
    assert release_request.review_turn == 3

    factories.complete_independent_review(release_request)

    # in RequestPhase.CONSOLIDATING for a 2nd round
    # Checkers should see previous and current round's private comments,
    # Author should see previous round's public comments, but not any private comments
    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
        "turn 3 checker 1 private",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
        "turn 3 checker 1 private",
    ]
    assert get_comments(author) == ["turn 1 checker 0 public", "turn 2 author public"]

    # assume returned

    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 0 public",
        CommentVisibility.PUBLIC,
        checkers[0],
    )

    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
        "turn 3 checker 1 private",
        "turn 3 checker 0 public",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
        "turn 3 checker 1 private",
        "turn 3 checker 0 public",
    ]
    assert get_comments(author) == ["turn 1 checker 0 public", "turn 2 author public"]

    bll.return_request(release_request, checkers[0])

    # in RequestPhase.COMPLETE for a 2nd round
    # Checkers should see all historic comments,
    # Author should see previous round's public comments, but not any private comments
    assert get_comments(checkers[0]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
        "turn 3 checker 1 private",
        "turn 3 checker 0 public",
    ]
    assert get_comments(checkers[1]) == [
        "turn 1 checker 0 private",
        "turn 1 checker 1 private",
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 private",
        "turn 3 checker 1 private",
        "turn 3 checker 0 public",
    ]
    assert get_comments(author) == [
        "turn 1 checker 0 public",
        "turn 2 author public",
        "turn 3 checker 0 public",
    ]

    assert release_request.review_turn == 4


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
    path1 = UrlPath("path/file1.txt")
    path2 = UrlPath("path/file2.txt")
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
        "test_group": [UrlPath("path/file.txt"), UrlPath("path/file1.txt")],
        "test_group1": [UrlPath("path/file2.txt")],
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


def _get_request_file(release_request, path):
    """Syntactic sugar to make the tests a little more readable"""
    # refresh
    release_request = factories.refresh_release_request(release_request)
    return release_request.get_request_file_from_output_path(path)


def test_approve_file_not_submitted(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user(output_checker=True)

    bll.add_file_to_request(release_request, path, author)
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


def test_approve_file_not_your_own(bll):
    release_request, path, author = setup_empty_release_request()

    bll.add_file_to_request(release_request, path, author)
    bll.group_edit(release_request, "default", "foo", "bar", author)
    release_request = bll.get_release_request(release_request.id, author)

    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, request_file, author)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(author) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


def test_approve_file_not_checker(bll):
    release_request, path, author = setup_empty_release_request()
    author2 = factories.create_user("author2", [], False)

    bll.add_file_to_request(release_request, path, author)
    bll.group_edit(release_request, "default", "foo", "bar", author)
    release_request = bll.get_release_request(release_request.id, author)

    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, request_file, author2)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(author) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


def test_approve_file_not_part_of_request(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)
    bad_path = Path("path/file2.txt")
    bad_request_file = factories.create_request_file_bad_path(request_file, bad_path)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, bad_request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


def test_approve_supporting_file(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path, filetype=RequestFileType.SUPPORTING)],
    )
    checker = factories.create_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


def test_approve_file(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    assert "group" in release_request.filegroups

    checker = factories.create_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.APPROVED
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_APPROVE,
        user=checker,
        path=request_file.relpath,
        group="group",
    )


def test_approve_file_requires_two_plus(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker1 = factories.create_user("checker1", [], True)
    checker2 = factories.create_user("checker2", [], True)
    checker3 = factories.create_user("checker3", [], True)

    request_file = release_request.get_request_file_from_output_path(path)

    bll.approve_file(release_request, request_file, checker1)
    bll.reject_file(release_request, request_file, checker2)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_decision() == RequestFileDecision.CONFLICTED

    bll.approve_file(release_request, request_file, checker3)
    rfile = _get_request_file(release_request, path)
    assert rfile.get_decision() == RequestFileDecision.APPROVED


def test_reject_file(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    assert "group" in release_request.filegroups

    checker = factories.create_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    bll.reject_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.REJECTED
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_REJECT,
        user=checker,
        path=path,
        group="group",
    )


def test_approve_then_reject_file(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    bll.approve_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.APPROVED
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    bll.reject_file(release_request, request_file, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == RequestFileVote.REJECTED
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


@pytest.mark.parametrize("review", [RequestFileVote.APPROVED, RequestFileVote.REJECTED])
def test_reviewreset_then_reset_review_file(bll, review):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_user(output_checker=True)
    request_file = release_request.get_request_file_from_output_path(path)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    if review == RequestFileVote.APPROVED:
        bll.approve_file(release_request, request_file, checker)
    elif review == RequestFileVote.REJECTED:
        bll.reject_file(release_request, request_file, checker)
    else:
        assert False

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) == review
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    bll.reset_review_file(release_request, path, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


def test_reset_review_file_no_reviews(bll):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )
    checker = factories.create_user(output_checker=True)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE

    with pytest.raises(bll.FileReviewNotFound):
        bll.reset_review_file(release_request, path, checker)

    rfile = _get_request_file(release_request, path)
    assert rfile.get_file_vote_for_user(checker) is None
    assert rfile.get_decision() == RequestFileDecision.INCOMPLETE


@pytest.mark.parametrize(
    "votes, decision",
    [
        (["APPROVED", "APPROVED"], "APPROVED"),
        (["REJECTED", "REJECTED"], "REJECTED"),
        (["APPROVED", "REJECTED"], "CONFLICTED"),
    ],
)
def test_request_file_status_decision(bll, votes, decision):
    path = Path("path/file1.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file(path=path)],
    )

    for i, vote in enumerate(votes):
        checker = factories.create_user(f"checker{i}", [], True)
        request_file = release_request.get_request_file_from_output_path(path)

        if vote == "APPROVED":
            bll.approve_file(release_request, request_file, checker)
        else:
            bll.reject_file(release_request, request_file, checker)

        rfile = _get_request_file(release_request, path)
        assert rfile.get_file_vote_for_user(checker) == RequestFileVote[vote]

        if i == 0:
            assert rfile.get_decision() == RequestFileDecision.INCOMPLETE
        else:
            assert rfile.get_decision() == RequestFileDecision[decision]


def test_mark_file_undecided(bll):
    # Set up submitted request
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.RETURNED,
        files=[factories.request_file(path="file.txt", rejected=True)],
    )

    # first default output-checker
    checker = factories.create_user("output-checker-0")

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
        # can only mark undecided for a rejected file on a returned request
        (RequestStatus.SUBMITTED, RequestFileVote.REJECTED, False),
        (RequestStatus.RETURNED, RequestFileVote.APPROVED, False),
        (RequestStatus.RETURNED, RequestFileVote.REJECTED, True),
    ],
)
def test_mark_file_undecided_permission_errors(
    bll, request_status, file_status, allowed
):
    # Set up that already has 2 reviews; these are both rejected for
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
                rejected=file_status == RequestFileVote.REJECTED,
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
        with pytest.raises(bll.ApprovalPermissionDenied):
            bll.mark_file_undecided(release_request, review, path, checkers[0])


def test_review_request(bll):
    checker = factories.create_user("checker", output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="test.txt", rejected=True, checkers=[checker]),
            factories.request_file(path="test1.txt"),
        ],
    )
    # first file is already rejected, second file is not reviewed
    with pytest.raises(
        bll.RequestReviewDenied,
        match="You must review all files to complete your review",
    ):
        bll.review_request(release_request, checker)
    assert "checker" not in release_request.completed_reviews
    assert release_request.status == RequestStatus.SUBMITTED

    # approved second file
    factories.review_file(
        release_request, "test1.txt", RequestFileVote.APPROVED, checker
    )
    release_request = factories.refresh_release_request(release_request)
    bll.review_request(release_request, checker)
    release_request = factories.refresh_release_request(release_request)
    assert "checker" in release_request.completed_reviews
    assert release_request.status == RequestStatus.PARTIALLY_REVIEWED

    # re-review
    with pytest.raises(
        bll.RequestReviewDenied, match="You have already completed your review"
    ):
        bll.review_request(release_request, checker)


def test_review_request_non_submitted_status(bll):
    checker = factories.create_user(output_checker=True)
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.WITHDRAWN,
        withdrawn_after=RequestStatus.PENDING,
        files=[
            factories.request_file(
                path="test.txt",
                checkers=[checker, factories.create_user(output_checker=True)],
                approved=True,
            ),
        ],
    )
    with pytest.raises(
        bll.RequestPermissionDenied, match="Cannot review request in state WITHDRAWN"
    ):
        bll.review_request(release_request, checker)


def test_review_request_non_output_checker(bll):
    user = factories.create_user("non-output-checker")
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="test.txt", approved=True),
        ],
    )
    with pytest.raises(
        bll.RequestPermissionDenied, match="Only an output checker can review a request"
    ):
        bll.review_request(release_request, user)


def test_review_request_more_than_2_checkers(bll):
    checkers = [factories.create_user(f"checker_{i}", [], True) for i in range(3)]
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
    assert len(release_request.completed_reviews) == 3


def test_review_request_race_condition(bll):
    """
    In a potential race condition, a
    """
    checkers = [
        factories.create_user("checker", output_checker=True),
        factories.create_user("checker1", output_checker=True),
        factories.create_user("checker2", output_checker=True),
        factories.create_user("checker3", output_checker=True),
    ]
    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(path="test.txt", approved=True, checkers=checkers),
            factories.request_file(path="test1.txt", rejected=True, checkers=checkers),
        ],
    )
    # first checker completes review
    bll.review_request(release_request, checkers[0])

    # mock race condition by patching the number of completed reviews to return 1 initially
    # This is called AFTER recording the review. If it's a first review, we expect there to
    # be 1 completed reivew, if it's a second review we expect there to be 2.
    # However in a race condition, this could be review 2, but the count is incorrectly
    # retrieved as 1
    with patch(
        "airlock.business_logic.ReleaseRequest.completed_reviews_count"
    ) as completed_reviews:
        completed_reviews.side_effect = [1, 2]
        bll.review_request(release_request, checkers[1])

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.REVIEWED

    # Similarly, there could be a race between reviewer 2 and 3. For reviewer 2, we want to move the
    # status to REVIEWED, for review 3 we should do nothing
    with patch(
        "airlock.business_logic.ReleaseRequest.completed_reviews_count"
    ) as completed_reviews:
        completed_reviews.side_effect = [2, 3]
        bll.review_request(release_request, checkers[2])
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.REVIEWED

    # The request must be in a reviewed state (part or fully)
    # Set status to submitted (mock check status to allow the invalid transition)
    with patch("airlock.business_logic.BusinessLogicLayer.check_status"):
        bll.set_status(release_request, RequestStatus.SUBMITTED, checkers[0])

    with pytest.raises(bll.InvalidStateTransition):
        with patch(
            "airlock.business_logic.ReleaseRequest.completed_reviews_count"
        ) as completed_reviews:
            completed_reviews.side_effect = [2, 4]
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


def test_group_edit_author(bll):
    author = factories.create_user("author", ["workspace"], False)
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

    audit_log = bll.get_audit_log(request=release_request.id)
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
    author = factories.create_user("author", ["workspace"], False)
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
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_edit(release_request, "group", "foo", "bar", other)


@pytest.mark.parametrize(
    "status", [RequestStatus.APPROVED, RequestStatus.REJECTED, RequestStatus.WITHDRAWN]
)
def test_group_edit_not_editable(bll, status):
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[factories.request_file(approved=True)],
        withdrawn_after=RequestStatus.PENDING,
    )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_edit(release_request, "group", "foo", "bar", author)


def test_group_edit_bad_group(bll):
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    with pytest.raises(bll.FileNotFound):
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
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user("checker", ["workspace"], True)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=status,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert release_request.filegroups["group"].comments == []

    # check all visibilities
    bll.group_comment_create(
        release_request, "group", "private", CommentVisibility.PRIVATE, checker
    )
    bll.group_comment_create(
        release_request, "group", "public", CommentVisibility.PUBLIC, author
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
    assert comments[0].comment == "private"
    assert comments[0].visibility == CommentVisibility.PRIVATE
    assert comments[0].author == "checker"

    assert comments[1].comment == "public"
    assert comments[1].visibility == CommentVisibility.PUBLIC
    assert comments[1].author == "author"

    audit_log = bll.get_audit_log(request=release_request.id)

    assert audit_log[1].request == release_request.id
    assert audit_log[1].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[1].user == checker.username
    assert audit_log[1].extra["group"] == "group"
    assert audit_log[1].extra["comment"] == "private"

    assert audit_log[0].request == release_request.id
    assert audit_log[0].type == AuditEventType.REQUEST_COMMENT
    assert audit_log[0].user == "author"
    assert audit_log[0].extra["group"] == "group"
    assert audit_log[0].extra["comment"] == "public"


def test_group_comment_create_permissions(bll):
    author = factories.create_user("author", ["workspace"], False)
    collaborator = factories.create_user("collaborator", ["workspace"], False)
    other = factories.create_user("other", ["other"], False)
    checker = factories.create_user("checker", ["other"], True)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.PENDING,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert len(release_request.filegroups["group"].comments) == 0

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_comment_create(
            release_request, "group", "question?", CommentVisibility.PUBLIC, other
        )

    bll.group_comment_create(
        release_request, "group", "collaborator", CommentVisibility.PUBLIC, collaborator
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1

    bll.group_comment_create(
        release_request, "group", "checker", CommentVisibility.PUBLIC, checker
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 2


def test_group_comment_delete_success(bll):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    assert release_request.filegroups["group"].comments == []

    bll.group_comment_create(
        release_request, "group", "typo comment", CommentVisibility.PUBLIC, other
    )
    bll.group_comment_create(
        release_request, "group", "not-a-typo comment", CommentVisibility.PUBLIC, other
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

    audit_log = bll.get_audit_log(request=release_request.id)
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


def test_group_comment_delete_permissions(bll):
    author = factories.create_user("author", ["workspace"], False)
    collaborator = factories.create_user("collaborator", ["workspace"], False)
    other = factories.create_user("other", ["other"], False)
    # checker who does not have access to workspace
    checker = factories.create_user("checker", [], True)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    bll.group_comment_create(
        release_request, "group", "author comment", CommentVisibility.PUBLIC, author
    )
    bll.group_comment_create(
        release_request, "group", "checker comment", CommentVisibility.PUBLIC, checker
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 2
    test_comment = release_request.filegroups["group"].comments[0]
    checker_comment = release_request.filegroups["group"].comments[1]

    for user in [collaborator, other, checker]:
        with pytest.raises(bll.RequestPermissionDenied):
            bll.group_comment_delete(release_request, "group", test_comment.id, user)

    for user in [collaborator, author, other]:
        with pytest.raises(bll.RequestPermissionDenied):
            bll.group_comment_delete(release_request, "group", checker_comment.id, user)

    assert len(release_request.filegroups["group"].comments) == 2

    # users can delete their own comments
    bll.group_comment_delete(release_request, "group", test_comment.id, author)
    bll.group_comment_delete(release_request, "group", checker_comment.id, checker)
    release_request = factories.refresh_release_request(release_request)
    assert len(release_request.filegroups["group"].comments) == 0


def test_group_comment_create_invalid_params(bll):
    author = factories.create_user("author", ["workspace"], False)
    collaborator = factories.create_user("collaborator", ["workspace"], False)

    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.SUBMITTED,
        files=[factories.request_file("group", "test/file.txt")],
    )

    with pytest.raises(bll.APIException):
        bll.group_comment_delete(release_request, "group", 1, author)

    bll.group_comment_create(
        release_request, "group", "author comment", CommentVisibility.PUBLIC, author
    )
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1
    test_comment = release_request.filegroups["group"].comments[0]

    with pytest.raises(bll.APIException):
        bll.group_comment_delete(
            release_request, "badgroup", test_comment.id, collaborator
        )

    assert len(release_request.filegroups["group"].comments) == 1


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"repo": None, "outputs": {}},
        {"repo": None, "outputs": {"file.txt": {"commit": "commit"}}},
    ],
)
def test_coderepo_from_workspace_no_repo_in_manifest(bll, manifest):
    workspace = factories.create_workspace("workspace")
    workspace.manifest = manifest
    with pytest.raises(CodeRepo.RepoNotFound):
        CodeRepo.from_workspace(workspace, "commit")


def test_coderepo_from_workspace(bll):
    workspace = factories.create_workspace("workspace")
    factories.create_repo(workspace)
    # No root repo, retrieved from first output in manifest instead
    workspace.manifest["repo"] = None
    CodeRepo.from_workspace(
        workspace, workspace.manifest["outputs"]["foo.txt"]["commit"]
    )

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
    DataAccessLayerProtocol,
    RequestFileReviewStatus,
    RequestFileType,
    RequestStatus,
    UserFileReviewStatus,
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


def test_workspace_get_workspace_status(bll):
    path = UrlPath("foo/bar.txt")
    workspace = factories.create_workspace("workspace")
    user = factories.create_user(workspaces=["workspace"])

    assert workspace.get_workspace_status(path) is None

    factories.write_workspace_file(workspace, path, contents="foo")
    assert workspace.get_workspace_status(path) == WorkspaceFileStatus.UNRELEASED

    release_request = factories.create_release_request(workspace, user=user)
    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_status(path) == WorkspaceFileStatus.UNRELEASED

    factories.write_request_file(release_request, "group", path)
    # refresh workspace
    workspace = bll.get_workspace("workspace", user)
    assert workspace.get_workspace_status(path) == WorkspaceFileStatus.UNDER_REVIEW

    factories.write_workspace_file(workspace, path, contents="changed")
    assert workspace.get_workspace_status(path) == WorkspaceFileStatus.CONTENT_UPDATED


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
    factories.complete_independent_review(release_request)

    release_request = factories.refresh_release_request(release_request)
    bll.set_status(release_request, RequestStatus.APPROVED, checker)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.release_files(release_request, checker)
    assert_last_notification(mock_notifications, "request_approved")


def test_provider_request_release_files(mock_old_api, mock_notifications, bll, freezer):
    old_api.create_release.return_value = "jobserver_id"
    author = factories.create_user("author", ["workspace"])
    checker = factories.create_user("checker", [], output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )
    relpath = UrlPath("test/file.txt")
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
    factories.complete_independent_review(release_request)
    release_request = factories.refresh_release_request(release_request)
    factories.bll.set_status(release_request, RequestStatus.APPROVED, checker)

    abspath = release_request.abspath("group" / relpath)

    freezer.move_to("2022-01-01T12:34:56")
    bll.release_files(release_request, checker)

    # TODO: when we do this, it reverses the order of the audit log in the data struct??
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
        "workspace", json.dumps(expected_json), checker.username
    )
    old_api.upload_file.assert_called_once_with(
        "jobserver_id", relpath, abspath, checker.username
    )

    notification_responses = parse_notification_responses(mock_notifications)
    # Notifications expected for:
    # - write file x2
    # - set status to partially reviewed
    # - set status to reviewed
    # - set status to approved
    # - set status to released
    assert notification_responses["count"] == 6
    request_json = notification_responses["request_json"]
    assert request_json[0]["event_type"] == "request_updated"
    assert request_json[1]["event_type"] == "request_updated"
    assert request_json[2]["event_type"] == "request_partially_reviewed"
    assert request_json[3]["event_type"] == "request_reviewed"
    assert request_json[4]["event_type"] == "request_approved"
    assert request_json[5]["event_type"] == "request_released"

    audit_log = bll.get_audit_log(request=release_request.id)

    assert audit_log[5].type == AuditEventType.REQUEST_APPROVE
    assert audit_log[5].user == checker.username
    assert audit_log[5].request == release_request.id
    assert audit_log[5].workspace == "workspace"

    assert audit_log[6].type == AuditEventType.REQUEST_FILE_RELEASE
    assert audit_log[6].user == checker.username
    assert audit_log[6].request == release_request.id
    assert audit_log[6].workspace == "workspace"
    assert audit_log[6].path == Path("test/file.txt")

    assert audit_log[7].type == AuditEventType.REQUEST_RELEASE
    assert audit_log[7].user == checker.username
    assert audit_log[7].request == release_request.id
    assert audit_log[7].workspace == "workspace"


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
    release_request1 = factories.create_release_request(
        "workspace", other_user, id="r1", status=RequestStatus.SUBMITTED
    )
    factories.write_request_file(release_request1, "group", "file.txt", approved=True)
    factories.complete_independent_review(release_request1)
    release_request1 = factories.refresh_release_request(release_request1)
    factories.bll.set_status(release_request1, RequestStatus.RETURNED, output_checker)

    # requests not visible to output checker
    # status returned, but authored by output checker
    release_request2 = factories.create_release_request(
        "workspace", user, id="r2", status=RequestStatus.SUBMITTED
    )
    factories.write_request_file(release_request2, "group", "file.txt", approved=True)
    factories.complete_independent_review(release_request2)
    release_request2 = factories.refresh_release_request(release_request2)
    factories.bll.set_status(release_request2, RequestStatus.RETURNED, output_checker)

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
        factories.create_release_request(ws, user_n, status=status)

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
    factories.create_release_request(
        "workspace", other_user, id="r1", status=RequestStatus.APPROVED
    )

    # requests not visible to output checker
    # status returned, but authored by output checker
    factories.create_release_request(
        "workspace", user, id="r2", status=RequestStatus.APPROVED
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
        factories.create_release_request(ws, user_n, status=status)

    assert set(r.id for r in bll.get_approved_requests(user)) == set(expected)


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
        (RequestStatus.PENDING, RequestStatus.PARTIALLY_REVIEWED, False, False),
        (RequestStatus.PENDING, RequestStatus.REVIEWED, False, False),
        (RequestStatus.PENDING, RequestStatus.APPROVED, False, False),
        (RequestStatus.PENDING, RequestStatus.REJECTED, False, False),
        (RequestStatus.PENDING, RequestStatus.RELEASED, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.PENDING, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.PARTIALLY_REVIEWED, False, True),
        (RequestStatus.SUBMITTED, RequestStatus.REVIEWED, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.APPROVED, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.REJECTED, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.WITHDRAWN, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.RETURNED, False, False),
        (RequestStatus.SUBMITTED, RequestStatus.RELEASED, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.PENDING, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.SUBMITTED, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.REVIEWED, False, True),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.APPROVED, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.REJECTED, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.RELEASED, False, False),
        (RequestStatus.PARTIALLY_REVIEWED, RequestStatus.WITHDRAWN, False, False),
        (RequestStatus.REVIEWED, RequestStatus.PENDING, False, False),
        (RequestStatus.REVIEWED, RequestStatus.SUBMITTED, False, False),
        (RequestStatus.REVIEWED, RequestStatus.PARTIALLY_REVIEWED, False, False),
        (RequestStatus.REVIEWED, RequestStatus.RETURNED, False, True),
        (RequestStatus.REVIEWED, RequestStatus.APPROVED, False, True),
        (RequestStatus.REVIEWED, RequestStatus.REJECTED, False, True),
        (RequestStatus.REVIEWED, RequestStatus.WITHDRAWN, False, False),
        (RequestStatus.RETURNED, RequestStatus.SUBMITTED, True, False),
        (RequestStatus.RETURNED, RequestStatus.WITHDRAWN, True, False),
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

    release_request1 = factories.create_request_at_state(
        "workspace1",
        author,
        current,
        files=[factories.request_file(approved=True)],
    )
    release_request2 = factories.create_request_at_state(
        "workspace2",
        author,
        current,
        files=[factories.request_file(approved=True)],
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
        (
            RequestStatus.SUBMITTED,
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

    if current != RequestStatus.PENDING:
        bll.set_status(release_request, RequestStatus.SUBMITTED, user=users["author"])
    if current in [RequestStatus.APPROVED, RequestStatus.RETURNED]:
        bll.set_status(release_request, current, user=users["checker"])

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
    assert caplog.records[-1].message == "something went wrong"


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


def test_submit_request(bll, mock_notifications):
    """
    From pending
    """
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request(
        "workspace", user=author, status=RequestStatus.PENDING
    )
    factories.write_request_file(release_request, "group", "test/file.txt")
    bll.submit_request(release_request, author)
    assert release_request.status == RequestStatus.SUBMITTED
    assert_last_notification(mock_notifications, "request_submitted")


def test_return_request_files_not_reviewed(bll, mock_notifications):
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user("checker", ["workspace"], True)
    release_request = factories.create_release_request(
        "workspace", user=author, status=RequestStatus.SUBMITTED
    )
    # file approved twice
    factories.write_request_file(
        release_request, "group", "test/file.txt", approved=True
    )
    # file rejected twice
    factories.write_request_file(
        release_request, "group", "test/file1.txt", rejected=True
    )
    # file with one approval
    factories.write_request_file(release_request, "group", "test/file2.txt")
    release_request = factories.refresh_release_request(release_request)
    bll.approve_file(release_request, UrlPath("test/file2.txt"), user=checker)
    release_request = factories.refresh_release_request(release_request)

    with pytest.raises(
        BusinessLogicLayer.RequestPermissionDenied, match="request has unreviewed files"
    ):
        bll.set_status(release_request, RequestStatus.RETURNED, checker)


def test_resubmit_request(bll, mock_notifications):
    """
    From returned
    Files with rejected status are moved to undecided
    """
    author = factories.create_user("author", ["workspace"], False)
    checker = factories.create_user("checker", [], True)
    release_request = factories.create_release_request(
        "workspace", user=author, status=RequestStatus.SUBMITTED
    )
    # One file is approved by both, one is rejected
    factories.write_request_file(
        release_request, "group", "test/file.txt", approved=True
    )
    factories.write_request_file(
        release_request, "group", "test/file1.txt", rejected=True
    )
    release_request = factories.refresh_release_request(release_request)

    # checker returns request
    bll.set_status(release_request, RequestStatus.RETURNED, checker)

    # author re-submits with no changes to files
    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.SUBMITTED
    assert_last_notification(mock_notifications, "request_resubmitted")

    for i in range(2):
        user = factories.create_user(f"output-checker-{i}", output_checker=True)
        # approved file review is still approved
        approved_file = release_request.get_request_file_from_output_path(
            UrlPath("test/file.txt")
        )
        assert approved_file.get_status_for_user(user) == UserFileReviewStatus.APPROVED

        # rejected file review is now undecided approved
        rejected_file = release_request.get_request_file_from_output_path(
            UrlPath("test/file1.txt")
        )
        assert rejected_file.get_status_for_user(user) == UserFileReviewStatus.UNDECIDED


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


def test_add_file_to_request_invalid_file_type(bll):
    author = factories.create_user("author", ["workspace"], False)

    path = UrlPath("path/file.foo")
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

    path = UrlPath("path/file.txt")
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
    path = UrlPath("path/file.txt")
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
    path1 = UrlPath("path/file1.txt")
    path2 = UrlPath("path/file2.txt")
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
    path1 = UrlPath("path/file1.txt")
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


def test_request_all_files_by_name(bll):
    author = factories.create_user(username="author", workspaces=["workspace"])
    path = UrlPath("path/file.txt")
    supporting_path = UrlPath("path/supporting_file.txt")
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

    release_request = factories.refresh_release_request(release_request)
    # all_files_by_name consists of output files and supporting files
    assert release_request.all_files_by_name.keys() == {path, supporting_path}

    filegroup = release_request.filegroups["default"]
    assert len(filegroup.files) == 2
    assert len(filegroup.output_files) == 1
    assert len(filegroup.supporting_files) == 1


def test_request_release_get_request_file_from_urlpath(bll):
    path = UrlPath("foo/bar.txt")
    supporting_path = UrlPath("foo/bar1.txt")
    release_request = factories.create_release_request("id")
    factories.write_request_file(release_request, "default", path)
    factories.write_request_file(
        release_request, "default", supporting_path, filetype=RequestFileType.SUPPORTING
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
    path = UrlPath("path/file.txt")
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


def _get_request_file(bll, release_request, path):
    """Syntactic sugar to make the tests a little more readable"""
    # refresh
    release_request = bll.get_release_request(
        release_request.id, factories.create_user(output_checker=True)
    )
    return release_request.get_request_file_from_output_path(path)


def test_approve_file_not_submitted(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


def test_approve_file_not_your_own(bll):
    release_request, path, author = setup_empty_release_request()

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, author)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(author) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


def test_approve_file_not_checker(bll):
    release_request, path, author = setup_empty_release_request()
    author2 = factories.create_user("author2", [], False)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, path, author2)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(author) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


def test_approve_file_not_part_of_request(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    bad_path = UrlPath("path/file2.txt")
    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.approve_file(release_request, bad_path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


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

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


def test_approve_file(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    bll.approve_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) == UserFileReviewStatus.APPROVED
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    audit_log = bll.get_audit_log(request=release_request.id)
    assert audit_log[0] == AuditEvent.from_request(
        release_request,
        AuditEventType.REQUEST_FILE_APPROVE,
        user=checker,
        path=path,
    )


def test_approve_file_requires_two_plus(bll):
    release_request, path, author = setup_empty_release_request()
    checker1 = factories.create_user("checker1", [], True)
    checker2 = factories.create_user("checker2", [], True)
    checker3 = factories.create_user("checker3", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    bll.approve_file(release_request, path, checker1)
    bll.reject_file(release_request, path, checker2)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status() == RequestFileReviewStatus.CONFLICTED

    bll.approve_file(release_request, path, checker3)
    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status() == RequestFileReviewStatus.APPROVED


def test_reject_file(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    bll.reject_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) == UserFileReviewStatus.REJECTED
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

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

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    bll.approve_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) == UserFileReviewStatus.APPROVED
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    bll.reject_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) == UserFileReviewStatus.REJECTED
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


@pytest.mark.parametrize(
    "review", [UserFileReviewStatus.APPROVED, UserFileReviewStatus.REJECTED]
)
def test_reviewreset_then_reset_review_file(bll, review):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    if review == UserFileReviewStatus.APPROVED:
        bll.approve_file(release_request, path, checker)
    elif review == UserFileReviewStatus.REJECTED:
        bll.reject_file(release_request, path, checker)
    else:
        assert False

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) == review
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    bll.reset_review_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


def test_reset_review_file_no_reviews(bll):
    release_request, path, author = setup_empty_release_request()
    checker = factories.create_user("checker", [], True)

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE

    with pytest.raises(bll.FileReviewNotFound):
        bll.reset_review_file(release_request, path, checker)

    rfile = _get_request_file(bll, release_request, path)
    assert rfile.get_status_for_user(checker) is None
    assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE


@pytest.mark.parametrize(
    "reviews, final_review",
    [
        (["APPROVED", "APPROVED"], "APPROVED"),
        (["REJECTED", "REJECTED"], "REJECTED"),
        (["APPROVED", "REJECTED"], "CONFLICTED"),
    ],
)
def test_request_file_status_approved(bll, reviews, final_review):
    release_request, path, author = setup_empty_release_request()

    bll.add_file_to_request(release_request, path, author)
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.SUBMITTED, user=author
    )

    for i, review in enumerate(reviews):
        checker = factories.create_user(f"checker{i}", [], True)

        if review == "APPROVED":
            bll.approve_file(release_request, path, checker)
        else:
            bll.reject_file(release_request, path, checker)

        rfile = _get_request_file(bll, release_request, path)
        assert rfile.get_status_for_user(checker) == UserFileReviewStatus[review]

        if i == 0:
            assert rfile.get_status() == RequestFileReviewStatus.INCOMPLETE
        else:
            assert rfile.get_status() == RequestFileReviewStatus[final_review]


def test_mark_file_undecided(bll):
    # Set up submitted request
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED
    )

    # Write a request file that already has 2 rejected reviewsso that we can
    # set the request to RETURNED
    path = Path("path/file.txt")
    factories.write_request_file(
        release_request,
        "default",
        path,
        rejected=True,
    )

    # setup the initial file status for our test checker
    checker = factories.create_user("checker", [], True)
    bll.reject_file(release_request, path, checker)
    release_request = factories.refresh_release_request(release_request)

    # set the request to returned
    bll.set_status(
        release_request=release_request, to_status=RequestStatus.RETURNED, user=checker
    )

    # mark file review as undecided
    review = release_request.get_request_file_from_output_path(path).reviews[
        checker.username
    ]
    bll.mark_file_undecided(release_request, review, path, checker)
    release_request = factories.refresh_release_request(release_request)
    review = release_request.get_request_file_from_output_path(path).reviews[
        checker.username
    ]
    assert review.status == UserFileReviewStatus.UNDECIDED


@pytest.mark.parametrize(
    "request_status,file_status",
    [
        # can only mark undecided for a rejected file on a returned request
        (RequestStatus.SUBMITTED, UserFileReviewStatus.REJECTED),
        (RequestStatus.APPROVED, UserFileReviewStatus.REJECTED),
        (RequestStatus.RELEASED, UserFileReviewStatus.REJECTED),
        (RequestStatus.RETURNED, UserFileReviewStatus.APPROVED),
    ],
)
def test_mark_file_undecided_permission_errors(bll, request_status, file_status):
    # Set up submitted request
    release_request = factories.create_release_request(
        "workspace", status=RequestStatus.SUBMITTED
    )

    # Write a request file that already has 2 reviews; these are both rejected for
    # requests that we want to be in RETURNED status, and approved
    # for SUBMITTED/APPROVED/RELEASED, so we can set the request status
    path = Path("path/file.txt")
    factories.write_request_file(
        release_request,
        "default",
        path,
        rejected=file_status == RequestStatus.RETURNED,
        approved=file_status != RequestStatus.RETURNED,
    )

    # setup the initial file status for our test checker
    checker = factories.create_user("checker", [], True)
    if file_status == UserFileReviewStatus.APPROVED:
        bll.approve_file(release_request, path, checker)
    else:
        bll.reject_file(release_request, path, checker)
    release_request = factories.refresh_release_request(release_request)

    # set the request status
    # For released, first move the initially submitted request to approved
    if request_status == RequestStatus.RELEASED:
        bll.set_status(
            release_request=release_request,
            to_status=RequestStatus.APPROVED,
            user=checker,
        )
    # If necessary, move the initally submitted request to the final state for testing
    if request_status != RequestStatus.SUBMITTED:
        bll.set_status(
            release_request=release_request, to_status=request_status, user=checker
        )

    review = release_request.get_request_file_from_output_path(path).reviews[
        checker.username
    ]
    assert review.status == file_status
    with pytest.raises(bll.ApprovalPermissionDenied):
        bll.mark_file_undecided(release_request, review, path, checker)


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
    bll, mock_notifications, new_context, new_controls, expected_updates, settings
):
    # Set the output checking org and repo to override any local settings
    settings.AIRLOCK_OUTPUT_CHECKING_ORG = settings.AIRLOCK_OUTPUT_CHECKING_REPO = None
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
        }
    else:
        assert notification_responses["count"] == 1


@pytest.mark.parametrize(
    "org,repo,sent_in_notification", [(None, None, False), ("test", "foo", True)]
)
def test_notifications_org_repo(
    bll, mock_notifications, settings, org, repo, sent_in_notification
):
    settings.AIRLOCK_OUTPUT_CHECKING_ORG = org
    settings.AIRLOCK_OUTPUT_CHECKING_REPO = repo
    author = factories.create_user("author", ["workspace"], False)
    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)
    bll.set_status(release_request, RequestStatus.SUBMITTED, author)

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
def test_group_comment_create_success(
    bll, mock_notifications, status, notification_count
):
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

    bll.group_comment_create(release_request, "group", "question?", other)
    bll.group_comment_create(release_request, "group", "answer!", author)

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


def test_group_comment_create_permissions(bll):
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
        bll.group_comment_create(release_request, "group", "question?", other)

    bll.group_comment_create(release_request, "group", "collaborator", collaborator)
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1

    bll.group_comment_create(release_request, "group", "checker", checker)
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 2


def test_group_comment_delete_success(bll):
    author = factories.create_user("author", ["workspace"], False)
    other = factories.create_user("other", ["workspace"], False)
    status = RequestStatus.SUBMITTED
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

    bll.group_comment_create(release_request, "group", "typo comment", other)
    bll.group_comment_create(release_request, "group", "not-a-typo comment", other)

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

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)

    bll.group_comment_create(release_request, "group", "author comment", author)
    release_request = factories.refresh_release_request(release_request)

    assert len(release_request.filegroups["group"].comments) == 1
    test_comment = release_request.filegroups["group"].comments[0]

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_comment_delete(
            release_request, "group", test_comment.id, collaborator
        )

    with pytest.raises(bll.RequestPermissionDenied):
        bll.group_comment_delete(release_request, "group", test_comment.id, other)

    assert len(release_request.filegroups["group"].comments) == 1


def test_group_comment_create_invalid_params(bll):
    author = factories.create_user("author", ["workspace"], False)
    collaborator = factories.create_user("collaborator", ["workspace"], False)

    release_request = factories.create_release_request("workspace", user=author)
    factories.write_request_file(
        release_request,
        "group",
        "test/file.txt",
    )
    release_request = factories.refresh_release_request(release_request)

    with pytest.raises(bll.APIException):
        bll.group_comment_delete(release_request, "group", 1, author)

    bll.group_comment_create(release_request, "group", "author comment", author)
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

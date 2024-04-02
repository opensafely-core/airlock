import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest
from django.conf import settings

import old_api
from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    BusinessLogicLayer,
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
        in workspace.get_contents_url("foo/bar.html")
    )


def test_workspace_request_filetype(bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "foo/bar.txt")
    assert workspace.request_filetype("foo/bar.txt") is None


def test_request_container():
    release_request = factories.create_release_request("workspace", id="id")
    factories.write_request_file(release_request, "group", "bar.html")

    assert release_request.root() == settings.REQUEST_DIR / "workspace/id"
    assert release_request.get_id() == "id"
    assert (
        release_request.get_url("group/bar.html") == "/requests/view/id/group/bar.html"
    )
    assert (
        "/requests/content/id/group/bar.html?cache_id="
        in release_request.get_contents_url("group/bar.html")
    )


@pytest.mark.parametrize("output_checker", [False, True])
def test_provider_get_workspaces_for_user(output_checker):
    factories.create_workspace("foo")
    factories.create_workspace("bar")
    factories.create_workspace("not-allowed")
    workspaces = {
        "foo": {"project": "project 1"},
        "bar": {"project": "project 2"},
        "not-exists": {"project": "project 3"},
    }
    user = factories.create_user(workspaces=workspaces, output_checker=output_checker)

    bll = BusinessLogicLayer(data_access_layer=None)

    assert bll.get_workspaces_for_user(user) == [
        Workspace("foo", {"project": "project 1"}),
        Workspace("bar", {"project": "project 2"}),
    ]


@pytest.fixture
def mock_old_api(monkeypatch):
    monkeypatch.setattr(
        old_api, "create_release", MagicMock(autospec=old_api.create_release)
    )
    monkeypatch.setattr(old_api, "upload_file", MagicMock(autospec=old_api.upload_file))


def test_provider_request_release_files_not_approved():
    author = factories.create_user("author", ["workspace"])
    checker = factories.create_user("checker", [], output_checker=True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=RequestStatus.SUBMITTED,
    )

    bll = BusinessLogicLayer(data_access_layer=None)
    with pytest.raises(bll.InvalidStateTransition):
        bll.release_files(release_request, checker)


def test_provider_request_release_files_invalid_file_type():
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
        factories.write_request_file(release_request, "group", relpath, "test")

    factories.bll.set_status(release_request, RequestStatus.APPROVED, checker)
    bll = BusinessLogicLayer(data_access_layer=None)
    with pytest.raises(bll.RequestPermissionDenied):
        bll.release_files(release_request, checker)


def test_provider_request_release_files(mock_old_api):
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
    factories.write_request_file(release_request, "group", relpath, "test")
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
                "metadata": {"tool": "airlock"},
                "review": None,
            }
        ],
        "metadata": {"tool": "airlock"},
        "review": None,
    }

    old_api.create_release.assert_called_once_with(
        "workspace", json.dumps(expected_json), checker.username
    )
    old_api.upload_file.assert_called_once_with(
        "jobserver_id", relpath, abspath, checker.username
    )


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
    bll._create_release_request(author=user.username, workspace="workspace")

    with pytest.raises(Exception):
        bll.get_current_request("workspace", user)


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
        (RequestStatus.APPROVED, RequestStatus.REJECTED, False, True),
        (RequestStatus.APPROVED, RequestStatus.WITHDRAWN, True, False),
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
        "workspace", user=author, status=current
    )
    release_request2 = factories.create_release_request(
        "workspace", user=author, status=current
    )

    if valid_author:
        bll.set_status(release_request1, future, user=author)
        assert release_request1.status == future
        audit_log = bll.get_audit_log(request=release_request1.id)
        assert audit_log[0].type == audit_type
        assert audit_log[0].user == author.username
        assert audit_log[0].request == release_request1.id
        assert audit_log[0].workspace == "workspace"
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
        assert audit_log[0].workspace == "workspace"
    else:
        with pytest.raises((bll.InvalidStateTransition, bll.RequestPermissionDenied)):
            bll.set_status(release_request2, future, user=checker)


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
    "status,success",
    [
        (RequestStatus.PENDING, True),
        (RequestStatus.SUBMITTED, True),
        (RequestStatus.APPROVED, False),
        (RequestStatus.REJECTED, False),
        (RequestStatus.RELEASED, False),
        (RequestStatus.WITHDRAWN, False),
    ],
)
def test_add_file_to_request_states(status, success, bll):
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


def test_withdraw_file_from_request_pending(bll):
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


def test_withdraw_file_from_request_submitted(bll):
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


# add DAL method names to this if they do not require auditing
DAL_AUDIT_EXCLUDED = {
    "get_release_request",
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

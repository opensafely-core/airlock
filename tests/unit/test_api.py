import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from django.conf import settings

import old_api
from airlock.api import BusinessLogicLayer, Status, UrlPath, Workspace
from airlock.users import User
from tests import factories


pytestmark = pytest.mark.django_db


def test_workspace_container():
    workspace = factories.create_workspace("workspace")

    assert workspace.root() == settings.WORKSPACE_DIR / "workspace"
    assert workspace.get_id() == "workspace"
    assert (
        workspace.get_url("foo/bar.html") == "/workspaces/view/workspace/foo/bar.html"
    )
    assert (
        workspace.get_contents_url("foo/bar.html")
        == "/workspaces/content/workspace/foo/bar.html"
    )


def test_request_container():
    release_request = factories.create_release_request("workspace", id="id")

    assert release_request.root() == settings.REQUEST_DIR / "workspace/id"
    assert release_request.get_id() == "id"
    assert (
        release_request.get_url("group/bar.html") == "/requests/view/id/group/bar.html"
    )
    assert (
        release_request.get_contents_url("group/bar.html")
        == "/requests/content/id/group/bar.html"
    )


@pytest.mark.parametrize(
    "user_workspaces,output_checker,expected",
    [
        ([], False, []),
        (["allowed"], False, ["allowed"]),
        ([], True, ["allowed", "not-allowed"]),
        (["allowed", "notexist"], False, ["allowed"]),
    ],
)
def test_provider_get_workspaces_for_user(user_workspaces, output_checker, expected):
    factories.create_workspace("allowed")
    factories.create_workspace("not-allowed")
    user = User(1, "test", user_workspaces, output_checker)

    api = BusinessLogicLayer(data_access_layer=None)

    assert set(api.get_workspaces_for_user(user)) == set(Workspace(w) for w in expected)


@pytest.fixture
def mock_old_api(monkeypatch):
    monkeypatch.setattr(
        old_api, "create_release", MagicMock(autospec=old_api.create_release)
    )
    monkeypatch.setattr(old_api, "upload_file", MagicMock(autospec=old_api.upload_file))


def test_provider_request_release_files_not_approved():
    author = User(1, "author", ["workspace"], False)
    checker = User(1, "checker", [], True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=Status.SUBMITTED,
    )

    api = BusinessLogicLayer(data_access_layer=None)
    with pytest.raises(api.InvalidStateTransition):
        api.release_files(release_request, checker)


def test_provider_request_release_files(mock_old_api):
    old_api.create_release.return_value = "jobserver_id"
    author = User(1, "author", ["workspace"], False)
    checker = User(1, "checker", [], True)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        id="request_id",
        status=Status.SUBMITTED,
    )
    relpath = Path("test/file.txt")
    factories.write_request_file(release_request, "group", relpath, "test")
    factories.api.set_status(release_request, Status.APPROVED, checker)

    abspath = release_request.abspath("group" / relpath)

    api = BusinessLogicLayer(data_access_layer=Mock())
    api.release_files(release_request, checker)

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


def test_provider_get_requests_authored_by_user(api):
    user = User(1, "test", [], True)
    other_user = User(1, "other", [], True)
    factories.create_release_request("workspace", user, id="r1")
    factories.create_release_request("workspace", other_user, id="r2")

    assert [r.id for r in api.get_requests_authored_by_user(user)] == ["r1"]


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
def test_provider_get_outstanding_requests_for_review(output_checker, expected, api):
    user = User(1, "test", ["workspace"], output_checker)
    other_user = User(1, "other", ["workspace"], False)
    # request created by another user, status submitted
    factories.create_release_request(
        "workspace", other_user, id="r1", status=Status.SUBMITTED
    )

    # requests not visible to output checker
    # status submitted, but authored by output checker
    factories.create_release_request(
        "workspace", user, id="r2", status=Status.SUBMITTED
    )
    # requests authored by other users, status other than pending
    for i, status in enumerate(
        [
            Status.PENDING,
            Status.WITHDRAWN,
            Status.APPROVED,
            Status.REJECTED,
            Status.RELEASED,
        ]
    ):
        ws = f"workspace{i}"
        factories.create_release_request(ws, User(1, f"test_{i}", [ws]), status=status)

    assert set(r.id for r in api.get_outstanding_requests_for_review(user)) == set(
        expected
    )


def test_provider_get_current_request_for_user(api):
    workspace = factories.create_workspace("workspace")
    user = User(1, "testuser", ["workspace"], False)
    other_user = User(2, "otheruser", ["workspace"], False)

    assert api.get_current_request("workspace", user) is None

    factories.create_release_request(workspace, other_user)
    assert api.get_current_request("workspace", user) is None

    release_request = api.get_current_request("workspace", user, create=True)
    assert release_request.workspace == "workspace"
    assert release_request.author == user.username

    # reach around an simulate 2 active requests for same user
    api._create_release_request(author=user.username, workspace="workspace")

    with pytest.raises(Exception):
        api.get_current_request("workspace", user)


def test_provider_get_current_request_for_user_output_checker(api):
    """Output checker must have explict workspace permissions to create requests."""
    factories.create_workspace("workspace")
    user = User(1, "output_checker", [], True)

    with pytest.raises(api.RequestPermissionDenied):
        api.get_current_request("workspace", user, create=True)


@pytest.mark.parametrize(
    "current,future,valid_author,valid_checker",
    [
        (Status.PENDING, Status.SUBMITTED, True, False),
        (Status.PENDING, Status.WITHDRAWN, True, False),
        (Status.PENDING, Status.APPROVED, False, False),
        (Status.PENDING, Status.REJECTED, False, False),
        (Status.PENDING, Status.RELEASED, False, False),
        (Status.SUBMITTED, Status.APPROVED, False, True),
        (Status.SUBMITTED, Status.REJECTED, False, True),
        (Status.SUBMITTED, Status.WITHDRAWN, True, False),
        (Status.SUBMITTED, Status.PENDING, True, False),
        (Status.SUBMITTED, Status.RELEASED, False, False),
        (Status.APPROVED, Status.RELEASED, False, True),
        (Status.APPROVED, Status.REJECTED, False, True),
        (Status.APPROVED, Status.WITHDRAWN, True, False),
        (Status.REJECTED, Status.PENDING, False, False),
        (Status.REJECTED, Status.SUBMITTED, False, False),
        (Status.REJECTED, Status.APPROVED, False, True),
        (Status.REJECTED, Status.WITHDRAWN, False, False),
        (Status.RELEASED, Status.REJECTED, False, False),
        (Status.RELEASED, Status.PENDING, False, False),
        (Status.RELEASED, Status.SUBMITTED, False, False),
        (Status.RELEASED, Status.APPROVED, False, False),
        (Status.RELEASED, Status.REJECTED, False, False),
        (Status.RELEASED, Status.WITHDRAWN, False, False),
    ],
)
def test_set_status(current, future, valid_author, valid_checker, api):
    author = User(1, "author", ["workspace"], False)
    checker = User(2, "checker", [], True)
    release_request1 = factories.create_release_request(
        "workspace", user=author, status=current
    )
    release_request2 = factories.create_release_request(
        "workspace", user=author, status=current
    )

    if valid_author:
        api.set_status(release_request1, future, user=author)
        assert release_request1.status == future
    else:
        with pytest.raises((api.InvalidStateTransition, api.RequestPermissionDenied)):
            api.set_status(release_request1, future, user=author)

    if valid_checker:
        api.set_status(release_request2, future, user=checker)
        assert release_request2.status == future
    else:
        with pytest.raises((api.InvalidStateTransition, api.RequestPermissionDenied)):
            api.set_status(release_request2, future, user=checker)


def test_set_status_cannot_action_own_request(api):
    user = User(2, "checker", [], True)
    release_request1 = factories.create_release_request(
        "workspace", user=user, status=Status.SUBMITTED
    )

    with pytest.raises(api.RequestPermissionDenied):
        api.set_status(release_request1, Status.APPROVED, user=user)
    with pytest.raises(api.RequestPermissionDenied):
        api.set_status(release_request1, Status.REJECTED, user=user)

    release_request2 = factories.create_release_request(
        "workspace",
        user=user,
        status=Status.APPROVED,
    )

    with pytest.raises(api.RequestPermissionDenied):
        api.set_status(release_request2, Status.RELEASED, user=user)


def test_add_file_to_request_not_author(api):
    author = User(1, "author", ["workspace"], False)
    other = User(1, "other", ["workspace"], True)

    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )

    with pytest.raises(api.RequestPermissionDenied):
        api.add_file_to_request(release_request, path, other)


@pytest.mark.parametrize(
    "status,success",
    [
        (Status.PENDING, True),
        (Status.SUBMITTED, True),
        (Status.APPROVED, False),
        (Status.REJECTED, False),
        (Status.RELEASED, False),
        (Status.WITHDRAWN, False),
    ],
)
def test_add_file_to_request_states(status, success, api):
    author = User(1, "author", ["workspace"], False)

    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
        status=status,
    )

    if success:
        api.add_file_to_request(release_request, path, author)
        assert release_request.abspath("default" / path).exists()
    else:
        with pytest.raises(api.RequestPermissionDenied):
            api.add_file_to_request(release_request, path, author)


def test_request_release_invalid_state():
    factories.create_workspace("workspace")
    with pytest.raises(AttributeError):
        factories.create_release_request(
            "workspace",
            status="unknown",
        )


def test_request_release_abspath(api):
    path = UrlPath("foo/bar.txt")
    release_request = factories.create_release_request("id")
    factories.write_request_file(release_request, "default", path)

    with pytest.raises(api.FileNotFound):
        release_request.abspath("badgroup" / path)

    with pytest.raises(api.FileNotFound):
        release_request.abspath("default/does/not/exist")

    assert release_request.abspath("default" / path).exists()


def setup_empty_release_request():
    author = User(1, "author", ["workspace"], False)
    path = Path("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, path)
    release_request = factories.create_release_request(
        "workspace",
        user=author,
    )
    return release_request, path, author


def test_release_request_filegroups_with_no_files(api):
    release_request, _, _ = setup_empty_release_request()
    assert release_request.filegroups == {}


def test_release_request_filegroups_default_filegroup(api):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    api.add_file_to_request(release_request, path, author)
    assert len(release_request.filegroups) == 1
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert len(filegroup.files) == 1
    assert filegroup.files[0].relpath == path


def test_release_request_filegroups_named_filegroup(api):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    api.add_file_to_request(release_request, path, author, "test_group")
    assert len(release_request.filegroups) == 1
    filegroup = release_request.filegroups["test_group"]
    assert filegroup.name == "test_group"
    assert len(filegroup.files) == 1
    assert filegroup.files[0].relpath == path


def test_release_request_filegroups_multiple_filegroups(api):
    release_request, path, author = setup_empty_release_request()
    api.add_file_to_request(release_request, path, author, "test_group")
    assert len(release_request.filegroups) == 1

    workspace = api.get_workspace("workspace")
    path1 = Path("path/file1.txt")
    path2 = Path("path/file2.txt")
    factories.write_workspace_file(workspace, path1)
    factories.write_workspace_file(workspace, path2)
    api.add_file_to_request(release_request, path1, author, "test_group")
    api.add_file_to_request(release_request, path2, author, "test_group1")

    release_request = api.get_release_request(release_request.id)
    assert len(release_request.filegroups) == 2

    release_request_files = {
        filegroup.name: [file.relpath for file in filegroup.files]
        for filegroup in release_request.filegroups.values()
    }

    assert release_request_files == {
        "test_group": [Path("path/file.txt"), Path("path/file1.txt")],
        "test_group1": [Path("path/file2.txt")],
    }


def test_release_request_add_same_file(api):
    release_request, path, author = setup_empty_release_request()
    assert release_request.filegroups == {}
    api.add_file_to_request(release_request, path, author)
    assert len(release_request.filegroups) == 1
    assert len(release_request.filegroups["default"].files) == 1

    # Adding the same file again should not create a new RequestFile
    with pytest.raises(api.APIException):
        api.add_file_to_request(release_request, path, author)

    # We also can't add the same file to a different group
    with pytest.raises(api.APIException):
        api.add_file_to_request(release_request, path, author, "new_group")

    release_request = api.get_release_request(release_request.id)
    # No additional files or groups have been created
    assert len(release_request.filegroups) == 1
    assert len(release_request.filegroups["default"].files) == 1

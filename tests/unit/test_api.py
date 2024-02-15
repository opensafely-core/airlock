import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import old_api
from airlock.api import ProviderAPI, Status, Workspace, modified_time
from airlock.users import User
from tests import factories


pytestmark = pytest.mark.django_db


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

    api = ProviderAPI()

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

    api = ProviderAPI()
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
        status=Status.APPROVED,
    )
    relpath = Path("test/file.txt")
    factories.write_request_file(release_request, relpath, "test")
    abspath = release_request.abspath(relpath)

    api = ProviderAPI()
    api.release_files(release_request, checker)

    expected_json = {
        "files": [
            {
                "name": "test/file.txt",
                "url": "test/file.txt",
                "size": 4,
                "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "date": modified_time(abspath),
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


@pytest.mark.parametrize(
    "workspaces, output_checker, expected",
    [
        ([], False, []),
        (["allowed"], False, ["r1"]),
        (
            [],
            True,
            [
                "r1",
                "r2",
                "r3",
            ],
        ),
        (["allowed", "notexist"], False, ["r1"]),
        (["notexist", "notexist"], False, []),
        (["no-request-dir", "notexist"], False, []),
    ],
)
def test_provider_get_requests_for_user(workspaces, output_checker, expected, api):
    user = User(1, "test", workspaces, output_checker)
    other_user = User(1, "other", [], False)
    factories.create_release_request("allowed", user, id="r1")
    factories.create_release_request("allowed", other_user, id="r2")
    factories.create_release_request("not-allowed", user, id="r3")
    factories.create_workspace("no-request-dir")

    assert set(r.id for r in api.get_requests_for_user(user)) == set(expected)


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
        assert release_request.abspath(path).exists()
    else:
        with pytest.raises(api.RequestPermissionDenied):
            api.add_file_to_request(release_request, path, author)

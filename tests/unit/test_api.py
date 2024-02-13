import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import old_api
from airlock.api import ProviderAPI, Workspace, modified_time
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


def test_provider_request_release_files(mock_old_api):
    old_api.create_release.return_value = "jobserver_id"
    user = User(1, "testuser", [], True)
    release_request = factories.create_release_request(
        "workspace", user, id="request_id"
    )
    relpath = Path("test/file.txt")
    factories.write_request_file(release_request, relpath, "test")
    abspath = release_request.abspath(relpath)

    api = ProviderAPI()
    api.release_files(release_request, user)

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
        "workspace", json.dumps(expected_json), "testuser"
    )
    old_api.upload_file.assert_called_once_with(
        "jobserver_id", relpath, abspath, "testuser"
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
    user = User(1, "testuser", [], True)
    other_user = User(2, "otheruser", [], True)

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

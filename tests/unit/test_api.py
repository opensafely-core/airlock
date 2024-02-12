import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.conf import settings

import old_api
from airlock.api import FileProvider, ProviderAPI, Workspace, modified_time
from airlock.users import User
from tests import factories


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
        "workspace", user, request_id="request_id"
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
        (["allowed"], False, ["r1-test"]),
        (
            [],
            True,
            [
                "r1-test",
                "r3-test",
                "r2-other",
            ],
        ),
        (["allowed", "notexist"], False, ["r1-test"]),
        (["notexist", "notexist"], False, []),
        (["no-request-dir", "notexist"], False, []),
    ],
)
def test_fileprovider_get_requests_for_user(workspaces, output_checker, expected):
    user = User(1, "test", workspaces, output_checker)
    other_user = User(1, "other", [], False)
    factories.create_release_request("allowed", user, "r1-test")
    factories.create_release_request("allowed", other_user, "r2-other")
    factories.create_release_request("not-allowed", user, "r3-test")
    factories.create_workspace("no-request-dir")

    api = FileProvider()

    expected_requests = set(api.get_release_request(rid) for rid in expected)

    assert set(api.get_requests_for_user(user)) == expected_requests


def test_fileprovider_get_current_request_for_user():
    workspace = factories.create_workspace("workspace")
    user = User(1, "testuser", [], True)
    other_user = User(2, "otheruser", [], True)

    api = FileProvider()

    assert api.get_current_request("workspace", user) is None

    factories.create_release_request(workspace, other_user)
    assert api.get_current_request("workspace", user) is None

    request = api.get_current_request("workspace", user, create=True)
    assert request.workspace == "workspace"
    assert request.id.endswith("workspace-test-testuser")

    # reach around an simulate 2 active requests for same user
    (settings.REQUEST_DIR / "workspace/other-request-testuser").mkdir(parents=True)

    request = api.get_current_request("workspace", user)
    assert request.workspace == "workspace"
    assert request.id == "other-request-testuser"

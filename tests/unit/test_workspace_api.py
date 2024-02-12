import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.conf import settings

import old_api
from airlock.users import User
from airlock.workspace_api import (
    Container,
    PathItem,
    ReleaseRequest,
    Workspace,
    get_requests_for_user,
    get_workspaces_for_user,
)
from tests import factories


@pytest.mark.parametrize(
    "user_workspaces,output_checker,expected",
    [
        ([], False, []),
        (["allowed"], False, [Workspace("allowed")]),
        ([], True, [Workspace("allowed"), Workspace("not-allowed")]),
        (["allowed", "notexist"], False, [Workspace("allowed")]),
    ],
)
def test_get_workspaces_for_user(user_workspaces, output_checker, expected):
    factories.create_workspace("allowed")
    factories.create_workspace("not-allowed")

    user = User(1, "test", user_workspaces, output_checker)
    assert set(get_workspaces_for_user(user)) == set(expected)


@pytest.mark.parametrize(
    "workspaces, output_checker, expected",
    [
        ([], False, []),
        (["allowed"], False, [("allowed", "r1-test")]),
        (
            [],
            True,
            [
                ("allowed", "r1-test"),
                ("not-allowed", "r3-test"),
                ("allowed", "r2-other"),
            ],
        ),
        (["allowed", "notexist"], False, [("allowed", "r1-test")]),
        (["notexist", "notexist"], False, []),
        (["no-request-dir", "notexist"], False, []),
    ],
)
def test_get_requests_for_user(workspaces, output_checker, expected):
    user = User(1, "test", workspaces, output_checker)
    other_user = User(1, "other", [], False)
    factories.create_request("allowed", user, "r1-test")
    factories.create_request("allowed", other_user, "r2-other")
    factories.create_request("not-allowed", user, "r3-test")
    factories.create_workspace("no-request-dir")
    expected_requests = set(ReleaseRequest(Workspace(w), rid) for (w, rid) in expected)
    assert set(get_requests_for_user(user)) == expected_requests


def test_workspace_container():
    workspace = Workspace("test-workspace")

    assert not workspace.exists()
    assert workspace.root() == settings.WORKSPACE_DIR / "test-workspace"
    assert workspace.get_url("foo/bar").endswith("foo/bar")


def test_workspace_get_current_request_for_user():
    workspace = factories.create_workspace("workspace")
    user = User(1, "testuser", [], True)
    other_user = User(2, "otheruser", [], True)

    assert workspace.get_current_request(user) is None

    factories.create_request(workspace, other_user)
    assert workspace.get_current_request(user) is None

    release_request = workspace.get_current_request(user, create=True)
    assert release_request.workspace.name == "workspace"
    assert release_request.request_id.endswith("workspace-test-testuser")

    # reach around an simulate 2 active requests for same user
    (settings.REQUEST_DIR / "workspace/other-request-testuser").mkdir(parents=True)

    release_request = workspace.get_current_request(user)
    assert release_request.workspace.name == "workspace"
    assert release_request.request_id == "other-request-testuser"


def test_workspace_create_new_request():
    user = User(1, "testuser", [], True)
    release_request = factories.create_request("workspace", user)

    assert release_request.workspace.name == "workspace"
    assert release_request.request_id.endswith("testuser")


def test_request_container():
    workspace = factories.create_workspace("test-workspace")

    output_request = ReleaseRequest(workspace, "test-request")

    assert not output_request.exists()
    output_request.ensure_request_dir()
    assert output_request.exists()

    assert output_request.root() == settings.REQUEST_DIR / Path(
        "test-workspace/test-request"
    )
    assert output_request.get_url("foo/bar").endswith("foo/bar")


@pytest.fixture
def mock_old_api(monkeypatch):
    monkeypatch.setattr(
        old_api, "create_release", MagicMock(autospec=old_api.create_release)
    )
    monkeypatch.setattr(old_api, "upload_file", MagicMock(autospec=old_api.upload_file))


def test_request_release_files(mock_old_api):
    old_api.create_release.return_value = "jobserver_id"
    user = User(1, "testuser", [], True)
    release_request = factories.create_request(
        "workspace", user, request_id="request_id"
    )
    factories.write_request_file(release_request, "test/file.txt", "test")

    release_request.release_files(user)

    item = release_request.get_path("test/file.txt")
    expected_json = {
        "files": [
            {
                "name": "test/file.txt",
                "url": "test/file.txt",
                "size": 4,
                "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
                "date": item.modified_date(),
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
        "jobserver_id", item.relpath, item._absolute_path(), "testuser"
    )


@dataclasses.dataclass(frozen=True)
class DummyContainer(Container):
    path: Path

    def root(self):
        return self.path

    def get_url(self, relpath):
        return f"/test/{relpath}"


@pytest.fixture(scope="module")
def tmp_files(tmp_path_factory):
    tmp_files = tmp_path_factory.mktemp(__name__)
    (tmp_files / "empty_dir").mkdir()
    (tmp_files / "some_dir").mkdir()
    (tmp_files / "some_dir/file_a.txt").write_text("file_a")
    (tmp_files / "some_dir/file_b.txt").write_text("file_b")
    return tmp_files


@pytest.fixture
def container(tmp_files):
    return DummyContainer(tmp_files)


@pytest.mark.parametrize(
    "path,exists",
    [
        ("some_dir", True),
        ("some_dir/file_a.txt", True),
        ("not_a_dir", False),
        ("empty_dir/not_a_file.txt", False),
    ],
)
def test_exists(container, path, exists):
    assert PathItem(container, path).exists() == exists


@pytest.mark.parametrize(
    "path,is_directory",
    [
        ("some_dir", True),
        ("some_dir/file_a.txt", False),
    ],
)
def test_is_directory(container, path, is_directory):
    assert PathItem(container, path).is_directory() == is_directory


def test_name(container):
    assert PathItem(container, "some_dir/file_a.txt").name() == "file_a.txt"


@pytest.mark.parametrize(
    "path,url",
    [
        ("some_dir", "/some_dir/"),
        ("some_dir/file_a.txt", "/some_dir/file_a.txt"),
    ],
)
def test_url(container, path, url):
    assert PathItem(container, path).url().endswith(url)


@pytest.mark.parametrize(
    "path,parent_path",
    [
        ("", None),
        ("some_dir", ""),
        ("some_dir/file_a.txt", "some_dir"),
    ],
)
def test_parent(container, path, parent_path):
    parent = PathItem(container, path).parent()
    if parent_path is None:
        assert parent is None
    else:
        assert parent == PathItem(container, parent_path)


@pytest.mark.parametrize(
    "path,child_paths",
    [
        (
            "",
            ["some_dir", "empty_dir"],
        ),
        (
            "empty_dir",
            [],
        ),
        (
            "some_dir",
            ["some_dir/file_a.txt", "some_dir/file_b.txt"],
        ),
    ],
)
def test_children(container, path, child_paths):
    children = PathItem(container, path).children()
    assert set(children) == {PathItem(container, Path(child)) for child in child_paths}


@pytest.mark.parametrize(
    "path,sibling_paths",
    [
        ("", []),
        ("empty_dir", ["empty_dir", "some_dir"]),
    ],
)
def test_siblings(container, path, sibling_paths):
    siblings = PathItem(container, path).siblings()
    assert set(siblings) == {PathItem(container, sibling) for sibling in sibling_paths}


@pytest.mark.parametrize(
    "path,contents",
    [
        ("some_dir/file_a.txt", "file_a"),
        ("some_dir/file_b.txt", "file_b"),
    ],
)
def test_contents(container, path, contents):
    assert PathItem(container, path).contents() == contents


@pytest.mark.parametrize(
    "path",
    [
        "../../relative_path",
        "/tmp/absolute/path",
    ],
)
def test_from_relative_path_rejects_path_escape(container, path):
    with pytest.raises(ValueError, match="is not in the subpath"):
        PathItem(container, path)


def test_breadcrumbs(container):
    assert PathItem(container, "foo/bar/baz").breadcrumbs() == [
        PathItem(container, "foo"),
        PathItem(container, "foo/bar"),
        PathItem(container, "foo/bar/baz"),
    ]

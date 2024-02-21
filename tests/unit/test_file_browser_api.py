import dataclasses
from pathlib import Path

import pytest

from airlock.file_browser_api import ROOT_PATH, PathItem, UrlPath


@dataclasses.dataclass(frozen=True)
class DummyContainer:
    path: Path
    selected_path: UrlPath = ROOT_PATH

    def root(self):
        return self.path

    def get_url_for_path(self, relpath):
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
        (
            "some_dir/file_a.txt",
            [],
        ),
    ],
)
def test_children(container, path, child_paths):
    children = PathItem(container, path).children()
    assert set(children) == {
        PathItem(container, UrlPath(child)) for child in child_paths
    }


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


def test_selection_logic(tmp_files):
    selected_path = UrlPath("some_dir/file_a.txt")
    container = DummyContainer(tmp_files, selected_path=selected_path)

    selected_item = PathItem(container, selected_path)
    assert selected_item.is_selected()
    assert selected_item.is_open()

    parent_item = PathItem(container, "some_dir")
    assert not parent_item.is_selected()
    assert parent_item.is_on_selected_path()
    assert parent_item.is_open()

    other_item = PathItem(container, "other_dir")
    assert not other_item.is_selected()
    assert not other_item.is_on_selected_path()
    assert not other_item.is_open()

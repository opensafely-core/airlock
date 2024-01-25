import pytest

from airlock.workspace_api import PathItem


@pytest.fixture(scope="module")
def tmp_files(tmp_path_factory):
    tmp_files = tmp_path_factory.mktemp(__name__)
    (tmp_files / "empty_dir").mkdir()
    (tmp_files / "some_dir").mkdir()
    (tmp_files / "some_dir/file_a.txt").write_text("file_a")
    (tmp_files / "some_dir/file_b.txt").write_text("file_b")
    return tmp_files


@pytest.fixture
def workspace_files(tmp_files, settings):
    settings.WORKSPACE_DIR = tmp_files


@pytest.mark.parametrize(
    "path,exists",
    [
        ("some_dir", True),
        ("some_dir/file_a.txt", True),
        ("not_a_dir", False),
        ("empty_dir/not_a_file.txt", False),
    ],
)
def test_exists(workspace_files, path, exists):
    assert PathItem.from_relative_path(path).exists() == exists


@pytest.mark.parametrize(
    "path,is_directory",
    [
        ("some_dir", True),
        ("some_dir/file_a.txt", False),
    ],
)
def test_is_directory(workspace_files, path, is_directory):
    assert PathItem.from_relative_path(path).is_directory() == is_directory


def test_name(workspace_files):
    assert PathItem.from_relative_path("some_dir/file_a.txt").name() == "file_a.txt"


@pytest.mark.parametrize(
    "path,url",
    [
        ("some_dir", "/some_dir/"),
        ("some_dir/file_a.txt", "/some_dir/file_a.txt"),
    ],
)
def test_url(workspace_files, path, url):
    assert PathItem.from_relative_path(path).url().endswith(url)


@pytest.mark.parametrize(
    "path,parent_path",
    [
        ("", None),
        ("some_dir", ""),
        ("some_dir/file_a.txt", "some_dir"),
    ],
)
def test_parent(workspace_files, path, parent_path):
    parent = PathItem.from_relative_path(path).parent()
    if parent_path is None:
        assert parent is None
    else:
        assert parent == PathItem.from_relative_path(parent_path)


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
def test_children(workspace_files, path, child_paths):
    children = PathItem.from_relative_path(path).children()
    assert set(children) == {
        PathItem.from_relative_path(child) for child in child_paths
    }


@pytest.mark.parametrize(
    "path,sibling_paths",
    [
        ("", []),
        ("empty_dir", ["empty_dir", "some_dir"]),
    ],
)
def test_siblings(workspace_files, path, sibling_paths):
    siblings = PathItem.from_relative_path(path).siblings()
    assert set(siblings) == {
        PathItem.from_relative_path(sibling) for sibling in sibling_paths
    }


@pytest.mark.parametrize(
    "path,contents",
    [
        ("some_dir/file_a.txt", "file_a"),
        ("some_dir/file_b.txt", "file_b"),
    ],
)
def test_contents(workspace_files, path, contents):
    assert PathItem.from_relative_path(path).contents() == contents


@pytest.mark.parametrize(
    "path",
    [
        "../../relative_path",
        "/tmp/absolute/path",
    ],
)
def test_from_relative_path_rejects_path_escape(path):
    with pytest.raises(ValueError, match="is not in the subpath"):
        PathItem.from_relative_path(path)

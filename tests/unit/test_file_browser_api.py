import textwrap

import pytest
from django.template.loader import render_to_string

from airlock.file_browser_api import (
    PathType,
    UrlPath,
    get_request_tree,
    get_workspace_tree,
)
from tests import factories


@pytest.fixture
def workspace():
    w = factories.create_workspace("workspace")
    (w.root() / "empty_dir").mkdir()
    factories.write_workspace_file(w, "some_dir/file_a.txt", "file_a")
    factories.write_workspace_file(w, "some_dir/file_b.txt", "file_b")
    factories.write_workspace_file(w, "some_dir/file_c.txt", "file_c")
    return w


@pytest.fixture
def release_request(workspace):
    rr = factories.create_release_request(workspace)
    factories.write_request_file(rr, "group1", "some_dir/file_a.txt")
    factories.write_request_file(rr, "group1", "some_dir/file_c.txt")
    factories.write_request_file(rr, "group2", "some_dir/file_b.txt")
    return rr


def test_get_workspace_tree_general(workspace):
    """Tests an entire tree for the basics."""
    selected_path = UrlPath("some_dir/file_a.txt")
    tree = get_workspace_tree(workspace, selected_path)

    # simple way to express the entire tree structure, including selected
    expected = textwrap.dedent(
        """
        workspace*
          empty_dir
          some_dir*
            file_a.txt**
            file_b.txt
            file_c.txt
        """
    )

    assert str(tree).strip() == expected.strip()

    # types
    assert tree.type == PathType.WORKSPACE
    assert tree.get_path("empty_dir").type == PathType.DIR
    assert tree.get_path("empty_dir").children == []
    assert tree.get_path("some_dir").type == PathType.DIR
    assert tree.get_path("some_dir/file_a.txt").type == PathType.FILE
    assert tree.get_path("some_dir/file_b.txt").type == PathType.FILE

    # selected
    assert tree.get_path("some_dir/file_a.txt") == tree.get_selected()

    # errors
    with pytest.raises(tree.PathNotFound):
        tree.get_path("some_dir/notexist.txt")
    with pytest.raises(tree.PathNotFound):
        tree.get_path("no_dir")

    # check that the tree works with the recursive template
    render_to_string("file_browser/tree.html", {"path": tree})


@pytest.mark.django_db
def test_get_request_tree_general(release_request):
    selected_path = UrlPath("group1/some_dir/file_a.txt")
    tree = get_request_tree(release_request, selected_path)

    # simple way to express the entire tree structure, including selected
    expected = textwrap.dedent(
        f"""
        {release_request.id}*
          group1*
            some_dir*
              file_a.txt**
              file_c.txt
          group2
            some_dir
              file_b.txt
        """
    )

    assert str(tree).strip() == expected.strip()

    # types
    assert tree.type == PathType.REQUEST
    assert tree.get_path("group1").type == PathType.FILEGROUP
    assert tree.get_path("group1/some_dir").type == PathType.DIR
    assert tree.get_path("group1/some_dir/file_a.txt").type == PathType.FILE
    assert tree.get_path("group2").type == PathType.FILEGROUP
    assert tree.get_path("group2/some_dir").type == PathType.DIR
    assert tree.get_path("group2/some_dir/file_b.txt").type == PathType.FILE

    # selected
    assert tree.get_path("group1/some_dir/file_a.txt") == tree.get_selected()

    # check that the tree works with the recursive template
    render_to_string("file_browser/tree.html", {"path": tree})


@pytest.mark.parametrize(
    "path,exists",
    [
        ("some_dir", True),
        ("some_dir/file_a.txt", True),
        ("not_a_dir", False),
        ("empty_dir/not_a_file.txt", False),
    ],
)
def test_workspace_tree_get_path(workspace, path, exists):
    tree = get_workspace_tree(workspace)

    if exists:
        tree.get_path(path)
    else:
        with pytest.raises(tree.PathNotFound):
            tree.get_path(path)


@pytest.mark.parametrize(
    "path,exists",
    [
        ("group1", True),
        ("group1/some_dir", True),
        ("group1/some_dir/file_a.txt", True),
        ("not_a_group", False),
        ("group1/not_a_dir", False),
        ("group1/some_dir/not_a_file.txt", False),
    ],
)
@pytest.mark.django_db
def test_request_tree_get_path(release_request, path, exists):
    tree = get_request_tree(release_request)

    if exists:
        tree.get_path(path)
    else:
        with pytest.raises(tree.PathNotFound):
            tree.get_path(path)


@pytest.mark.parametrize(
    "path,url",
    [
        ("some_dir", "/some_dir/"),
        ("some_dir/file_a.txt", "/some_dir/file_a.txt"),
    ],
)
def test_workspace_tree_urls(workspace, path, url):
    tree = get_workspace_tree(workspace)
    assert tree.get_path(path).url().endswith(url)


def test_workspace_tree_content_urls(workspace):
    tree = get_workspace_tree(workspace)
    assert (
        tree.get_path("some_dir/file_a.txt")
        .contents_url()
        .endswith("some_dir/file_a.txt")
    )

    with pytest.raises(Exception):
        assert tree.get_path("some_dir").contents_url()


@pytest.mark.parametrize(
    "path,url",
    [
        ("group1/some_dir", "group1/some_dir/"),
        ("group1/some_dir/file_a.txt", "group1/some_dir/file_a.txt"),
    ],
)
@pytest.mark.django_db
def test_request_tree_urls(release_request, path, url):
    tree = get_request_tree(release_request)
    assert tree.get_path(path).url().endswith(url)


def test_workspace_tree_breadcrumbs(workspace):
    tree = get_workspace_tree(workspace)
    path = tree.get_path("some_dir/file_a.txt")
    assert [c.name() for c in path.breadcrumbs()] == [
        "workspace",
        "some_dir",
        "file_a.txt",
    ]


@pytest.mark.django_db
def test_request_tree_breadcrumbs(release_request):
    tree = get_request_tree(release_request)
    path = tree.get_path("group1/some_dir/file_a.txt")
    assert [c.name() for c in path.breadcrumbs()] == [
        release_request.id,
        "group1",
        "some_dir",
        "file_a.txt",
    ]


def test_workspace_tree_selection_root(workspace):
    tree = get_workspace_tree(workspace)
    assert tree.get_selected() == tree


def test_workspace_tree_selection_path_file(workspace):
    selected_path = UrlPath("some_dir/file_a.txt")
    tree = get_workspace_tree(workspace, selected_path)

    selected_item = tree.get_path(selected_path)
    assert selected_item.selected
    assert not selected_item.expanded

    parent_item = tree.get_path("some_dir")
    assert not parent_item.selected
    assert parent_item.expanded

    other_item = tree.get_path("some_dir/file_b.txt")
    assert not other_item.selected
    assert not other_item.expanded


def test_workspace_tree_selection_path_dir(workspace):
    selected_path = UrlPath("some_dir")
    tree = get_workspace_tree(workspace, selected_path)

    selected_item = tree.get_path(selected_path)
    assert selected_item.selected
    # selected dir *is* expanded
    assert selected_item.expanded


def test_workspace_tree_selection_bad_path(workspace):
    selected_path = UrlPath("bad/path")
    tree = get_workspace_tree(workspace, selected_path)
    with pytest.raises(tree.PathNotFound):
        tree.get_selected()


@pytest.mark.django_db
def test_request_tree_selection_root(release_request):
    # selected root by default
    tree = get_request_tree(release_request)
    assert tree.get_selected() == tree


@pytest.mark.django_db
def test_request_tree_selection_path(release_request):
    selected_path = UrlPath("group1/some_dir/file_a.txt")
    tree = get_request_tree(release_request, selected_path)

    selected_item = tree.get_path(selected_path)
    assert selected_item.selected
    assert not selected_item.expanded

    parent_item = tree.get_path("group1/some_dir")
    assert not parent_item.selected
    assert parent_item.expanded

    other_item = tree.get_path("group2/some_dir/file_b.txt")
    assert not other_item.selected
    assert not other_item.expanded


@pytest.mark.django_db
def test_request_tree_selection_not_path(release_request):
    selected_path = UrlPath("bad/path")
    tree = get_request_tree(release_request, selected_path)
    with pytest.raises(tree.PathNotFound):
        tree.get_selected()


def test_workspace_tree_siblings(workspace):
    tree = get_workspace_tree(workspace)

    assert tree.siblings() == []
    assert {s.name() for s in tree.get_path("some_dir").siblings()} == {
        "empty_dir",
        "some_dir",
    }
    assert {s.name() for s in tree.get_path("some_dir/file_a.txt").siblings()} == {
        "file_a.txt",
        "file_b.txt",
        "file_c.txt",
    }


@pytest.mark.django_db
def test_request_tree_siblings(release_request):
    tree = get_request_tree(release_request)

    assert tree.siblings() == []
    assert {s.name() for s in tree.get_path("group1").siblings()} == {
        "group1",
        "group2",
    }
    assert {s.name() for s in tree.get_path("group1/some_dir").siblings()} == {
        "some_dir"
    }


def test_workspace_tree_contents(workspace):
    (workspace.root() / "dir.ext").mkdir()
    tree = get_workspace_tree(workspace)

    with pytest.raises(Exception):
        tree.contents()

    with pytest.raises(Exception):
        tree.get_path("some_dir").contents()

    tree.get_path("dir.ext").contents() == "dir.ext is not a file"

    assert tree.get_path("some_dir/file_a.txt").contents() == "file_a"


@pytest.mark.django_db
def test_request_tree_contents(release_request):
    tree = get_request_tree(release_request)

    with pytest.raises(Exception):
        tree.contents()

    with pytest.raises(Exception):
        tree.get_path("group1").contents()

    with pytest.raises(Exception):
        tree.get_path("group1/some_dir").contents()

    assert tree.get_path("group1/some_dir/file_a.txt").contents() == "file_a"

import textwrap

import pytest
from django.template.loader import render_to_string

from airlock.file_browser_api import (
    PathType,
    UrlPath,
    add_request_pathitem_filesizes,
    add_workspace_pathitem_filesizes,
    filter_files,
    get_request_tree,
    get_workspace_tree,
)
from tests import factories
from tests.conftest import get_trace


@pytest.fixture
def workspace():
    w = factories.create_workspace("workspace")
    (w.root() / "empty_dir").mkdir()
    factories.write_workspace_file(w, "some_dir/file_a.txt", "file_a")
    factories.write_workspace_file(w, "some_dir/file_b.txt", "file_b")
    factories.write_workspace_file(w, "some_dir/file_c.txt", "file_c")
    # A file with an extension that is not allowed on L4 still
    # appears in the workspace tree
    factories.write_workspace_file(w, "some_dir/file_a.foo", "bad file")
    factories.write_workspace_file(w, "some_dir/.file.txt", "bad file")
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
            .file.txt
            file_a.foo
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

    # valid
    assert tree.get_path("some_dir/file_a.txt").is_valid()
    assert tree.get_path("some_dir/file_b.txt").is_valid()
    assert not tree.get_path("some_dir/file_a.foo").is_valid()
    assert not tree.get_path("some_dir/.file.txt").is_valid()

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


def test_get_workspace_tree_selected_only_file(workspace):
    selected_path = UrlPath("some_dir/file_a.txt")
    tree = get_workspace_tree(workspace, selected_path, selected_only=True)

    # only the selected path should be in the tree
    expected = textwrap.dedent(
        """
        workspace*
          some_dir*
            file_a.txt**
        """
    )

    assert str(tree).strip() == expected.strip()


def test_get_workspace_tree_selected_only_dir(workspace):
    selected_path = UrlPath("some_dir")
    # needed for coverage of is_file() branch
    (workspace.root() / "some_dir/subdir").mkdir()
    tree = get_workspace_tree(workspace, selected_path, selected_only=True)

    # only the selected path should be in the tree
    expected = textwrap.dedent(
        """
        workspace*
          some_dir***
            subdir
            .file.txt
            file_a.foo
            file_a.txt
            file_b.txt
            file_c.txt
        """
    )

    assert str(tree).strip() == expected.strip()


@pytest.mark.django_db
def test_get_request_tree_selected_only_file(release_request):
    selected_path = UrlPath("group1/some_dir/file_a.txt")
    tree = get_request_tree(release_request, selected_path, selected_only=True)

    # only the selected path should be in the tree, and all groups
    expected = textwrap.dedent(
        f"""
        {release_request.id}*
          group1*
            some_dir*
              file_a.txt**
              file_c.txt
          group2
        """
    )

    assert str(tree).strip() == expected.strip()


@pytest.mark.django_db
def test_get_request_tree_selected_only_group(release_request):
    selected_path = UrlPath("group1")
    tree = get_request_tree(release_request, selected_path, selected_only=True)

    # only the selected path should be in the tree, and all groups
    expected = textwrap.dedent(
        f"""
        {release_request.id}*
          group1***
            some_dir*
              file_a.txt
              file_c.txt
          group2
        """
    )

    assert str(tree).strip() == expected.strip()


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


@pytest.mark.django_db
def test_request_tree_get_path_filesize(release_request):
    tree = get_request_tree(release_request)

    mypath = tree.get_path("group1/some_dir")
    add_request_pathitem_filesizes(release_request, mypath)
    assert tree.get_path("group1/some_dir/file_a.txt").size == 6


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


def test_workspace_tree_get_path_filesize(workspace):
    tree = get_workspace_tree(workspace)
    path = tree.get_path("some_dir")
    add_workspace_pathitem_filesizes(workspace, path)
    assert tree.get_path("some_dir/file_a.txt").size == 6


def test_workspace_tree_content_urls(workspace):
    tree = get_workspace_tree(workspace)
    url = tree.get_path("some_dir/file_a.txt").contents_url()
    assert "some_dir/file_a.txt" in url
    assert "cache_id=" in url

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


@pytest.mark.django_db
def test_request_tree_download_url(release_request):
    tree = get_request_tree(release_request)
    assert (
        tree.get_path("group1/some_dir/file_a.txt")
        .download_url()
        .endswith("group1/some_dir/file_a.txt?download")
    )

    with pytest.raises(Exception):
        assert tree.get_path("some_dir").download_url()


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
        ".file.txt",
        "file_a.foo",
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


def test_filter_files():
    selected = UrlPath("foo/bar")
    files = [
        UrlPath("foo/bar"),
        UrlPath("foo/bar/child1"),
        UrlPath("foo/bar/child2"),
        UrlPath("foo/bar/child1/grandchild"),
        UrlPath("foo/other"),
    ]

    assert list(filter_files(selected, files)) == [
        UrlPath("foo/bar"),
        UrlPath("foo/bar/child1"),
        UrlPath("foo/bar/child2"),
    ]


def test_get_workspace_tree_tracing(workspace):
    selected_path = UrlPath("some_dir/file_a.txt")
    get_workspace_tree(workspace, selected_path)
    traces = get_trace()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.attributes == {"workspace": workspace.name}


@pytest.mark.django_db
def test_get_request_tree_tracing(release_request):
    get_request_tree(release_request)
    traces = get_trace()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.attributes == {"release_request": release_request.id}

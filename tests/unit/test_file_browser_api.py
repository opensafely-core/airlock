import textwrap

import pytest
from django.template.loader import render_to_string

from airlock.enums import (
    PathType,
    RequestFileDecision,
    RequestFileVote,
    RequestStatus,
    WorkspaceFileStatus,
)
from airlock.file_browser_api import (
    get_code_tree,
    get_request_tree,
    get_workspace_tree,
)
from airlock.types import FilePath
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
    factories.add_request_file(rr, "group1", "some_dir/file_a.txt")
    factories.add_request_file(rr, "group1", "some_dir/file_c.txt")
    factories.add_request_file(rr, "group2", "some_dir/file_b.txt")
    return factories.refresh_release_request(rr)


def test_get_workspace_tree_general(release_request):
    """Tests an entire tree for the basics."""
    # refresh workspace
    workspace = factories.create_workspace("workspace")

    # add new file not in request
    factories.write_workspace_file(workspace, "some_dir/file_d.txt", "file_d")
    # modified file in request
    factories.write_workspace_file(workspace, "some_dir/file_c.txt", "changed")

    selected_path = FilePath("some_dir/file_a.txt")
    tree = get_workspace_tree(workspace, selected_path)

    # simple way to express the entire tree structure, including selected
    expected = textwrap.dedent(
        """
        workspace*
          metadata
            manifest.json
          empty_dir
          some_dir*
            .file.txt
            file_a.foo
            file_a.txt**
            file_b.txt
            file_c.txt
            file_d.txt
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

    # state
    assert (
        tree.get_path("some_dir/file_a.txt").workspace_status
        == WorkspaceFileStatus.UNDER_REVIEW
    )
    assert (
        tree.get_path("some_dir/file_b.txt").workspace_status
        == WorkspaceFileStatus.UNDER_REVIEW
    )
    assert (
        tree.get_path("some_dir/file_c.txt").workspace_status
        == WorkspaceFileStatus.CONTENT_UPDATED
    )
    assert (
        tree.get_path("some_dir/file_d.txt").workspace_status
        == WorkspaceFileStatus.UNRELEASED
    )

    # html classes
    assert (
        "workspace_under_review" in tree.get_path("some_dir/file_a.txt").html_classes()
    )
    assert "workspace_updated" in tree.get_path("some_dir/file_c.txt").html_classes()
    assert "workspace_unreleased" in tree.get_path("some_dir/file_d.txt").html_classes()

    assert tree.get_path("some_dir/file_a.txt").request_status is None

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
    selected_path = FilePath("group1/some_dir/file_a.txt")
    tree = get_request_tree(
        release_request, factories.create_airlock_user(), selected_path
    )

    # simple way to express the entire tree structure, including selected
    expected = textwrap.dedent(
        f"""
        {release_request.id}*
          group1*
            some_dir*
              file_a.txt**
              file_c.txt
          group2*
            some_dir*
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


def test_get_request_tree_status(bll):
    author = factories.create_airlock_user(
        username="author", output_checker=True, workspaces=["workspace"]
    )
    checker1 = factories.create_airlock_user(
        username="checker1", workspaces=[], output_checker=True
    )
    checker2 = factories.create_airlock_user(
        username="checker2", workspaces=[], output_checker=True
    )

    path = FilePath("some_dir/file_a.txt")
    release_request = factories.create_request_at_status(
        "workspace",
        RequestStatus.SUBMITTED,
        author=author,
        files=[factories.request_file(path=path)],
    )

    def assert_status(user, decision, vote):
        nonlocal release_request
        release_request = factories.refresh_release_request(release_request)
        tree = get_request_tree(release_request, user)
        item = tree.get_path("group" / path)
        assert item.request_status.decision == decision
        assert item.request_status.vote == vote

    # SUBMITTED, so reviews are blinded, checkers can only see your own votes,
    # author can see nothing.
    assert_status(author, RequestFileDecision.INCOMPLETE, None)
    assert_status(checker1, RequestFileDecision.INCOMPLETE, None)
    assert_status(checker2, RequestFileDecision.INCOMPLETE, None)

    bll.approve_file(
        release_request,
        release_request.get_request_file_from_output_path(path),
        checker1,
    )

    assert_status(author, RequestFileDecision.INCOMPLETE, None)
    assert_status(checker1, RequestFileDecision.INCOMPLETE, RequestFileVote.APPROVED)
    assert_status(checker2, RequestFileDecision.INCOMPLETE, None)

    # move to PARTIALLY_REVIEWED, but still blinded
    factories.submit_independent_review(release_request, checker1)

    assert_status(author, RequestFileDecision.INCOMPLETE, None)
    assert_status(checker1, RequestFileDecision.INCOMPLETE, RequestFileVote.APPROVED)
    assert_status(checker2, RequestFileDecision.INCOMPLETE, None)
    assert release_request.status == RequestStatus.PARTIALLY_REVIEWED

    bll.approve_file(
        release_request,
        release_request.get_request_file_from_output_path(path),
        checker2,
    )

    assert_status(author, RequestFileDecision.INCOMPLETE, None)
    assert_status(checker1, RequestFileDecision.INCOMPLETE, RequestFileVote.APPROVED)
    assert_status(checker2, RequestFileDecision.INCOMPLETE, RequestFileVote.APPROVED)

    # move to REVIEWED, now unblinded, but author still cannot see anything
    factories.submit_independent_review(release_request, checker2)

    assert_status(author, RequestFileDecision.INCOMPLETE, None)
    assert_status(checker1, RequestFileDecision.APPROVED, RequestFileVote.APPROVED)
    assert_status(checker2, RequestFileDecision.APPROVED, RequestFileVote.APPROVED)
    assert release_request.status == RequestStatus.REVIEWED

    # move to RETURNED, votes are all public now
    bll.return_request(release_request, checker1)
    assert_status(author, RequestFileDecision.APPROVED, None)
    assert_status(checker1, RequestFileDecision.APPROVED, RequestFileVote.APPROVED)
    assert_status(checker2, RequestFileDecision.APPROVED, RequestFileVote.APPROVED)


def test_get_workspace_tree_selected_only_file(workspace):
    selected_path = FilePath("some_dir/file_a.txt")
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


def test_get_workspace_tree_selected_only_root(workspace):
    tree = get_workspace_tree(workspace, FilePath(), selected_only=True)

    # only the selected path should be in the tree
    expected = textwrap.dedent(
        """
        workspace***
          metadata
          empty_dir
          some_dir
        """
    )

    assert str(tree).strip() == expected.strip()


def test_get_workspace_tree_selected_has_empty_dir(workspace):
    selected_path = FilePath("some_dir")
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


def test_get_workspace_tree_selected_is_empty_dir(workspace):
    selected_path = FilePath("some_dir/subdir")
    (workspace.root() / selected_path).mkdir()
    tree = get_workspace_tree(workspace, selected_path, selected_only=True)

    # only the selected path should be in the tree
    expected = textwrap.dedent(
        """
        workspace*
          some_dir*
            subdir***
        """
    )

    assert str(tree).strip() == expected.strip()
    assert tree.get_path(selected_path).type == PathType.DIR


@pytest.mark.django_db
def test_get_request_tree_selected_only_file(release_request):
    selected_path = FilePath("group1/some_dir/file_a.txt")
    tree = get_request_tree(
        release_request,
        factories.create_airlock_user(),
        selected_path,
        selected_only=True,
    )

    # only the selected path should be in the tree, and all groups
    expected = textwrap.dedent(
        f"""
        {release_request.id}*
          group1*
            some_dir*
              file_a.txt**
              file_c.txt
          group2*
        """
    )

    assert str(tree).strip() == expected.strip()


@pytest.mark.django_db
def test_get_request_tree_selected_only_group(release_request):
    selected_path = FilePath("group1")
    tree = get_request_tree(
        release_request,
        factories.create_airlock_user(),
        selected_path,
        selected_only=True,
    )

    # only the selected path should be in the tree, and all groups
    expected = textwrap.dedent(
        f"""
        {release_request.id}*
          group1***
            some_dir*
              file_a.txt
              file_c.txt
          group2*
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
    tree = get_request_tree(release_request, factories.create_airlock_user())

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
    tree = get_request_tree(release_request, factories.create_airlock_user())
    assert tree.get_path(path).url().endswith(url)


@pytest.mark.django_db
def test_request_tree_download_url(release_request):
    tree = get_request_tree(release_request, factories.create_airlock_user())
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
    tree = get_request_tree(release_request, factories.create_airlock_user())
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
    selected_path = FilePath("some_dir/file_a.txt")
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
    selected_path = FilePath("some_dir")
    tree = get_workspace_tree(workspace, selected_path)

    selected_item = tree.get_path(selected_path)
    assert selected_item.selected
    # selected dir *is* expanded
    assert selected_item.expanded


def test_workspace_tree_selection_bad_path(workspace):
    selected_path = FilePath("bad/path")
    tree = get_workspace_tree(workspace, selected_path)
    with pytest.raises(tree.PathNotFound):
        tree.get_selected()


@pytest.mark.django_db
def test_request_tree_selection_root(release_request):
    # selected root by default
    tree = get_request_tree(release_request, factories.create_airlock_user())
    assert tree.get_selected() == tree


@pytest.mark.django_db
def test_request_tree_selection_path(release_request):
    selected_path = FilePath("group1/some_dir/file_a.txt")
    tree = get_request_tree(
        release_request, factories.create_airlock_user(), selected_path
    )

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
    selected_path = FilePath("bad/path")
    tree = get_request_tree(
        release_request, factories.create_airlock_user(), selected_path
    )
    with pytest.raises(tree.PathNotFound):
        tree.get_selected()


def test_workspace_tree_siblings(workspace):
    tree = get_workspace_tree(workspace)

    assert tree.siblings() == []
    assert {s.name() for s in tree.get_path("some_dir").siblings()} == {
        "metadata",
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
    tree = get_request_tree(release_request, factories.create_airlock_user())

    assert tree.siblings() == []
    assert {s.name() for s in tree.get_path("group1").siblings()} == {
        "group1",
        "group2",
    }
    assert {s.name() for s in tree.get_path("group1/some_dir").siblings()} == {
        "some_dir"
    }


def test_get_workspace_tree_tracing(workspace):
    selected_path = FilePath("some_dir/file_a.txt")
    get_workspace_tree(workspace, selected_path)
    traces = get_trace()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.attributes == {"workspace": workspace.name}


@pytest.mark.django_db
def test_get_request_tree_tracing(release_request):
    get_request_tree(release_request, factories.create_airlock_user())
    traces = get_trace()
    assert len(traces) == 1
    trace = traces[0]
    assert trace.attributes == {"release_request": release_request.id}


def test_get_code_tree(workspace):
    repo = factories.create_repo(
        workspace,
        [
            ("bar/1.txt", ""),
            ("foo/1.txt", ""),
            ("foo/2.txt", ""),
            ("foo/baz/3.txt", ""),
        ],
    )

    tree = get_code_tree(repo, FilePath("foo"), selected_only=False)

    expected = textwrap.dedent(
        f"""
        {repo.get_id()}*
          bar
            1.txt
          foo***
            baz
              3.txt
            1.txt
            2.txt
        """
    )

    assert str(tree).strip() == expected.strip()

    tree = get_code_tree(repo, FilePath("foo"), selected_only=True)

    expected = textwrap.dedent(
        f"""
        {repo.get_id()}*
          foo***
            baz
            1.txt
            2.txt
        """
    )

    assert str(tree).strip() == expected.strip()


def test_get_code_tree_root(workspace):
    repo = factories.create_repo(
        workspace,
        [
            ("bar/1.txt", ""),
            ("foo/1.txt", ""),
            ("foo/2.txt", ""),
            ("foo/baz/3.txt", ""),
        ],
    )

    tree = get_code_tree(repo, FilePath("."), selected_only=False)

    expected = textwrap.dedent(
        f"""
        {repo.get_id()}***
          bar
            1.txt
          foo
            baz
              3.txt
            1.txt
            2.txt
        """
    )
    assert str(tree).strip() == expected.strip()

    tree = get_code_tree(repo, FilePath("."), selected_only=True)

    expected = textwrap.dedent(
        f"""
        {repo.get_id()}***
          bar
            1.txt
          foo
            baz
              3.txt
            1.txt
            2.txt
        """
    )
    assert str(tree).strip() == expected.strip()

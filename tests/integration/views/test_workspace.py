import pytest
from django.contrib import messages
from django.contrib.messages.api import get_messages
from django.urls import reverse

from airlock import policies
from airlock.enums import RequestFileType, RequestStatus, WorkspaceFileStatus
from airlock.models import Project
from airlock.types import FilePath
from tests import factories
from tests.conftest import get_trace


pytestmark = pytest.mark.django_db


def test_home_redirects(airlock_client):
    airlock_client.login()
    response = airlock_client.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/workspaces/"


def test_workspace_view_summary(airlock_client):
    user = factories.create_airlock_user(
        workspaces={"workspace": factories.create_api_workspace(project="TESTPROJECT")}
    )

    airlock_client.login_with_user(user)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.txt")

    response = airlock_client.get("/workspaces/view/workspace/")
    assert "file.txt" in response.rendered_content
    assert "release-request-button" not in response.rendered_content
    assert "TESTPROJECT" in response.rendered_content


def test_workspace_view_archived_inactive(airlock_client):
    user = factories.create_airlock_user(
        workspaces={
            "workspace-abc": factories.create_api_workspace(
                project="TESTPROJECT",
                archived=True,
                ongoing=False,
            )
        }
    )

    airlock_client.login_with_user(user)
    workspace = factories.create_workspace("workspace-abc")
    factories.write_workspace_file(workspace, "file.txt")

    response = airlock_client.get("/workspaces/view/workspace-abc/")
    assert "workspace-abc (ARCHIVED)" in response.rendered_content
    assert "TESTPROJECT (INACTIVE)" in response.rendered_content


def test_workspace_view_with_existing_request_for_user(airlock_client):
    user = factories.create_airlock_user(output_checker=True)
    airlock_client.login_with_user(user)
    factories.write_workspace_file("workspace", "file.txt")
    release_request = factories.create_release_request("workspace", user=user)
    factories.create_filegroup(
        release_request, group_name="default_group", filepaths=["file.txt"]
    )
    response = airlock_client.get("/workspaces/view/workspace/")
    assert "current-request-button" in response.rendered_content


def test_workspace_does_not_exist(airlock_client):
    airlock_client.login(output_checker=True)
    response = airlock_client.get("/workspaces/view/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "some_dir/file.txt")
    (workspace.root() / "some_dir/subdir").mkdir()  # adds coverage of dir rendering
    response = airlock_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content
    assert "subdir" in response.rendered_content


@pytest.mark.parametrize(
    "login_as,status,can_multiselect_add",
    [
        # The request is pending, only author can add
        ("author", RequestStatus.PENDING, True),
        ("checker", RequestStatus.PENDING, False),
        # The request is under reivew, no-one can add
        ("author", RequestStatus.SUBMITTED, False),
        ("checker", RequestStatus.SUBMITTED, False),
        ("author", RequestStatus.PARTIALLY_REVIEWED, False),
        ("checker", RequestStatus.PARTIALLY_REVIEWED, False),
        ("author", RequestStatus.REVIEWED, False),
        ("checker", RequestStatus.REVIEWED, False),
        # The request is pending, only author can add
        ("author", RequestStatus.RETURNED, True),
        ("checker", RequestStatus.RETURNED, False),
        # The request is not current, only author can add
        ("author", RequestStatus.APPROVED, True),
        ("checker", RequestStatus.APPROVED, False),
        ("author", RequestStatus.RELEASED, True),
        ("checker", RequestStatus.RELEASED, False),
        ("author", RequestStatus.REJECTED, True),
        ("checker", RequestStatus.REJECTED, False),
        ("author", RequestStatus.WITHDRAWN, True),
        ("checker", RequestStatus.WITHDRAWN, False),
    ],
)
def test_workspace_directory_and_request_can_multiselect_add(
    airlock_client, bll, mock_old_api, login_as, status, can_multiselect_add
):
    users = {
        "author": factories.create_airlock_user(
            username="author", workspaces=["workspace"]
        ),
        "checker": factories.create_airlock_user(
            username="checker", workspaces=[], output_checker=True
        ),
    }
    airlock_client.login_with_user(users[login_as])
    factories.create_request_at_status(
        "workspace",
        status,
        author=users["author"],
        files=[factories.request_file(path="test/file.txt", approved=True)],
        withdrawn_after=(
            RequestStatus.PENDING if status == RequestStatus.WITHDRAWN else None
        ),
    )
    response = airlock_client.get("/workspaces/view/workspace/test/")
    button_enabled = not response.context["content_buttons"]["multiselect_add"].disabled
    assert button_enabled == can_multiselect_add


def test_workspace_view_with_empty_directory(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir()
    response = airlock_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 200
    assert "This directory is empty" in response.rendered_content


def test_workspace_view_with_file(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.txt", "foobar")
    response = airlock_client.get("/workspaces/view/workspace/file.txt")
    assert response.status_code == 200
    assert workspace.get_contents_url(FilePath("file.txt")) in response.rendered_content
    assert response.template_name == "file_browser/workspace/index.html"
    assert "HX-Request" in response.headers["Vary"]


@pytest.mark.parametrize(
    "request_status",
    [
        (RequestStatus.RETURNED),
        (RequestStatus.SUBMITTED),
    ],
)
def test_workspace_view_with_updated_file(bll, airlock_client, request_status):
    author = factories.create_airlock_user(
        username="author", workspaces=["test-workspace"]
    )

    airlock_client.login_with_user(author)
    # set up a returned file & request
    path = "file.txt"
    workspace = factories.create_workspace("test-workspace")

    request = factories.create_request_at_status(
        "test-workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(path=path, group="default", changes_requested=True)
        ],
    )

    if request_status != RequestStatus.RETURNED:
        bll.set_status(request, request_status, author)

    # change the file on disk
    factories.write_workspace_file(workspace, path, contents="changed")

    response = airlock_client.get("/workspaces/view/test-workspace/file.txt")
    assert response.status_code == 200
    assert workspace.get_contents_url(FilePath("file.txt")) in response.rendered_content
    assert "Update File in Request" in response.rendered_content
    assert response.template_name == "file_browser/workspace/index.html"
    assert "HX-Request" in response.headers["Vary"]


def test_workspace_view_with_file_htmx(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.txt", "foobar")
    response = airlock_client.get(
        "/workspaces/view/workspace/file.txt", headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert workspace.get_contents_url(FilePath("file.txt")) in response.rendered_content
    assert response.template_name == "file_browser/contents.html"
    assert '<ul id="tree"' not in response.rendered_content
    assert "HX-Request" in response.headers["Vary"]


def test_workspace_view_with_html_file(airlock_client):
    airlock_client.login(output_checker=True)
    factories.write_workspace_file(
        "workspace", "file.html", "<html><body>foobar</body></html>"
    )
    response = airlock_client.get("/workspaces/view/workspace/file.html")
    url = reverse(
        "workspace_contents",
        kwargs={"workspace_name": "workspace", "path": "file.html"},
    )
    assert f"{url}?cache_id=" in response.rendered_content


def test_workspace_view_with_svg_file(airlock_client):
    airlock_client.login(output_checker=True)
    TEST_SVG = """
    <svg viewBox="0 0 240 80" xmlns="http://www.w3.org/2000/svg">
    <style>
        .small {
        font: italic 13px sans-serif;
        }
        .heavy {
        font: bold 30px sans-serif;
        }

        /* Note that the color of the text is set with the    *
        * fill property, the color property is for HTML only */
        .Rrrrr {
        font: italic 40px serif;
        fill: red;
        }
    </style>

    <text x="20" y="35" class="small">My</text>
    <text x="40" y="35" class="heavy">cat</text>
    <text x="55" y="55" class="small">is</text>
    <text x="65" y="55" class="Rrrrr">Grumpy!</text>
    </svg>
    """

    factories.write_workspace_file(
        "workspace",
        "file.svg",
        TEST_SVG,
    )
    response = airlock_client.get("/workspaces/view/workspace/file.svg")
    url = reverse(
        "workspace_contents",
        kwargs={"workspace_name": "workspace", "path": "file.svg"},
    )
    assert f"{url}?cache_id=" in response.rendered_content


def test_workspace_view_with_csv_file(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.csv", "header1,header2\nFoo,Bar")
    response = airlock_client.get("/workspaces/view/workspace/file.csv")
    assert workspace.get_contents_url(FilePath("file.csv")) in response.rendered_content


def test_workspace_view_with_404(airlock_client):
    airlock_client.login(output_checker=True)
    factories.create_workspace("workspace")
    response = airlock_client.get("/workspaces/view/workspace/no_such_file.txt")
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = airlock_client.get("/workspaces/view/workspace/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces/view/workspace/some_dir/"


def test_workspace_view_directory_with_sub_directory(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "sub_dir/file.txt")
    response = airlock_client.get("/workspaces/view/workspace", follow=True)
    assert "sub_dir" in response.rendered_content
    assert "file.txt" in response.rendered_content


def test_workspace_view_redirects_to_file(airlock_client):
    airlock_client.login(output_checker=True)
    factories.write_workspace_file("workspace", "file.txt")
    response = airlock_client.get("/workspaces/view/workspace/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces/view/workspace/file.txt"


@pytest.mark.parametrize(
    "user,can_see_form",
    [
        (
            factories.create_api_user(workspaces=["workspace"], output_checker=True),
            True,
        ),
        (
            factories.create_api_user(workspaces=["workspace"], output_checker=False),
            True,
        ),
        (factories.create_api_user(workspaces=[], output_checker=True), False),
    ],
)
def test_workspace_view_file_add_to_request(airlock_client, user, can_see_form):
    airlock_client.login(**user)
    factories.write_workspace_file("workspace", "file.txt")
    response = airlock_client.get("/workspaces/view/workspace/file.txt")
    button_enabled = not response.context["content_buttons"]["add_file_button"].disabled
    assert button_enabled == can_see_form


@pytest.mark.parametrize(
    "status,is_current,files,can_see_form",
    [
        # author-editable
        (RequestStatus.PENDING, True, [], True),
        (RequestStatus.PENDING, True, [factories.request_file(path="file.txt")], False),
        (RequestStatus.RETURNED, True, [], True),
        (
            RequestStatus.RETURNED,
            True,
            [factories.request_file(path="file.txt", changes_requested=True)],
            False,
        ),
        # reviewer-editable
        (RequestStatus.SUBMITTED, True, [], False),
        (
            RequestStatus.SUBMITTED,
            True,
            [factories.request_file(path="file.txt")],
            False,
        ),
        (RequestStatus.PARTIALLY_REVIEWED, True, [], False),
        (
            RequestStatus.PARTIALLY_REVIEWED,
            True,
            [factories.request_file(path="file.txt", changes_requested=True)],
            False,
        ),
        (RequestStatus.REVIEWED, True, [], False),
        (
            RequestStatus.REVIEWED,
            True,
            [factories.request_file(path="file.txt", changes_requested=True)],
            False,
        ),
        # non-editable, can see form because there is no current request
        (RequestStatus.WITHDRAWN, False, [], True),
        (
            RequestStatus.WITHDRAWN,
            False,
            [factories.request_file(path="file.txt")],
            True,
        ),
        (RequestStatus.REJECTED, False, [], True),
        (
            RequestStatus.REJECTED,
            False,
            [factories.request_file(path="file.txt", changes_requested=True)],
            True,
        ),
        (RequestStatus.APPROVED, False, [], True),
        (
            # In Approved status, files are released but may not be uploaded yet;
            # Released files cannot be added to a new request as an output file
            RequestStatus.APPROVED,
            False,
            [factories.request_file(path="file.txt", approved=True)],
            False,
        ),
        (RequestStatus.RELEASED, False, [], True),
        (
            # Released files cannot be added to a new request as an output file
            RequestStatus.RELEASED,
            False,
            [factories.request_file(path="file.txt", approved=True)],
            False,
        ),
    ],
)
def test_workspace_view_file_add_to_current_request(
    mock_old_api, airlock_client, status, is_current, files, can_see_form
):
    user = factories.create_airlock_user(workspaces=["workspace"])
    airlock_client.login_with_user(user)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file("workspace", "file.txt", "foo")
    release_request = factories.create_request_at_status(
        workspace,
        status,
        author=user,
        files=[factories.request_file(path="other_file.txt", approved=True), *files],
        withdrawn_after=RequestStatus.PENDING
        if status == RequestStatus.WITHDRAWN
        else None,
    )
    response = airlock_client.get("/workspaces/view/workspace/file.txt")
    if is_current:
        assert response.context["current_request"] == release_request
    else:
        assert response.context["current_request"] is None
    button_enabled = not response.context["content_buttons"]["add_file_button"].disabled
    assert button_enabled == can_see_form


def test_workspace_view_index_no_user(airlock_client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = airlock_client.get("/workspaces/view/workspace/")
    assert response.status_code == 302


def test_workspace_view_with_directory_no_user(airlock_client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = airlock_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 302


def test_workspace_view_index_no_permission(airlock_client):
    factories.create_workspace("workspace")
    airlock_client.login(workspaces=["another-workspace"])
    response = airlock_client.get("/workspaces/view/workspace/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_permission(airlock_client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    airlock_client.login(workspaces=["another-workspace"])
    response = airlock_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 403


def test_workspace_contents_file(airlock_client):
    airlock_client.login(output_checker=True)
    factories.write_workspace_file("workspace", "file.txt", "test")
    response = airlock_client.get("/workspaces/content/workspace/file.txt")
    assert response.status_code == 200
    assert response.content == b'<pre class="txt">\ntest\n</pre>\n'


def test_workspace_contents_dir(airlock_client):
    airlock_client.login(output_checker=True)
    factories.write_workspace_file("workspace", "foo/file.txt", "test")
    response = airlock_client.get("/workspaces/content/workspace/foo")
    assert response.status_code == 400


def test_workspace_contents_not_exists(airlock_client):
    airlock_client.login(output_checker=True)
    factories.write_workspace_file("workspace", "file.txt", "test")
    response = airlock_client.get("/workspaces/content/workspace/notexists.txt")
    assert response.status_code == 404


def test_workspaces_index_no_user(airlock_client):
    response = airlock_client.get("/workspaces/")
    assert response.status_code == 302


def test_workspaces_index_user_permitted_workspaces(airlock_client):
    user = factories.create_airlock_user(
        username="testuser",
        workspaces={
            "test1a": factories.create_api_workspace(
                project="Project 1", archived=True
            ),
            "test1b": factories.create_api_workspace(project="Project 1"),
            "test1c": factories.create_api_workspace(project="Project 1"),
            "test2b": factories.create_api_workspace(
                project="Project 2", ongoing=False
            ),
            "test2a": factories.create_api_workspace(
                project="Project 2", ongoing=False
            ),
            "test3": factories.create_api_workspace(project="Project 3"),
        },
    )

    airlock_client.login_with_user(user)
    factories.create_workspace("test1a")
    factories.create_workspace("test1b")
    factories.create_workspace("test1c")
    factories.create_workspace("test2b")
    factories.create_workspace("test2a")
    factories.create_workspace("test3")
    factories.create_workspace("not-allowed")
    response = airlock_client.get("/workspaces/")

    projects = response.context["projects"]
    ongoing_project1 = Project(name="Project 1", is_ongoing=True)
    inactive_project1 = Project(name="Project 2", is_ongoing=False)
    ongoing_project2 = Project(name="Project 3", is_ongoing=True)

    # projects are ordered by ongoing first, then by name
    assert list(projects.keys()) == [
        ongoing_project1,
        ongoing_project2,
        inactive_project1,
    ]

    # within a project, workspaces are ordered by unarchived first and then by name
    assert [ws.name for ws in projects[ongoing_project1]] == [
        "test1b",
        "test1c",
        "test1a",
    ]
    assert [ws.name for ws in projects[ongoing_project2]] == ["test3"]
    assert [ws.name for ws in projects[inactive_project1]] == ["test2a", "test2b"]


def test_copiloted_workspaces_index(airlock_client):
    user = factories.create_airlock_user(
        username="testuser",
        workspaces={
            "test1a": factories.create_api_workspace(
                project="Project 1", archived=True
            ),
            "test1b": factories.create_api_workspace(project="Project 1"),
            "test1c": factories.create_api_workspace(project="Project 1"),
        },
        copiloted_workspaces={
            "test2b": factories.create_api_workspace(
                project="Project 2", ongoing=False
            ),
            "test2a": factories.create_api_workspace(
                project="Project 2", ongoing=False
            ),
            "test3": factories.create_api_workspace(project="Project 3"),
        },
    )

    airlock_client.login_with_user(user)
    factories.create_workspace("test1a")
    factories.create_workspace("test1b")
    factories.create_workspace("test1c")
    factories.create_workspace("test2b")
    factories.create_workspace("test2a")
    factories.create_workspace("test3")
    response = airlock_client.get("/copiloted-workspaces/")

    projects = response.context["projects"]
    inactive_project1 = Project(name="Project 2", is_ongoing=False)
    ongoing_project2 = Project(name="Project 3", is_ongoing=True)

    # Only copiloted workspaces are shown
    # projects are ordered by ongoing first, then by name
    assert list(projects.keys()) == [
        ongoing_project2,
        inactive_project1,
    ]

    # within a project, workspaces are ordered by unarchived first and then by name
    assert [ws.name for ws in projects[ongoing_project2]] == ["test3"]
    assert [ws.name for ws in projects[inactive_project1]] == ["test2a", "test2b"]


def test_workspace_multiselect_add_files_all_valid(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path1.txt")
    factories.write_workspace_file(workspace, "test/path2.txt")

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "add_files",
            "selected": [
                "test/path1.txt",
                "test/path2.txt",
            ],
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 200
    assert "test/path1.txt" in response.rendered_content
    assert "test/path2.txt" in response.rendered_content
    assert response.rendered_content.count("already in group") == 0
    assert response.rendered_content.count('value="OUTPUT"') == 2


def test_workspace_multiselect_add_files_one_valid(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path1.txt")
    factories.write_workspace_file(workspace, "test/path2.txt")
    release_request = factories.create_release_request(
        workspace, user=airlock_client.user
    )
    factories.add_request_file(release_request, "group1", "test/path1.txt")

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "add_files",
            "selected": [
                "test/path1.txt",
                "test/path2.txt",
            ],
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 200
    assert "test/path1.txt" in response.rendered_content
    assert "test/path2.txt" in response.rendered_content
    assert response.rendered_content.count("already in group") == 1
    assert response.rendered_content.count('value="OUTPUT"') == 1


def test_workspace_multiselect_add_files_none_valid(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path1.txt")
    factories.write_workspace_file(workspace, "test/path2.txt")
    release_request = factories.create_release_request(
        workspace, user=airlock_client.user
    )
    factories.add_request_file(release_request, "group1", "test/path1.txt")
    factories.add_request_file(release_request, "group1", "test/path2.txt")

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "add_files",
            "selected": [
                "test/path1.txt",
                "test/path2.txt",
            ],
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 200
    assert "test/path1.txt" in response.rendered_content
    assert "test/path2.txt" in response.rendered_content
    assert response.rendered_content.count("already in group") == 2
    assert response.rendered_content.count('value="OUTPUT"') == 0
    assert 'name="filegroup"' not in response.rendered_content


def test_workspace_multiselect_add_files_updated_file(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path1.txt")
    factories.write_workspace_file(workspace, "test/path2.txt")
    release_request = factories.create_release_request(
        workspace, user=airlock_client.user
    )
    factories.add_request_file(release_request, "group1", "test/path1.txt")
    factories.add_request_file(release_request, "group1", "test/path2.txt")

    factories.write_workspace_file(workspace, "test/path1.txt", "changed1")
    # refresh workspace
    workspace = bll.get_workspace("test1", airlock_client.user)
    policies.check_can_update_file_on_request(workspace, FilePath("test/path1.txt"))

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "add_files",
            "selected": [
                "test/path1.txt",
                "test/path2.txt",
            ],
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 200
    assert "test/path1.txt" in response.rendered_content
    assert "test/path2.txt" in response.rendered_content
    assert response.rendered_content.count("already in group") == 2
    assert 'name="filegroup"' not in response.rendered_content


@pytest.mark.parametrize(
    "path1_updated,path2_updated,ignored_count",
    [
        (True, True, 0),
        (True, False, 1),
        (False, False, 2),
    ],
)
def test_workspace_multiselect_update_files(
    airlock_client, bll, path1_updated, path2_updated, ignored_count
):
    author = factories.create_airlock_user(username="author", workspaces=["test1"])
    airlock_client.login_with_user(author)

    workspace = factories.create_workspace("test1")
    path1 = FilePath("test/path1.txt")
    path2 = FilePath("test/path2.txt")

    factories.create_request_at_status(
        "test1",
        author=airlock_client.user,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(path=path1, group="default", changes_requested=True),
            factories.request_file(path=path2, group="default", changes_requested=True),
        ],
    )

    assert workspace.get_workspace_file_status(path1) == WorkspaceFileStatus.UNRELEASED
    assert workspace.get_workspace_file_status(path2) == WorkspaceFileStatus.UNRELEASED

    if path1_updated:
        factories.write_workspace_file(workspace, path1, "changed1")
        # refresh workspace
        workspace = bll.get_workspace("test1", airlock_client.user)
        policies.check_can_update_file_on_request(workspace, FilePath(path1))

    if path2_updated:
        factories.write_workspace_file(workspace, path2, "changed1")
        # refresh workspace
        workspace = bll.get_workspace("test1", airlock_client.user)
        policies.check_can_update_file_on_request(workspace, FilePath(path2))

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "update_files",
            "selected": [
                "test/path1.txt",
                "test/path2.txt",
            ],
            "next_url": workspace.get_url(FilePath("test/path1.txt")),
        },
    )

    assert response.status_code == 200
    assert "test/path1.txt" in response.rendered_content
    assert "test/path2.txt" in response.rendered_content
    assert response.rendered_content.count("file cannot be updated") == ignored_count


def test_workspace_multiselect_add_released_file_not_valid(
    airlock_client, bll, mock_old_api
):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path1.txt", "foo")
    factories.write_workspace_file(workspace, "test/path2.txt", "bar")

    # create previously released request
    factories.create_request_at_status(
        workspace,
        RequestStatus.RELEASED,
        author=airlock_client.user,
        files=[
            factories.request_file(path="test/path1.txt", contents="foo", approved=True)
        ],
    )

    # create current pending request
    factories.create_release_request(workspace, user=airlock_client.user)

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "add_files",
            "selected": [
                "test/path1.txt",
                "test/path2.txt",
            ],
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 200
    assert "test/path1.txt" in response.rendered_content
    assert "test/path2.txt" in response.rendered_content
    assert response.rendered_content.count("already released") == 1
    assert response.rendered_content.count('value="OUTPUT"') == 1


def test_workspace_multiselect_bad_action(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={
            "action": "bad",
            "selected": ["foo"],
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 404


def test_workspace_multiselect_bad_form(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    factories.create_workspace("test1")

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={},
        follow=True,
    )

    assert response.status_code == 400
    assert response.headers["HX-Redirect"] == "/workspaces/view/test1/"

    all_messages = list(get_messages(response.wsgi_request))
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "action: This field is required" in message.message
    assert "next_url: This field is required" in message.message
    assert "selected: You must select at least one file" in message.message


def test_workspace_multiselect_bad_form_with_next_url(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    factories.create_workspace("test1")

    response = airlock_client.post(
        "/workspaces/multiselect/test1",
        data={"next_url": "/next"},
    )

    assert response.status_code == 400
    assert response.headers["HX-Redirect"] == "/next"


@pytest.mark.parametrize("filetype", ["OUTPUT", "SUPPORTING"])
def test_workspace_request_file_creates(airlock_client, bll, filetype):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert bll.get_current_request(workspace.name, airlock_client.user) is None
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": filetype,
            "filegroup": "default",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )
    assert response.status_code == 302
    assert workspace.get_url(FilePath("test/path.txt")) in response.headers["Location"]

    release_request = bll.get_current_request(workspace.name, airlock_client.user)
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert FilePath("test/path.txt") in filegroup.files
    assert release_request.abspath("default/test/path.txt").exists()
    release_file = filegroup.files[FilePath("test/path.txt")]
    assert release_file.filetype == RequestFileType[filetype]


def test_workspace_request_file_request_already_exists(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")
    release_request = factories.create_release_request(workspace, airlock_client.user)
    assert release_request.filegroups == {}

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
            "filegroup": "default",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )
    assert response.status_code == 302
    assert workspace.get_url(FilePath("test/path.txt")) in response.headers["Location"]

    current_release_request = bll.get_current_request(
        workspace.name, airlock_client.user
    )
    assert current_release_request.id == release_request.id
    assert current_release_request.abspath("default/test/path.txt").exists()
    filegroup = current_release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert FilePath("test/path.txt") in filegroup.files


def test_workspace_request_file_with_new_filegroup(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert bll.get_current_request(workspace.name, airlock_client.user) is None
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
            # new filegroup overrides a selected existing one (or the default)
            "filegroup": "default",
            "new_filegroup": "new_group",
        },
    )
    assert response.status_code == 302
    assert workspace.get_url(FilePath("test/path.txt")) in response.headers["Location"]

    release_request = bll.get_current_request(workspace.name, airlock_client.user)
    filegroup = release_request.filegroups["new_group"]
    assert filegroup.name == "new_group"


def test_workspace_request_file_filegroup_already_exists(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    release_request = factories.create_release_request(workspace, airlock_client.user)
    filegroupmetadata = factories.create_filegroup(release_request, "default")
    assert not filegroupmetadata.request_files.exists()

    airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
            "filegroup": "default",
        },
    )

    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"

    # Attempt to add the same file again
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
            "filegroup": "default",
        },
    )
    assert response.status_code == 302
    assert workspace.get_url(FilePath("test/path.txt")) in response.headers["Location"]

    # No new file created
    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"


def test_workspace_request_file_request_path_does_not_exist(airlock_client):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
            "filegroup": "default",
        },
    )

    assert response.status_code == 404


def test_workspace_request_file_invalid_new_filegroup(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    release_request = factories.create_release_request(workspace, airlock_client.user)
    filegroupmetadata = factories.create_filegroup(release_request, "test_group")

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
            "filegroup": "default",
            "new_filegroup": "test_group",
        },
        follow=True,
    )

    assert not filegroupmetadata.request_files.exists()
    assert response.request["PATH_INFO"] == workspace.get_url(FilePath("test/path.txt"))

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "already exists" in message.message


def test_workspace_request_file_invalid_form(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "form-0-filetype": "OUTPUT",
        },
        follow=True,
    )

    assert response.request["PATH_INFO"] == workspace.get_url()

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "next_url: This field is required" in message.message


def test_workspace_request_file_invalid_formset(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "next_url": workspace.get_url(FilePath("test/path.txt")),
            "filegroup": "default",
            "new_filegroup": "test_group",
        },
        follow=True,
    )

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "At least one form must be completed" in message.message


def test_workspace_request_update_file_invalid_status(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert bll.get_current_request(workspace.name, airlock_client.user) is None
    response = airlock_client.post(
        "/workspaces/update-file-in-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
        follow=True,
    )
    assert response.status_code == 200

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert (
        "Cannot update file in request if it is not updated on disk" in message.message
    )


def test_workspace_request_update_file_request_path_does_not_exist(airlock_client):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")

    response = airlock_client.post(
        "/workspaces/update-file-in-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-0-file": "test/path.txt",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
    )

    assert response.status_code == 404


def test_workspace_request_update_file_invalid_formset(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    response = airlock_client.post(
        "/workspaces/update-file-in-request/test1",
        data={
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
        follow=True,
    )

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "file: This field is required" in message.message


def test_workspace_request_update_file_empty_formset(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    response = airlock_client.post(
        "/workspaces/update-file-in-request/test1",
        data={
            "form-TOTAL_FORMS": "0",
            "form-INITIAL_FORMS": "0",
            "next_url": workspace.get_url(FilePath("test/path.txt")),
        },
        follow=True,
    )

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "At least one form must be completed." in message.message


@pytest.mark.parametrize(
    "urlpath,post_data",
    [
        ("/workspaces/view/test-workspace/", None),
        ("/workspaces/view/test-workspace/file.txt", None),
        ("/workspaces/content/test-workspace/file.txt", None),
        (
            "/workspaces/add-file-to-request/test-workspace",
            {
                "path": "test-workspace/file.txt",
                "filegroup": "default",
                "filetype": RequestFileType.OUTPUT,
                "next_url": "/workspaces/test-workspace/file.txt",
            },
        ),
    ],
)
def test_workspace_view_tracing_with_workspace_attribute(
    airlock_client, urlpath, post_data
):
    airlock_client.login(workspaces=["test-workspace"])
    factories.write_workspace_file("test-workspace", "file.txt")
    if post_data is not None:
        airlock_client.post(urlpath, post_data)
    else:
        airlock_client.get(urlpath)
    traces = get_trace()
    last_trace = traces[-1]
    assert last_trace.attributes == {
        "workspace": "test-workspace",
        "username": airlock_client.user.username,
        "user_id": airlock_client.user.user_id,
    }

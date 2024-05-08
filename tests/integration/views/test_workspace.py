import pytest
from django.contrib import messages
from django.shortcuts import reverse

from airlock.business_logic import AuditEventType, RequestFileType, bll
from airlock.types import UrlPath
from tests import factories
from tests.conftest import get_trace


pytestmark = pytest.mark.django_db


def test_home_redirects(airlock_client):
    airlock_client.login()
    response = airlock_client.get("/")
    assert response.status_code == 302
    assert response.headers["location"] == "/workspaces/"


def test_workspace_view_summary(airlock_client):
    airlock_client.login(workspaces={"workspace": {"project": "TESTPROJECT"}})
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.txt")
    # create audit event to appear on activity
    factories.create_release_request(
        workspace, user=factories.create_user("audit_user")
    )

    response = airlock_client.get("/workspaces/view/workspace/")
    assert "file.txt" in response.rendered_content
    assert "release-request-button" not in response.rendered_content
    assert "TESTPROJECT" in response.rendered_content
    assert "Recent activity" in response.rendered_content
    assert "audit_user" in response.rendered_content
    assert "Created request" in response.rendered_content


def test_workspace_view_with_existing_request_for_user(airlock_client):
    user = factories.create_user(output_checker=True)
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
    assert workspace.get_contents_url(UrlPath("file.txt")) in response.rendered_content
    assert response.template_name == "file_browser/index.html"
    assert "HX-Request" in response.headers["Vary"]


def test_workspace_view_with_file_htmx(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.txt", "foobar")
    response = airlock_client.get(
        "/workspaces/view/workspace/file.txt", headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    assert workspace.get_contents_url(UrlPath("file.txt")) in response.rendered_content
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
    assert workspace.get_contents_url(UrlPath("file.csv")) in response.rendered_content


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
        (factories.create_user(workspaces=["workspace"], output_checker=True), True),
        (factories.create_user(workspaces=["workspace"], output_checker=False), True),
        (factories.create_user(workspaces=[], output_checker=True), False),
    ],
)
def test_workspace_view_file_add_to_request(airlock_client, user, can_see_form):
    airlock_client.login_with_user(user)
    factories.write_workspace_file("workspace", "file.txt")
    response = airlock_client.get("/workspaces/view/workspace/file.txt")
    assert (response.context["form"] is None) == (not can_see_form)


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
    audit = bll.get_audit_log(
        user=airlock_client.user.username,
        workspace="workspace",
    )
    assert audit[0].type == AuditEventType.WORKSPACE_FILE_VIEW
    assert audit[0].path == UrlPath("file.txt")


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
    airlock_client.login(
        workspaces={
            "test1a": {"project": "Project 1"},
            "test1b": {"project": "Project 1"},
            "test2": {"project": "Project 2"},
        }
    )
    factories.create_workspace("test1a")
    factories.create_workspace("test1b")
    factories.create_workspace("test2")
    factories.create_workspace("not-allowed")
    response = airlock_client.get("/workspaces/")

    projects = response.context["projects"]
    assert projects["Project 1"][0].name == "test1a"
    assert projects["Project 1"][1].name == "test1b"
    assert projects["Project 2"][0].name == "test2"
    assert "not-allowed" not in response.rendered_content


@pytest.mark.parametrize("filetype", ["OUTPUT", "SUPPORTING"])
def test_workspace_request_file_creates(airlock_client, bll, filetype):
    airlock_client.login(workspaces=["test1"])
    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert bll.get_current_request(workspace.name, airlock_client.user) is None
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default", "filetype": filetype},
    )
    assert response.status_code == 302

    release_request = bll.get_current_request(workspace.name, airlock_client.user)
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert UrlPath("test/path.txt") in filegroup.files
    assert release_request.abspath("default/test/path.txt").exists()
    release_file = filegroup.files[UrlPath("test/path.txt")]
    assert release_file.filetype == RequestFileType[filetype]


def test_workspace_request_file_request_already_exists(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")
    release_request = factories.create_release_request(workspace, airlock_client.user)
    assert release_request.filegroups == {}

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default", "filetype": "OUTPUT"},
    )
    assert response.status_code == 302
    current_release_request = bll.get_current_request(
        workspace.name, airlock_client.user
    )
    assert current_release_request.id == release_request.id
    assert current_release_request.abspath("default/test/path.txt").exists()
    filegroup = current_release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert UrlPath("test/path.txt") in filegroup.files


def test_workspace_request_file_with_new_filegroup(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert bll.get_current_request(workspace.name, airlock_client.user) is None
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "path": "test/path.txt",
            # new filegroup overrides a selected existing one (or the default)
            "filegroup": "default",
            "new_filegroup": "new_group",
            "filetype": "OUTPUT",
        },
    )
    assert response.status_code == 302

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
        data={"path": "test/path.txt", "filegroup": "default", "filetype": "OUTPUT"},
    )

    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"

    # Attempt to add the same file again
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default", "filetype": "OUTPUT"},
    )
    assert response.status_code == 302
    # No new file created
    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"


def test_workspace_request_file_request_path_does_not_exist(airlock_client):
    airlock_client.login(workspaces=["test1"])
    factories.create_workspace("test1")

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default", "filetype": "OUTPUT"},
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
            "path": "test/path.txt",
            "filegroup": "default",
            "new_filegroup": "test_group",
            "filetype": "OUTPUT",
        },
        follow=True,
    )

    assert not filegroupmetadata.request_files.exists()
    # redirects to the workspace file again, with error messages
    assert response.request["PATH_INFO"] == workspace.get_url("test/path.txt")

    all_messages = [msg for msg in response.context["messages"]]
    assert len(all_messages) == 1
    message = all_messages[0]
    assert message.level == messages.ERROR
    assert "already exists" in message.message


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
    assert last_trace.attributes == {"workspace": "test-workspace"}

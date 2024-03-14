import pytest
from django.contrib import messages
from django.shortcuts import reverse

from tests import factories


pytestmark = pytest.mark.django_db


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


def test_workspace_view(airlock_client):
    airlock_client.login(output_checker=True)
    factories.write_workspace_file("workspace", "file.txt")

    response = airlock_client.get("/workspaces/view/workspace/")
    assert "file.txt" in response.rendered_content
    assert "release-request-button" not in response.rendered_content


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
    factories.write_workspace_file("workspace", "some_dir/file.txt")
    response = airlock_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.txt", "foobar")
    response = airlock_client.get("/workspaces/view/workspace/file.txt")
    assert response.status_code == 200
    assert workspace.get_contents_url("file.txt") in response.rendered_content
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
    assert workspace.get_contents_url("file.txt") in response.rendered_content
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
    assert f'src="{url}"' in response.rendered_content


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
    assert f'src="{url}"' in response.rendered_content


def test_workspace_view_with_csv_file(airlock_client):
    airlock_client.login(output_checker=True)
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "file.csv", "header1,header2\nFoo,Bar")
    response = airlock_client.get("/workspaces/view/workspace/file.csv")
    assert workspace.get_contents_url("file.csv") in response.rendered_content


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
    assert response.content == b"<pre>test</pre>"


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


def test_workspace_request_file_creates(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert bll.get_current_request(workspace.name, airlock_client.user) is None
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )
    assert response.status_code == 302

    release_request = bll.get_current_request(workspace.name, airlock_client.user)
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert str(filegroup.files[0].relpath) == "test/path.txt"
    assert release_request.abspath("default/test/path.txt").exists()


def test_workspace_request_file_request_already_exists(airlock_client, bll):
    airlock_client.login(workspaces=["test1"])

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")
    release_request = factories.create_release_request(workspace, airlock_client.user)
    assert release_request.filegroups == {}

    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )
    assert response.status_code == 302
    current_release_request = bll.get_current_request(
        workspace.name, airlock_client.user
    )
    assert current_release_request.id == release_request.id
    assert current_release_request.abspath("default/test/path.txt").exists()
    filegroup = current_release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert str(filegroup.files[0].relpath) == "test/path.txt"


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
        data={"path": "test/path.txt", "filegroup": "default"},
    )

    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"

    # Attempt to add the same file again
    response = airlock_client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
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
        data={"path": "test/path.txt", "filegroup": "default"},
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

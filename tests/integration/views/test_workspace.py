import pytest
from django.contrib import messages

from airlock.users import User
from tests import factories


pytestmark = pytest.mark.django_db


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


def test_workspace_view(client_with_permission):
    factories.write_workspace_file("workspace", "file.txt")

    response = client_with_permission.get("/workspaces/view/workspace/")
    assert "file.txt" in response.rendered_content
    assert "release-request-button" not in response.rendered_content


def test_workspace_view_with_existing_request_for_user(
    client_with_permission,
):
    user = User.from_session(client_with_permission.session)
    factories.write_workspace_file("workspace", "file.txt")
    release_request = factories.create_release_request("workspace", user=user)
    factories.create_filegroup(
        release_request, group_name="default_group", filepaths=["file.txt"]
    )
    response = client_with_permission.get("/workspaces/view/workspace/")
    assert "current-request-button" in response.rendered_content


def test_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/workspaces/view/bad/")
    assert response.status_code == 404


def test_workspace_view_with_directory(client_with_permission):
    factories.write_workspace_file("workspace", "some_dir/file.txt")
    response = client_with_permission.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client_with_permission):
    factories.write_workspace_file("workspace", "file.txt", "foobar")
    response = client_with_permission.get("/workspaces/view/workspace/file.txt")
    assert response.status_code == 200
    assert "foobar" in response.rendered_content


def test_workspace_view_with_html_file(client_with_permission):
    factories.write_workspace_file(
        "workspace", "file.html", "<html><body>foobar</body></html>"
    )
    response = client_with_permission.get("/workspaces/view/workspace/file.html")
    assert "foobar" in response.rendered_content


def test_workspace_view_with_csv_file(client_with_permission):
    factories.write_workspace_file("workspace", "file.csv", "header1,header2\nFoo,Bar")
    response = client_with_permission.get("/workspaces/view/workspace/file.csv")
    for content in ["<table", "Foo", "Bar"]:
        assert content in response.rendered_content


def test_workspace_view_with_404(client_with_permission):
    factories.create_workspace("workspace")
    response = client_with_permission.get("/workspaces/view/workspace/no_such_file.txt")
    assert response.status_code == 404


def test_workspace_view_redirects_to_directory(client_with_permission):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = client_with_permission.get("/workspaces/view/workspace/some_dir")
    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces/view/workspace/some_dir/"


def test_workspace_view_directory_with_sub_directory(client_with_permission):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, "sub_dir/file.txt")
    response = client_with_permission.get("/workspaces/view/workspace", follow=True)
    assert "sub_dir" in response.rendered_content
    assert "file.txt" in response.rendered_content


def test_workspace_view_redirects_to_file(client_with_permission):
    factories.write_workspace_file("workspace", "file.txt")
    response = client_with_permission.get("/workspaces/view/workspace/file.txt/")
    assert response.status_code == 302
    assert response.headers["Location"] == "/workspaces/view/workspace/file.txt"


def test_workspace_view_index_no_user(client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = client.get("/workspaces/view/workspace/")
    assert response.status_code == 302


def test_workspace_view_with_directory_no_user(client):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    response = client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 302


def test_workspace_view_index_no_permission(client_with_user):
    factories.create_workspace("workspace")
    forbidden_client = client_with_user({"workspaces": ["another-workspace"]})
    response = forbidden_client.get("/workspaces/view/workspace/")
    assert response.status_code == 403


def test_workspace_view_with_directory_no_permission(client_with_user):
    workspace = factories.create_workspace("workspace")
    (workspace.root() / "some_dir").mkdir(parents=True)
    forbidden_client = client_with_user({"workspaces": ["another-workspace"]})
    response = forbidden_client.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 403


def test_workspaces_index_no_user(client):
    response = client.get("/workspaces/")
    assert response.status_code == 302


def test_workspaces_index_user_permitted_workspaces(client_with_user):
    permitted_client = client_with_user({"workspaces": ["test1"]})
    factories.create_workspace("test1")
    factories.create_workspace("test2")
    response = permitted_client.get("/workspaces/")
    workspace_names = {ws.name for ws in response.context["workspaces"]}
    assert workspace_names == {"test1"}
    assert "test2" not in response.rendered_content


def test_workspace_request_file_creates(client_with_user, api):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert api.get_current_request(workspace.name, user) is None
    response = client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )
    assert response.status_code == 302

    release_request = api.get_current_request(workspace.name, user)
    filegroup = release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert str(filegroup.files[0].relpath) == "test/path.txt"
    assert release_request.abspath("test/path.txt").exists()


def test_workspace_request_file_request_already_exists(client_with_user, api):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")
    release_request = factories.create_release_request(workspace, user)
    assert release_request.filegroups == {}

    response = client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )
    assert response.status_code == 302
    current_release_request = api.get_current_request(workspace.name, user)
    assert current_release_request.id == release_request.id
    assert release_request.abspath("test/path.txt").exists()
    filegroup = current_release_request.filegroups["default"]
    assert filegroup.name == "default"
    assert str(filegroup.files[0].relpath) == "test/path.txt"


def test_workspace_request_file_with_new_filegroup(client_with_user, api):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    assert api.get_current_request(workspace.name, user) is None
    response = client.post(
        "/workspaces/add-file-to-request/test1",
        data={
            "path": "test/path.txt",
            # new filegroup overrides a selected existing one (or the default)
            "filegroup": "default",
            "new_filegroup": "new_group",
        },
    )
    assert response.status_code == 302

    release_request = api.get_current_request(workspace.name, user)
    filegroup = release_request.filegroups["new_group"]
    assert filegroup.name == "new_group"


def test_workspace_request_file_filegroup_already_exists(client_with_user, api):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    release_request = factories.create_release_request(workspace, user)
    filegroupmetadata = factories.create_filegroup(release_request, "default")
    assert not filegroupmetadata.request_files.exists()

    client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )

    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"

    # Attempt to add the same file again
    response = client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )
    assert response.status_code == 302
    # No new file created
    assert filegroupmetadata.request_files.count() == 1
    assert str(filegroupmetadata.request_files.first().relpath) == "test/path.txt"


def test_workspace_request_file_request_path_does_not_exist(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    factories.create_workspace("test1")

    response = client.post(
        "/workspaces/add-file-to-request/test1",
        data={"path": "test/path.txt", "filegroup": "default"},
    )

    assert response.status_code == 404


def test_workspace_request_file_invalid_new_filegroup(client_with_user, api):
    client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(client.session)

    workspace = factories.create_workspace("test1")
    factories.write_workspace_file(workspace, "test/path.txt")

    release_request = factories.create_release_request(workspace, user)
    filegroupmetadata = factories.create_filegroup(release_request, "test_group")

    response = client.post(
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
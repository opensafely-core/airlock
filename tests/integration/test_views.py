from io import BytesIO

import pytest
import requests
from django.contrib import messages

from airlock.api import Status
from airlock.users import User
from tests import factories


pytestmark = pytest.mark.django_db


def test_index(client):
    response = client.get("/")
    assert "Hello World" in response.rendered_content


@pytest.fixture
def client_with_user(client):
    def _client(session_user):
        session_user = {"id": 1, "username": "test", **session_user}
        session = client.session
        session["user"] = session_user
        session.save()
        client.user = User.from_session(session)
        return client

    return _client


@pytest.fixture
def client_with_permission(client_with_user):
    output_checker = {"output_checker": True}
    yield client_with_user(output_checker)


def test_workspace_view(client_with_permission, ui_options):
    factories.write_workspace_file("workspace", "file.txt")

    response = client_with_permission.get("/workspaces/view/workspace/")
    assert "file.txt" in response.rendered_content
    assert "release-request-button" not in response.rendered_content


def test_workspace_view_with_existing_request_for_user(
    client_with_permission, ui_options
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


def test_workspace_view_with_directory(client_with_permission, ui_options):
    factories.write_workspace_file("workspace", "some_dir/file.txt")
    response = client_with_permission.get("/workspaces/view/workspace/some_dir/")
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


def test_workspace_view_with_file(client_with_permission, ui_options):
    factories.write_workspace_file("workspace", "file.txt", "foobar")
    response = client_with_permission.get("/workspaces/view/workspace/file.txt")
    assert response.status_code == 200
    assert "foobar" in response.rendered_content


def test_workspace_view_with_html_file(client_with_permission, ui_options):
    factories.write_workspace_file(
        "workspace", "file.html", "<html><body>foobar</body></html>"
    )
    response = client_with_permission.get("/workspaces/view/workspace/file.html")
    assert "foobar" in response.rendered_content


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


def test_request_index_no_user(client):
    release_request = factories.create_release_request("workspace")
    response = client.get(f"/requests/view/{release_request.id}/")
    assert response.status_code == 302


def test_request_view_index(client_with_permission, ui_options):
    release_request = factories.create_release_request(
        "workspace", client_with_permission.user
    )
    factories.write_request_file(release_request, "group", "file.txt")
    response = client_with_permission.get(f"/requests/view/{release_request.id}/")
    assert "group" in response.rendered_content


def test_request_workspace_does_not_exist(client_with_permission):
    response = client_with_permission.get("/requests/view/bad/id/")
    assert response.status_code == 404


def test_request_id_does_not_exist(client_with_permission):
    response = client_with_permission.get("/requests/view/bad_id/")
    assert response.status_code == 404


def test_request_view_with_directory(client_with_permission, ui_options):
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "some_dir/file.txt")
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}/group/some_dir/"
    )
    assert response.status_code == 200
    assert "file.txt" in response.rendered_content


def test_request_view_with_file(client_with_permission, ui_options):
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}/group/file.txt"
    )
    assert response.status_code == 200
    assert "group" in response.rendered_content


def test_request_view_with_submitted_request(client_with_permission, ui_options):
    release_request = factories.create_release_request(
        "workspace", status=Status.SUBMITTED
    )
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}", follow=True
    )
    assert "Reject Request" in response.rendered_content
    assert "Release Files" in response.rendered_content


def test_request_view_with_authored_request_file(client_with_permission, ui_options):
    release_request = factories.create_release_request(
        "workspace",
        user=User.from_session(client_with_permission.session),
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "file.txt", "foobar")
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}/group/file.txt", follow=True
    )
    assert "Remove this file" in response.rendered_content


def test_request_view_with_404(client_with_permission):
    release_request = factories.create_release_request("workspace")
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}/group/no_such_file.txt"
    )
    assert response.status_code == 404


def test_request_view_redirects_to_directory(client_with_permission):
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(
        release_request, "group", "some_dir/file.txt", "foobar"
    )

    # test for group
    response = client_with_permission.get(f"/requests/view/{release_request.id}/group")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/requests/view/{release_request.id}/group/"

    # test for dir
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}/group/some_dir"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"]
        == f"/requests/view/{release_request.id}/group/some_dir/"
    )


def test_request_view_redirects_to_file(client_with_permission):
    release_request = factories.create_release_request("workspace")
    factories.write_request_file(release_request, "group", "file.txt")
    response = client_with_permission.get(
        f"/requests/view/{release_request.id}/group/file.txt/"
    )
    assert response.status_code == 302
    assert (
        response.headers["Location"]
        == f"/requests/view/{release_request.id}/group/file.txt"
    )


def test_request_index_user_permitted_requests(client_with_user):
    permitted_client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(permitted_client.session)
    release_request = factories.create_release_request("test1", user)
    response = permitted_client.get("/requests/")
    authored_ids = {r.id for r in response.context["authored_requests"]}
    outstanding_ids = {r.id for r in response.context["outstanding_requests"]}
    assert authored_ids == {release_request.id}
    assert outstanding_ids == set()


def test_request_index_user_output_checker(client_with_user):
    permitted_client = client_with_user(
        {"workspaces": ["test_workspace"], "output_checker": True}
    )
    user = User.from_session(permitted_client.session)
    other = User(1, "other")
    r1 = factories.create_release_request(
        "test_workspace", user=user, status=Status.SUBMITTED
    )
    r2 = factories.create_release_request(
        "other_workspace", user=other, status=Status.SUBMITTED
    )
    response = permitted_client.get("/requests/")

    authored_ids = {r.id for r in response.context["authored_requests"]}
    outstanding_ids = {r.id for r in response.context["outstanding_requests"]}
    assert authored_ids == {r1.id}
    assert outstanding_ids == {r2.id}


def test_request_submit_author(client_with_user):
    permitted_client = client_with_user({"workspaces": ["test1"]})
    user = User.from_session(permitted_client.session)
    release_request = factories.create_release_request("test1", user=user)
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = permitted_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 302
    persisted_request = factories.api.get_release_request(release_request.id)
    assert persisted_request.status == Status.SUBMITTED


def test_request_submit_not_author(client_with_user):
    permitted_client = client_with_user({"workspaces": ["test1"]})
    other_user = User(2, "other", [], False)
    release_request = factories.create_release_request(
        "test1", user=other_user, status=Status.PENDING
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = permitted_client.post(f"/requests/submit/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.api.get_release_request(release_request.id)
    assert persisted_request.status == Status.PENDING


def test_request_reject_output_checker(client_with_permission):
    author = User(1, "author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = client_with_permission.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 302
    persisted_request = factories.api.get_release_request(release_request.id)
    assert persisted_request.status == Status.REJECTED


def test_request_reject_not_output_checker(client_with_user):
    client = client_with_user({"workspaces": ["test1"]})
    author = User(1, "author", ["test1"], False)
    release_request = factories.create_release_request(
        "test1",
        user=author,
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "path/test.txt")

    response = client.post(f"/requests/reject/{release_request.id}")

    assert response.status_code == 403
    persisted_request = factories.api.get_release_request(release_request.id)
    assert persisted_request.status == Status.SUBMITTED


def test_request_release_files_success(client_with_permission, release_files_stubber):
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file1.txt", "test1")
    factories.write_request_file(release_request, "group", "test/file2.txt", "test2")

    api_responses = release_files_stubber(release_request)
    response = client_with_permission.post("/requests/release/request_id")

    assert response.status_code == 302

    assert api_responses.calls[1].request.body.read() == b"test1"
    assert api_responses.calls[2].request.body.read() == b"test2"


def test_requests_release_workspace_403(client_with_user):
    not_permitted_client = client_with_user({"workspaces": [], "output_checker": False})
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file1.txt", "test1")
    response = not_permitted_client.post("/requests/release/request_id")
    assert response.status_code == 403


def test_requests_release_author_403(client_with_permission):
    user = User.from_session(client_with_permission.session)
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        user=user,
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file1.txt", "test1")
    response = client_with_permission.post("/requests/release/request_id")
    assert response.status_code == 403


def test_requests_release_jobserver_403(client_with_permission, release_files_stubber):
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file.txt", "test")

    response = requests.Response()
    response.status_code = 403
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = client_with_permission.post("/requests/release/request_id")

    assert response.status_code == 403


@pytest.mark.parametrize(
    "content_type,content,should_contain_iframe",
    [
        ("text/plain", b"An error from job-server", False),
        ("text/html", b"<p>An error from job-server</p>", True),
    ],
)
def test_requests_release_jobserver_403_with_debug(
    client_with_permission,
    release_files_stubber,
    settings,
    content_type,
    content,
    should_contain_iframe,
):
    settings.DEBUG = True
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "test/file.txt", "test")

    response = requests.Response()
    response.status_code = 403
    response.headers = {"Content-Type": content_type}
    response.raw = BytesIO(content)
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    # test 403 is handled
    response = client_with_permission.post("/requests/release/request_id")
    # DEBUG is on, so we return the job-server error
    assert response.status_code == 200
    assert "An error from job-server" in response.rendered_content
    contains_iframe = "<iframe" in response.rendered_content
    assert contains_iframe == should_contain_iframe


def test_requests_release_files_404(client_with_permission, release_files_stubber):
    release_request = factories.create_release_request(
        "workspace",
        id="request_id",
        status=Status.SUBMITTED,
    )
    factories.write_request_file(release_request, "group", "test/file.txt", "test")

    # test 404 results in 500
    response = requests.Response()
    response.status_code = 404
    api403 = requests.HTTPError(response=response)
    release_files_stubber(release_request, body=api403)

    with pytest.raises(requests.HTTPError):
        client_with_permission.post("/requests/release/request_id")

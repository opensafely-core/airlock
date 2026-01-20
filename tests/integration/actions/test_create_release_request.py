import logging
import time
from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings

from airlock.actions import create_release_request
from airlock.enums import RequestFileType, RequestStatus
from airlock.exceptions import (
    APIException,
    FileNotFound,
    ManifestFileError,
    RequestPermissionDenied,
)
from tests import factories
from users.models import User


@pytest.mark.django_db
def test_create_release_request(bll):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir/file1.txt", contents="file1")
    factories.write_workspace_file(
        workspace, "test-dir/test-subdir/file2.txt", contents="file2"
    )
    factories.write_workspace_file(
        workspace, "test-dir/test-subdir/file3.txt", contents="file3"
    )
    factories.write_workspace_file(workspace, "test-dir1/file4.txt", contents="file4")
    factories.write_workspace_file(
        workspace, "test-dir1/test-subdir/file5.txt", contents="file5"
    )

    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir", "test-dir1/test-subdir"],
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir", "test-dir1-test-subdir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file1.txt",
        "test-dir/test-subdir/file2.txt",
        "test-dir/test-subdir/file3.txt",
        "test-dir1/test-subdir/file5.txt",
    }
    for filegroup in release_request.filegroups.values():
        assert filegroup.context == ""
        assert filegroup.controls == ""
    assert release_request.status == RequestStatus.PENDING

    # All auditable events in an automated release request are registered as automated actions
    audit_log = bll._dal.get_audit_log(request=release_request.id)
    for log in audit_log:
        assert log.extra["automated_action"] == "true"


@pytest.mark.django_db
def test_create_release_request_with_supporting_files(bll):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir/file1.txt", contents="file1")
    factories.write_workspace_file(
        workspace, "test-dir/supporting_file1.txt", contents="supporting file1"
    )
    factories.write_workspace_file(
        workspace, "test-dir1/test-subdir/file2.txt", contents="file2"
    )
    factories.write_workspace_file(
        workspace,
        "test-dir1/test-subdir/supporting_file2.txt",
        contents="supporting file2",
    )

    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir", "test-dir1/test-subdir"],
        supporting_files=[
            "test-dir/supporting_file1.txt",
            "test-dir1/test-subdir/supporting_file2.txt",
        ],
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir", "test-dir1-test-subdir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file1.txt",
        "test-dir1/test-subdir/file2.txt",
    }
    assert {
        str(rfile.relpath)
        for rfile in release_request.all_files_by_name.values()
        if rfile.filetype == RequestFileType.SUPPORTING
    } == {
        "test-dir/supporting_file1.txt",
        "test-dir1/test-subdir/supporting_file2.txt",
    }

    for filegroup in release_request.filegroups.values():
        assert filegroup.context == ""
        assert filegroup.controls == ""
    assert release_request.status == RequestStatus.PENDING


@pytest.mark.django_db
def test_create_release_request_existing_files(bll, mock_old_api, caplog):
    caplog.set_level(logging.DEBUG)
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(
        workspace, "test-dir/file_added.txt", contents="file_added"
    )
    factories.write_workspace_file(
        workspace, "test-dir/file_released.txt", contents="file_released"
    )
    factories.write_workspace_file(
        workspace, "test-dir/test-subdir/file2.txt", contents="file2"
    )
    factories.write_workspace_file(
        workspace, "test-dir/test-subdir/file3.txt", contents="file3"
    )

    # create a previous release for one file
    factories.create_request_at_status(
        author=factories.create_airlock_user(
            username="other_user", workspaces=["workspace"]
        ),
        workspace=workspace,
        status=RequestStatus.RELEASED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir/file_released.txt", approved=True
            )
        ],
    )

    # Create a partial release request; one file has already been added to the
    # dir group
    release_request = factories.create_request_at_status(
        author=author,
        workspace=workspace,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(group="test-dir", path="test-dir/file_added.txt")
        ],
    )
    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir"],
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    # Existing release request is retrieved and added to
    assert release_requests[0].id == release_request.id

    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file_added.txt",
        "test-dir/test-subdir/file2.txt",
        "test-dir/test-subdir/file3.txt",
    }

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.actions.create_release_request"
    ]
    assert len(logs) == 2
    assert {log.message for log in logs} == {
        "Adding files for group test-dir",
        "Total: 2/2 added (group test-dir)",
    }


@pytest.mark.django_db
def test_create_release_request_no_files_to_add(bll, mock_old_api, caplog):
    caplog.set_level(logging.DEBUG)
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(
        workspace, "test-dir/file_added.txt", contents="file_added"
    )

    # Create an existing release request
    release_request = factories.create_request_at_status(
        author=author,
        workspace=workspace,
        status=RequestStatus.PENDING,
        files=[
            factories.request_file(group="test-dir", path="test-dir/file_added.txt")
        ],
    )
    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir"],
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    # Existing release request is retrieved, no new one created
    assert release_requests[0].id == release_request.id

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.actions.create_release_request"
    ]
    assert len(logs) == 1
    assert {log.message for log in logs} == {
        "No files to add for group test-dir; 1 already added",
    }


@pytest.mark.django_db
def test_create_release_request_with_file_errors(bll, caplog):
    caplog.set_level(logging.DEBUG)
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(
        workspace, "test-dir/file1.txt", contents="file_added"
    )
    # Write a file with an invalid file type
    factories.write_workspace_file(workspace, "test-dir/file2.foo", contents="foo")

    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir"],
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1

    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file1.txt",
    }

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.actions.create_release_request"
    ]
    assert len(logs) == 3
    assert {log.message for log in logs} == {
        "Adding files for group test-dir",
        "Total: 1/1 added (group test-dir)",
        "Could not add file test-dir/file2.foo",
    }


@pytest.mark.django_db
def test_create_submitted_release_request_with_file_errors(bll, capsys):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(
        workspace, "test-dir/file1.txt", contents="file_added"
    )
    # Write a file with an invalid file type
    factories.write_workspace_file(workspace, "test-dir/file2.foo", contents="foo")

    with pytest.raises(FileNotFound):
        create_release_request(
            "author",
            "workspace",
            dirs=["test-dir"],
            context="foo",
            controls="bar",
            submit=True,
        )


@pytest.mark.django_db
def test_create_release_request_with_context_and_controls(bll):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")
    factories.write_workspace_file(workspace, "test-dir2/file2.txt", contents="file2")

    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir1", "test-dir2"],
        context="The context",
        controls="The controls",
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir1", "test-dir2"}
    for filegroup in release_request.filegroups.values():
        assert filegroup.context == "The context"
        assert filegroup.controls == "The controls"
    assert release_request.status == RequestStatus.PENDING


@pytest.mark.django_db
def test_create_submitted_release_request(bll, caplog):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")

    create_release_request(
        "author",
        "workspace",
        dirs=["test-dir1"],
        context="The context",
        controls="The controls",
        submit=True,
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert release_request.status == RequestStatus.SUBMITTED

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.actions.create_release_request"
    ]
    assert len(logs) == 4
    assert {log.message for log in logs} == {
        "Adding files for group test-dir1",
        "Total: 1/1 added (group test-dir1)",
        "Updating context/controls for group test-dir1",
        "Release request submitted",
    }

    # All auditable events in an automated release request are registered as automated actions
    audit_log = bll._dal.get_audit_log(request=release_request.id)
    for log in audit_log:
        assert log.extra["automated_action"] == "true"


@pytest.mark.django_db
def test_create_submitted_release_request_incomplete_context_controls(bll):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")

    with pytest.raises(
        RequestPermissionDenied, match="Incomplete context and/or controls"
    ):
        create_release_request(
            "author",
            "workspace",
            dirs=["test-dir1"],
            submit=True,
        )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert release_request.status == RequestStatus.PENDING


@pytest.mark.django_db
def test_create_submitted_release_request_already_submitted(bll, capsys):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")
    # create submitted release request for this file and user
    request = factories.create_request_at_status(
        author=author,
        workspace=workspace,
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir1/file1.txt", approved=True
            )
        ],
    )

    result = create_release_request(
        "author",
        "workspace",
        dirs=["test-dir1"],
        context="The context",
        controls="The controls",
        submit=True,
    )
    assert not result["completed"]
    assert result["request_id"] == request.id
    assert result["message"] == "Already submitted"

    # No auditable events in this attempt to create an automated release request, so no
    # events are logged as automated actions
    audit_log = bll._dal.get_audit_log(request=request.id)
    for log in audit_log:
        assert "automated_action" not in log.extra


@pytest.mark.django_db
def test_create_submitted_release_request_updated_file(bll, mock_old_api, capsys):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(
        workspace, "test-dir/file_released.txt", contents="file_released"
    )
    # create a previous release for this file
    factories.create_request_at_status(
        author=author,
        workspace=workspace,
        status=RequestStatus.RELEASED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir/file_released.txt", approved=True
            )
        ],
    )

    assert len(bll.get_requests_authored_by_user(author)) == 1

    # The release request attempt doesn't error, but isn't completed because
    # all files are already released
    result = create_release_request(
        "author",
        "workspace",
        dirs=["test-dir"],
        context="1",
        controls="2",
        submit=True,
    )
    assert not result["completed"]
    assert result["message"] == "Already released"

    # An empty release request was created, but no files added
    assert len(bll.get_requests_authored_by_user(author)) == 2
    latest_release_request = bll.get_current_request(workspace.name, author)
    assert result["request_id"] == latest_release_request.id
    assert latest_release_request.status == RequestStatus.PENDING
    assert not latest_release_request.output_files()

    # update the file
    factories.write_workspace_file(
        workspace, "test-dir/file_released.txt", contents="updated"
    )

    result = create_release_request(
        "author",
        "workspace",
        dirs=["test-dir"],
        context="1",
        controls="2",
        submit=True,
    )
    # The previous empty release request is used
    assert result["completed"]
    assert result["message"] == "Success"
    assert len(bll.get_requests_authored_by_user(author)) == 2
    latest_release_request = bll.get_current_request(workspace.name, author)
    assert result["request_id"] == latest_release_request.id
    assert latest_release_request.status == RequestStatus.SUBMITTED


@pytest.mark.django_db
def test_create_release_requests_auth(freezer, bll, auth_api_stubber):
    mock_now = datetime(2026, 1, 20, 10, 30, 0, tzinfo=UTC)
    freezer.move_to(mock_now)
    workspace = factories.create_workspace("new_workspace")
    factories.write_workspace_file(workspace, "test-dir/file.txt")

    # Create user with access to workspace and new_workspace, last refreshed 30s ago
    mock_30s_ago = datetime(2026, 1, 20, 10, 29, 30, tzinfo=UTC)
    author = factories.create_airlock_user(
        username="testuser",
        workspaces=["workspace", "new_workspace"],
        last_refresh=mock_30s_ago.timestamp(),
    )
    # Mock response from auth endpoint with new workspace data; user no longer has access to workspace, but
    # does have access to new_workspace
    auth_responses = auth_api_stubber(
        "authorise",
        json={
            "username": "testuser",
            "output_checker": False,
            "workspaces": {
                "new_workspace": {
                    "archived": False,
                    "project_details": {"name": "project", "ongoing": True},
                },
            },
        },
    )

    assert set(author.workspaces.keys()) == {"workspace", "new_workspace"}

    # Create the release request: user was refreshed < settings.AIRLOCK_AUTHZ_TIMEOUT seconds ago, so auth api not called,
    # and author's workspaces have not been updated
    create_release_request(
        "testuser",
        "new_workspace",
        dirs=["test-dir"],
    )
    author.refresh_from_db()
    assert len(auth_responses.calls) == 0
    assert set(author.workspaces.keys()) == {"workspace", "new_workspace"}

    # Move time on by settings.AIRLOCK_AUTHZ_TIMEOUT seconds and call the create_release_request again
    # now the auth api is called and author's workspaces have been updated
    mock_now = mock_now + timedelta(seconds=settings.AIRLOCK_AUTHZ_TIMEOUT)
    freezer.move_to(mock_now)
    create_release_request(
        "testuser",
        "new_workspace",
        dirs=["test-dir"],
    )
    author.refresh_from_db()
    assert len(auth_responses.calls) == 1
    assert set(author.workspaces.keys()) == {"new_workspace"}


@pytest.mark.django_db
def test_create_release_request_with_user_from_manifest(bll, auth_api_stubber):
    auth_api_stubber(
        "authorise",
        json={
            "username": "manifest_user",
            "output_checker": False,
            "workspaces": {
                "workspace": {
                    "archived": False,
                    "project_details": {"name": "project", "ongoing": True},
                }
            },
        },
    )

    # Write multiple files, the release request is created with the user in the manifest for the output
    # with the latest timestamp
    workspace = factories.create_workspace("workspace")

    factories.write_workspace_file(
        workspace,
        "test-dir/file1.txt",
        contents="file1",
        manifest_username="another_user",
    )
    factories.write_workspace_file(
        workspace,
        "test-dir/test-subdir/file2.txt",
        contents="file2",
        manifest_username="another_user",
    )
    factories.write_workspace_file(
        workspace,
        "test-dir/test-subdir/file3.txt",
        contents="file3",
        manifest_username="another_user",
    )
    factories.write_workspace_file(
        workspace,
        "test-dir1/file4.txt",
        contents="file4",
        manifest_username="another_user",
    )
    factories.write_workspace_file(
        workspace,
        "test-dir1/test-subdir/file5.txt",
        contents="file5",
        manifest_username="manifest_user",
    )

    create_release_request(
        None,
        "workspace",
        dirs=["test-dir", "test-dir1/test-subdir"],
    )

    expected_author = User.from_api_data({"username": "manifest_user"})
    release_requests = bll.get_requests_authored_by_user(expected_author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir", "test-dir1-test-subdir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file1.txt",
        "test-dir/test-subdir/file2.txt",
        "test-dir/test-subdir/file3.txt",
        "test-dir1/test-subdir/file5.txt",
    }
    assert release_request.status == RequestStatus.PENDING


@pytest.mark.django_db
def test_create_release_requests_with_existing_author_from_manifest(
    bll, auth_api_stubber
):
    # Create user with access to workspace
    # We set the last_refresh time to ensure the auth api is called
    author = factories.create_airlock_user(
        username="author",
        workspaces=["workspace"],
        last_refresh=time.time() - settings.AIRLOCK_AUTHZ_TIMEOUT,
    )
    # Mock response from auth endpoint with new workspace data; user no longer has access to workspace, but
    # does have access to new_workspace
    auth_api_stubber(
        "authorise",
        json={
            "username": "author",
            "output_checker": False,
            "workspaces": {
                "new_workspace": {
                    "archived": False,
                    "project_details": {"name": "project", "ongoing": True},
                },
            },
        },
    )
    assert set(author.workspaces.keys()) == {"workspace"}
    assert not bll.get_requests_authored_by_user(author)

    new_workspace = factories.create_workspace("new_workspace")
    factories.write_workspace_file(
        new_workspace,
        "test-dir/file1.txt",
        contents="file1",
        manifest_username="author",
    )

    create_release_request(
        None,
        "new_workspace",
        dirs=["test-dir"],
    )
    author.refresh_from_db()
    # author's workspaces have been updated
    assert set(author.workspaces.keys()) == {"new_workspace"}
    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file1.txt"
    }
    assert release_request.status == RequestStatus.PENDING


@pytest.mark.django_db
def test_create_release_request_no_manifest_user(bll):
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(
        workspace, "test-dir/file1.txt", contents="file1", manifest_username=None
    )

    with pytest.raises(ManifestFileError):
        create_release_request(
            None,
            "workspace",
            dirs=["test-dir"],
        )


@pytest.mark.django_db
def test_create_release_request_api_auth_error(bll, auth_api_stubber):
    auth_api_stubber("authorise", status=400)

    # Write multiple files, the release request is created with the user in the manifest for the output
    # with the latest timestamp
    workspace = factories.create_workspace("workspace")

    factories.write_workspace_file(
        workspace,
        "test-dir/file1.txt",
        contents="file1",
        manifest_username="manifest_user",
    )

    with pytest.raises(
        APIException,
        match="Could not retrieve user information from API for user 'manifest_user'",
    ):
        create_release_request(
            None,
            "workspace",
            dirs=["test-dir"],
        )

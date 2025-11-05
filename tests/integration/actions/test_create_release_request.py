import logging

import pytest

from airlock.actions import create_release_request
from airlock.enums import RequestStatus
from airlock.exceptions import FileNotFound, RequestPermissionDenied
from tests import factories


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

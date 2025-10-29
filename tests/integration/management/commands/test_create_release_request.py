import pytest
from django.core.management import call_command

from airlock.enums import RequestStatus
from airlock.exceptions import RequestPermissionDenied
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

    call_command(
        "create_release_request",
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
def test_create_release_request_existing_files(bll, mock_old_api, capsys):
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
    call_command(
        "create_release_request",
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

    output = capsys.readouterr().out
    assert "Files already added: 1" in output
    assert "Files already released: 1" in output


@pytest.mark.django_db
@pytest.mark.parametrize("verbosity", [1, 2])
def test_create_release_request_with_file_errors(bll, capsys, verbosity):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(
        workspace, "test-dir/file1.txt", contents="file_added"
    )
    # Write a file with an invalid file type
    factories.write_workspace_file(workspace, "test-dir/file2.foo", contents="foo")

    call_command(
        "create_release_request",
        "author",
        "workspace",
        dirs=["test-dir"],
        verbosity=verbosity,
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1

    release_request = release_requests[0]
    assert set(release_request.filegroups) == {"test-dir"}
    assert {str(relpath) for relpath in release_request.output_files().keys()} == {
        "test-dir/file1.txt",
    }

    output = capsys.readouterr().out
    assert "Couldn't add files: 1" in output
    error_detail_in_output = "- test-dir/file2.foo" in output
    assert error_detail_in_output == (verbosity > 1)


@pytest.mark.django_db
def test_create_release_request_with_context_and_controls(bll):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")
    factories.write_workspace_file(workspace, "test-dir2/file2.txt", contents="file2")

    call_command(
        "create_release_request",
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
def test_create_submitted_release_request(bll, capsys):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")

    call_command(
        "create_release_request",
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
    output = capsys.readouterr().out
    assert "Release request submitted" in output


@pytest.mark.django_db
def test_create_submitted_release_request_incomplete_context_controls(bll):
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir1/file1.txt", contents="file1")

    with pytest.raises(
        RequestPermissionDenied, match="Incomplete context and/or controls"
    ):
        call_command(
            "create_release_request",
            "author",
            "workspace",
            dirs=["test-dir1"],
            submit=True,
        )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1
    release_request = release_requests[0]
    assert release_request.status == RequestStatus.PENDING

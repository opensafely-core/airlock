from unittest.mock import Mock, patch

import pytest
from django.core.management import call_command
from django.utils.dateparse import parse_datetime

from airlock.enums import AuditEventType, RequestStatus
from airlock.management.commands.run_file_uploader import do_upload_task
from airlock.types import FilePath
from old_api import FileUploadError
from tests import factories
from tests.conftest import get_trace


pytestmark = pytest.mark.django_db


def setup_release_request(upload_files_stubber, bll, response_statuses=None):
    # create an approved released request, with files waiting for upload
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])
    release_request = factories.create_request_at_status(
        workspace,
        author=author,
        status=RequestStatus.REVIEWED,
        files=[
            factories.request_file(
                group="group",
                path="test/file.txt",
                contents="test",
                approved=True,
            ),
            factories.request_file(
                group="group",
                path="test/file1.txt",
                contents="test",
                approved=True,
            ),
            factories.request_file(
                group="group",
                path="test/file2.txt",
                contents="test",
                approved=True,
            ),
        ],
    )
    upload_files_responses = upload_files_stubber(release_request, response_statuses)
    bll.release_files(release_request, factories.get_default_output_checkers()[0])
    release_request = factories.refresh_release_request(release_request)
    return release_request, upload_files_responses


def refresh_request_file(release_request, file_path):
    release_request = factories.refresh_release_request(release_request)
    request_file = release_request.get_request_file_from_output_path(file_path)
    return request_file


def test_do_upload_task(upload_files_stubber, bll, freezer):
    freezer.move_to("2022-01-01T12:34:56")
    release_request, _ = setup_release_request(
        upload_files_stubber, bll, response_statuses=[201]
    )

    file_path = FilePath("test/file.txt")
    request_file = release_request.get_request_file_from_output_path(file_path)
    assert not request_file.uploaded
    assert request_file.uploaded_at is None

    freezer.move_to("2022-01-03T12:34:56")
    do_upload_task(request_file, release_request)

    request_file = refresh_request_file(release_request, file_path)
    assert request_file.uploaded
    assert request_file.uploaded_at == parse_datetime("2022-01-03T12:34:56Z")


def test_do_upload_task_updated_file_content(upload_files_stubber, bll):
    release_request, upload_files_responses = setup_release_request(
        upload_files_stubber, bll, response_statuses=[201]
    )

    file_path = FilePath("test/file.txt")
    # modify workspace file content
    factories.write_workspace_file(
        release_request.workspace, file_path, contents="changed"
    )

    request_file = release_request.get_request_file_from_output_path(file_path)

    do_upload_task(request_file, release_request)
    # 2 calls, to create the release and then to upload one file
    assert len(upload_files_responses.calls) == 2
    upload_call = upload_files_responses.calls[-1]
    assert upload_call.request.url.endswith(f"/releases/release/{release_request.id}")

    # The upload endpoint is called with the (old) request file content, not the
    # updated workspace file content
    assert upload_call.request.body == b"test"


def test_do_upload_task_api_error(upload_files_stubber, bll, freezer):
    freezer.move_to("2022-01-01T12:34:56")
    release_request, workspace = setup_release_request(
        upload_files_stubber, bll, response_statuses=[403]
    )
    file_path = FilePath("test/file.txt")
    request_file = release_request.get_request_file_from_output_path(file_path)

    with pytest.raises(FileUploadError):
        do_upload_task(request_file, release_request)

    request_file = refresh_request_file(release_request, file_path)
    assert not request_file.uploaded
    assert request_file.uploaded_at is None


def test_run_file_uploader_command(upload_files_stubber, bll):
    release_request, _ = setup_release_request(upload_files_stubber, bll)
    checker = factories.get_default_output_checkers()[0]

    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = release_request.get_request_file_from_output_path(
            FilePath(filename)
        )
        assert not request_file.uploaded
        assert request_file.uploaded_at is None
        assert request_file.upload_attempts == 0

    # Mock run_fn so we only loop once
    run_fn = Mock(side_effect=[True, False])
    call_command("run_file_uploader", run_fn=run_fn)

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.RELEASED

    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = refresh_request_file(release_request, FilePath(filename))
        assert request_file.uploaded
        assert request_file.uploaded_at is not None
        # Running via the command updates retry attempt before the upload is tried
        assert request_file.upload_attempts == 1

    audit_log = bll.get_request_audit_log(checker, release_request)
    assert audit_log[0].type == AuditEventType.REQUEST_RELEASE
    assert {log.type for log in audit_log[1:3]} == {AuditEventType.REQUEST_FILE_UPLOAD}

    traces = get_trace()
    last_trace = traces[-1]
    assert last_trace.attributes == {
        "release_request": release_request.id,
        "workspace": "workspace",
        "group": "group",
        "file": "test/file2.txt",
        "username": checker.username,
        "user_id": checker.user_id,
    }


@patch("airlock.management.commands.run_file_uploader.time.sleep")
def test_run_file_uploader_command_no_tasks(mock_sleep, settings):
    run_fn = Mock(side_effect=[True, False])
    call_command("run_file_uploader", run_fn=run_fn)
    mock_sleep.assert_called_with(settings.UPLOAD_DELAY)


def test_run_file_uploader_command_all_files_uploaded(
    upload_files_stubber, bll, freezer
):
    freezer.move_to("2022-01-01T12:34:56")
    release_request, _ = setup_release_request(
        upload_files_stubber, bll, response_statuses=[]
    )
    assert release_request.status == RequestStatus.APPROVED

    checker = factories.get_default_output_checkers()[0]
    # make all files uploaded
    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        bll.register_file_upload(release_request, FilePath(filename), checker)

    freezer.move_to("2022-01-02T12:34:56")
    # Mock run_fn so we only loop once
    run_fn = Mock(side_effect=[True, False])
    call_command("run_file_uploader", run_fn=run_fn)

    release_request = factories.refresh_release_request(release_request)
    assert release_request.status == RequestStatus.RELEASED

    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = refresh_request_file(release_request, FilePath(filename))
        assert request_file.uploaded
        assert request_file.uploaded_at == parse_datetime("2022-01-01T12:34:56Z")
        # All files were already uploaded, no attempts made
        assert request_file.upload_attempts == 0


def test_run_file_uploader_command_api_error(upload_files_stubber, bll, settings):
    # set upload retry delay to 0 so files that error will be retried in the test
    settings.UPLOAD_RETRY_DELAY = 0
    # Mock status responses for file uploads; there are 3 files in total -
    # on the first run file 2/3 errors, on the second run only file 2
    # is retried and succeeds
    release_request, _ = setup_release_request(
        upload_files_stubber, bll, response_statuses=[201, 403, 201, 201]
    )

    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = release_request.get_request_file_from_output_path(
            FilePath(filename)
        )
        assert not request_file.uploaded
        assert request_file.uploaded_at is None
        assert request_file.upload_attempts == 0

    # mock the run function so it will loop twice
    run_fn = Mock(side_effect=[True, True, False])
    call_command("run_file_uploader", run_fn=run_fn)

    release_request = factories.refresh_release_request(release_request)

    # all files are successfully uploaded
    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = refresh_request_file(release_request, FilePath(filename))
        assert request_file.uploaded
        assert request_file.uploaded_at is not None
        # Running via the command updates retry attempt before the upload is tried
        # file and file2 succeeded on the first attempt, file1 on the second attempt
        if filename == "test/file1.txt":
            request_file.upload_attempts == 2
        else:
            request_file.upload_attempts == 1


def test_run_file_uploader_with_retry_delay(
    upload_files_stubber, bll, settings, freezer
):
    freezer.move_to("2022-01-02T12:00:00")
    settings.UPLOAD_RETRY_DELAY = 30
    # Mock status responses for file uploads; there are 3 files in total -
    # on the first run file 2/3 errors, on the second run only file 2
    # is retried and succeeds
    release_request, _ = setup_release_request(
        upload_files_stubber, bll, response_statuses=[201, 201]
    )

    # register a file upload attempt for test/file.txt at 12:00:00
    bll.register_file_upload_attempt(release_request, FilePath("test/file.txt"))

    # move to > UPLOAD_RETRY_DELAY secs later
    freezer.tick(delta=31)
    # register a file upload attempt for test/file1.txt at 12:00:31
    bll.register_file_upload_attempt(release_request, FilePath("test/file1.txt"))

    # mock the run function so it will loop once only
    run_fn = Mock(side_effect=[True, False])
    call_command("run_file_uploader", run_fn=run_fn)

    release_request = factories.refresh_release_request(release_request)

    # file.txt (attempted > 30s ago) and file2.txt (not attempted) are uploaded,
    # file1.txt was attempted too recently to retry
    for filename in ["test/file.txt", "test/file2.txt"]:
        request_file = refresh_request_file(release_request, FilePath(filename))
        assert request_file.uploaded
        assert request_file.uploaded_at is not None

    request_file = refresh_request_file(release_request, FilePath("test/file1.txt"))
    assert not request_file.uploaded
    assert request_file.uploaded_at is None


def test_run_file_uploader_command_unexpected_error(
    upload_files_stubber, bll, settings
):
    # set upload retry delay to 0 so files that error will be retried in the test
    settings.UPLOAD_RETRY_DELAY = 0
    # Mock status responses for file uploads; there are 3 files in total -
    # on the first run file 2/3 errors, on the second run only file 2
    # is retried and succeeds
    checker = factories.get_default_output_checkers()[0]
    release_request, _ = setup_release_request(
        upload_files_stubber, bll, response_statuses=[]
    )

    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = release_request.get_request_file_from_output_path(
            FilePath(filename)
        )
        assert not request_file.uploaded
        assert request_file.uploaded_at is None
        assert request_file.upload_attempts == 0

    # mock the run function so it will loop twice
    run_fn = Mock(side_effect=[True, True, False])

    with patch(
        "airlock.management.commands.run_file_uploader.do_upload_task",
        side_effect=Exception("an unknown exception"),
    ):
        call_command("run_file_uploader", run_fn=run_fn)

    release_request = factories.refresh_release_request(release_request)

    # no files are successfully uploaded, all have been attempted twice
    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = refresh_request_file(release_request, FilePath(filename))
        assert not request_file.uploaded
        request_file.upload_attempts == 2

    traces = get_trace()
    last_trace = traces[-1]

    assert last_trace.attributes == {
        "release_request": release_request.id,
        "workspace": "workspace",
        "group": "group",
        "file": "test/file2.txt",
        "username": checker.username,
        "user_id": checker.user_id,
    }
    last_trace_event = last_trace.events[0]
    assert last_trace_event.name == "exception"
    assert last_trace_event.attributes["exception.type"] == "Exception"
    assert last_trace_event.attributes["exception.message"] == "an unknown exception"


def test_run_file_uploader_command_multiple_attempts(
    upload_files_stubber, bll, settings
):
    # set upload retry delay to 0 so files that error will be retried in the test
    settings.UPLOAD_RETRY_DELAY = 0
    # Mock status responses for file uploads; there are 3 files in total -
    # file 1 errors 3 times
    release_request, _ = setup_release_request(
        upload_files_stubber,
        bll,
        response_statuses=[
            500,
            201,
            201,  # loop 1, 2/3 file succeed
            500,  # loop 2, file 1 fails again
            500,  # loop 3, file 1 fails again
            500,  # loop 4, file 1 fails again
        ],
    )

    for filename in ["test/file.txt", "test/file1.txt", "test/file2.txt"]:
        request_file = release_request.get_request_file_from_output_path(
            FilePath(filename)
        )
        assert not request_file.uploaded
        assert request_file.uploaded_at is None
        assert request_file.upload_attempts == 0

    # mock the run function so it will loop 4 times; file 1 fails each time
    # has now had 3 attempts to upload
    run_fn = Mock(side_effect=[True, True, True, True, False])
    call_command("run_file_uploader", run_fn=run_fn)

    release_request = factories.refresh_release_request(release_request)

    # successful uploads
    for filename in ["test/file1.txt", "test/file2.txt"]:
        request_file = refresh_request_file(release_request, FilePath(filename))
        assert request_file.uploaded
        assert request_file.uploaded_at is not None
        assert request_file.upload_attempts == 1

    bad_file = refresh_request_file(release_request, FilePath("test/file.txt"))
    assert bad_file.upload_attempts == 4
    assert not bad_file.uploaded

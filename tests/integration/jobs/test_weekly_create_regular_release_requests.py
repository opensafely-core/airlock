import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command

from airlock.enums import RequestStatus
from airlock.jobs.weekly.create_regular_release_requests import (
    ConfigValidationError,
    get_config_data,
    validate_config_data,
)
from tests import factories
from tests.conftest import get_trace
from users.models import User


FIXTURE_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def setup_test_data():
    factories.create_airlock_user(
        username="author", workspaces=["workspace", "workspace1"]
    )
    workspace = factories.create_workspace("workspace")
    workspace1 = factories.create_workspace("workspace1")
    factories.write_workspace_file(workspace, "test-dir/file1.txt", contents="file1")
    factories.write_workspace_file(workspace1, "test-dir/file2.txt", contents="file2")


@pytest.fixture
def mock_config():
    with patch(
        "airlock.jobs.weekly.create_regular_release_requests.CONFIG_PATH",
        FIXTURE_DIR / "regular_release_requests.json",
    ):
        yield


def test_get_config_data_file_does_not_exist():
    with patch(
        "airlock.jobs.weekly.create_regular_release_requests.CONFIG_PATH",
        FIXTURE_DIR / "non_existent_file.json",
    ):
        assert get_config_data() == []


@pytest.mark.parametrize(
    "config,errors",
    [
        # missing required keys
        ({}, ["keys missing in config"]),
        ({"dirs": ["output"]}, ["keys missing in config"]),
        # invalid types
        (
            {"workspace_name": "workspace", "username": "author", "dirs": "output"},
            ["Invalid config type for 'dirs'"],
        ),
        (
            {"workspace_name": 1, "username": "author", "dirs": ["output"]},
            ["Invalid config type for 'workspace_name'"],
        ),
        (
            {
                "workspace_name": "workspace",
                "username": "author",
                "dirs": ["output"],
                "submit": "false",
            },
            ["Invalid config type for 'submit'"],
        ),
        # submit missing context/controls
        (
            {
                "workspace_name": "workspace",
                "username": "author",
                "dirs": ["output"],
                "submit": True,
            },
            ["Context and/or controls missing"],
        ),
        # multiple errors
        (
            {"workspace_name": 1, "username": "author", "submit": True},
            [
                "keys missing in config",
                "Invalid config type for 'workspace_name'",
                "Context and/or controls missing",
            ],
        ),
    ],
)
def test_config_validation(config, errors):
    with pytest.raises(ConfigValidationError) as err:
        validate_config_data(config)
    for error in errors:
        assert error in str(err)


@pytest.mark.django_db
def test_create_regular_release_requests(bll, mock_config):
    author = User.objects.get(user_id="author")
    assert not bll.get_requests_authored_by_user(author)
    call_command("runjob", "create_regular_release_requests")
    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 2


@pytest.mark.django_db
def test_weekly_runjobs(bll, mock_config, caplog):
    caplog.set_level(logging.INFO)
    author = User.objects.get(user_id="author")
    assert not bll.get_requests_authored_by_user(author)
    call_command("runjobs", "weekly")
    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 2

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.jobs.weekly.create_regular_release_requests"
    ]
    assert len(logs) == 4
    assert {log.message for log in logs} == {
        "Starting automated release request for workspace",
        f"Release request complete for workspace: {release_requests[0].id}",
        "Starting automated release request for workspace1",
        f"Release request complete for workspace1: {release_requests[1].id}",
    }

    spans = get_trace()
    assert len(spans) == 2

    for span in spans:
        assert span.name == "create_regular_release_requests"
    # context/controls is not included in the attributes
    assert spans[0].attributes == {
        "workspace_name": "workspace",
        "username": "author",
        "dirs": ("test-dir",),
        "result.request_id": release_requests[0].id,
        "result.completed": True,
        "result.message": "Success",
    }
    assert spans[1].attributes == {
        "workspace_name": "workspace1",
        "username": "author",
        "dirs": ("test-dir",),
        "submit": True,
        "result.request_id": release_requests[1].id,
        "result.completed": True,
        "result.message": "Success",
    }


@pytest.mark.django_db
def test_weekly_runjobs_already_submitted(bll, mock_old_api, mock_config, caplog):
    caplog.set_level(logging.INFO)
    author = User.objects.get(user_id="author")
    # create submitted release request
    submitted_request = factories.create_request_at_status(
        author=author,
        workspace="workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir/file1.txt", approved=True
            )
        ],
    )

    # create pending release request for workspace1
    pending_release_request = factories.create_request_at_status(
        author=author,
        workspace="workspace1",
        status=RequestStatus.PENDING,
    )
    assert (len(bll.get_requests_authored_by_user(author))) == 2

    call_command("runjobs", "weekly")

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.jobs.weekly.create_regular_release_requests"
    ]
    assert len(logs) == 4

    assert {log.message for log in logs} == {
        "Starting automated release request for workspace",
        "Release request creation not completed for workspace: Already submitted",
        "Starting automated release request for workspace1",
        f"Release request complete for workspace1: {pending_release_request.id}",
    }

    spans = get_trace()
    assert len(spans) == 2

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 2
    # Exceptions are recorded on spans
    # request_id is not recorded as no new requests have been successfully created
    # (A new empty request may have been created, if all files have already been
    # released)
    assert spans[0].attributes == {
        "workspace_name": "workspace",
        "username": "author",
        "dirs": ("test-dir",),
        "result.request_id": submitted_request.id,
        "result.completed": False,
        "result.message": "Already submitted",
    }
    assert spans[1].attributes == {
        "workspace_name": "workspace1",
        "username": "author",
        "dirs": ("test-dir",),
        "submit": True,
        "result.request_id": pending_release_request.id,
        "result.completed": True,
        "result.message": "Success",
    }


@pytest.mark.django_db
def test_weekly_runjobs_already_released(bll, mock_old_api, mock_config, caplog):
    caplog.set_level(logging.INFO)
    author = User.objects.get(user_id="author")

    # create released release request for workspace
    factories.create_request_at_status(
        author=author,
        workspace="workspace",
        status=RequestStatus.RELEASED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir/file1.txt", approved=True
            )
        ],
    )
    # create pending release request for workspace1
    pending_release_request = factories.create_request_at_status(
        author=author,
        workspace="workspace1",
        status=RequestStatus.PENDING,
    )
    assert (len(bll.get_requests_authored_by_user(author))) == 2

    call_command("runjobs", "weekly")

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.jobs.weekly.create_regular_release_requests"
    ]
    assert len(logs) == 4

    assert {log.message for log in logs} == {
        "Starting automated release request for workspace",
        "Release request creation not completed for workspace: Already released",
        "Starting automated release request for workspace1",
        f"Release request complete for workspace1: {pending_release_request.id}",
    }

    spans = get_trace()
    assert len(spans) == 2

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 3
    # Exceptions are recorded on spans
    # request_id is not recorded as no new requests have been successfully created
    # (A new empty request may have been created, if all files have already been
    # released)
    new_empty_request = bll.get_current_request("workspace", author)
    assert spans[0].attributes == {
        "workspace_name": "workspace",
        "username": "author",
        "dirs": ("test-dir",),
        "result.request_id": new_empty_request.id,
        "result.completed": False,
        "result.message": "Already released",
    }
    assert spans[1].attributes == {
        "workspace_name": "workspace1",
        "username": "author",
        "dirs": ("test-dir",),
        "submit": True,
        "result.request_id": pending_release_request.id,
        "result.completed": True,
        "result.message": "Success",
    }


@pytest.mark.django_db
def test_weekly_runjobs_validation_error(bll, caplog):
    caplog.set_level(logging.INFO)
    with patch(
        "airlock.jobs.weekly.create_regular_release_requests.CONFIG_PATH",
        FIXTURE_DIR / "regular_release_requests_bad_config.json",
    ):
        call_command("runjobs", "weekly")

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.jobs.weekly.create_regular_release_requests"
    ]
    assert len(logs) == 2
    assert {log.message for log in logs} == {
        "Starting automated release request for workspace",
        "Failed to create release request for workspace - Invalid config type for 'dirs': expected <class 'list'>, got <class 'str'>",
    }

    spans = get_trace()
    assert len(spans) == 1

    # The exceptions are recorded on each span
    for span in spans:
        assert span.name == "create_regular_release_requests"
        assert span.events[0].name == "exception"

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
    assert len(logs) == 2
    assert {log.message for log in logs} == {
        "Release request created for workspace",
        "Release request created for workspace1",
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
    }
    assert spans[1].attributes == {
        "workspace_name": "workspace1",
        "username": "author",
        "dirs": ("test-dir",),
        "submit": True,
    }


@pytest.mark.django_db
def test_weekly_runjobs_error(bll, mock_old_api, mock_config, caplog):
    caplog.set_level(logging.INFO)
    author = User.objects.get(user_id="author")
    # create submitted release request for the first workspace
    factories.create_request_at_status(
        author=author,
        workspace="workspace",
        status=RequestStatus.SUBMITTED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir/file1.txt", approved=True
            )
        ],
    )

    # create released release request for the second workspace
    factories.create_request_at_status(
        author=author,
        workspace="workspace1",
        status=RequestStatus.RELEASED,
        files=[
            factories.request_file(
                group="test-dir", path="test-dir/file2.txt", approved=True
            )
        ],
    )

    call_command("runjobs", "weekly")

    logs = [
        record
        for record in caplog.records
        if record.name == "airlock.jobs.weekly.create_regular_release_requests"
    ]
    assert len(logs) == 2
    assert {log.message for log in logs} == {
        "Failed to create release request for workspace - cannot edit request that is in state SUBMITTED",
        "Failed to create release request for workspace1 - No output files on request; 1 file(s) already released, 0 file(s) with errors",
    }

    spans = get_trace()
    assert len(spans) == 2

    # The exceptions are recorded on each span
    for span in spans:
        assert span.name == "create_regular_release_requests"
        assert span.events[0].name == "exception"


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
    assert len(logs) == 1
    assert {log.message for log in logs} == {
        "Failed to create release request for workspace - Invalid config type for 'dirs': expected <class 'list'>, got <class 'str'>",
    }

    spans = get_trace()
    assert len(spans) == 1

    # The exceptions are recorded on each span
    for span in spans:
        assert span.name == "create_regular_release_requests"
        assert span.events[0].name == "exception"

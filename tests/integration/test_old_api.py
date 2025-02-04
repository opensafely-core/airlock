import logging
from pathlib import Path

import pytest
import requests
from django.conf import settings

import old_api
from tests import factories


pytestmark = pytest.mark.django_db


def test_old_api_get_or_create_release(responses):
    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/workspace_name",
        status=201,
        headers={"Release-Id": "jobserver-id"},
    )

    assert (
        old_api.get_or_create_release(
            "workspace_name", "jobserver-id", {"airlock_id": "jobserver-id"}, "testuser"
        )
        == "jobserver-id"
    )

    request = responses.calls[0].request
    assert request.headers["OS-User"] == "testuser"
    assert request.body == "airlock_id=jobserver-id"


def test_old_api_get_or_create_release_with_error(responses, caplog):
    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/workspace_name",
        status=403,
        headers={"Release-Id": "jobserver-id"},
        body="job-server error",
    )
    with pytest.raises(requests.exceptions.HTTPError):
        old_api.get_or_create_release(
            "workspace_name", "jobserver-id", {"airlock_id": "jobserver-id"}, "testuser"
        )
    assert len(caplog.messages) == 1
    log = caplog.messages[0]
    assert "Error creating release" in log
    assert "job-server error" in log


def test_old_api_upload_file(responses):
    release_request = factories.create_release_request("workspace")
    relpath = Path("test/file.txt")
    release_request = factories.add_request_file(
        release_request, "group", relpath, "test"
    )
    abspath = release_request.abspath("group" / relpath)

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/release-id",
        status=201,
    )

    old_api.upload_file("release-id", "workspace", relpath, abspath, "testuser")
    request = responses.calls[0].request
    assert request.body == b"test"
    assert request.headers["Content-Disposition"] == f'attachment; filename="{relpath}"'
    assert request.headers["OS-User"] == "testuser"


def test_old_api_upload_file_error(responses, caplog):
    release_request = factories.create_release_request("workspace")
    relpath = Path("test/file.txt")
    release_request = factories.add_request_file(
        release_request, "group", relpath, "test"
    )
    abspath = release_request.abspath("group" / relpath)

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/release-id",
        status=400,
        json={"detail": "job-server error"},
    )
    uploaded, error = old_api.upload_file(
        "release-id", "workspace", relpath, abspath, "testuser"
    )
    assert not uploaded
    assert len(caplog.messages) == 1
    log = caplog.messages[0]
    assert "Error uploading file" in log
    assert "job-server error" in log
    assert error == "job-server error"


def test_old_api_upload_file_already_uploaded_error(responses, caplog):
    caplog.set_level(logging.INFO)
    release_request = factories.create_release_request("workspace")
    relpath = Path("test/file.txt")
    release_request = factories.add_request_file(
        release_request, "group", relpath, "test"
    )
    abspath = release_request.abspath("group" / relpath)

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/release-id",
        status=400,
        json={"detail": f"This version of '{relpath}' has already been uploaded"},
    )
    old_api.upload_file("release-id", "workspace", relpath, abspath, "testuser")

    assert len(caplog.messages) == 1
    log = caplog.messages[0]
    assert "File already uploaded" in log

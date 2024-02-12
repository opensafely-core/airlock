from pathlib import Path

from django.conf import settings

import old_api
from tests import factories


def test_old_api_create_release(responses):
    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/workspace/workspace_name",
        status=201,
        headers={"Release-Id": "jobserver-id"},
    )

    assert (
        old_api.create_release("workspace_name", "json string", "testuser")
        == "jobserver-id"
    )

    request = responses.calls[0].request
    assert request.headers["OS-User"] == "testuser"
    assert request.body == "json string"


def test_old_api_upload_file(responses):
    release_request = factories.create_request("workspace", request_id="request-id")
    relpath = Path("test/file.txt")
    abspath = release_request.root() / relpath
    factories.write_request_file(release_request, relpath, "test")

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/release-id",
        status=201,
    )

    old_api.upload_file("release-id", relpath, abspath, "testuser")
    request = responses.calls[0].request
    assert request.body.read() == b"test"
    assert request.headers["Content-Disposition"] == f'attachment; filename="{relpath}"'
    assert request.headers["OS-User"] == "testuser"

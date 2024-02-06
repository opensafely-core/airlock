from django.conf import settings

import old_api
from tests.factories import WorkspaceFactory


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
    rf = WorkspaceFactory("workspace").create_request("request-id")
    rf.write_file("test/file.txt", "test")
    item = rf.get().get_path("test/file.txt")

    responses.post(
        f"{settings.AIRLOCK_API_ENDPOINT}/releases/release/release-id",
        status=201,
    )

    old_api.upload_file("release-id", item.relpath, item._absolute_path(), "testuser")
    request = responses.calls[0].request
    assert request.body.read() == b"test"
    assert (
        request.headers["Content-Disposition"]
        == f'attachment; filename="{item.relpath}"'
    )
    assert request.headers["OS-User"] == "testuser"

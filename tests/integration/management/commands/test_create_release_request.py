import pytest
from django.core.management import call_command

from tests import factories


@pytest.mark.django_db
def test_create_release_request(bll):
    """
    Test that we can create a release request using the management command.
    """
    workspace = factories.create_workspace("workspace")
    author = factories.create_airlock_user(username="author", workspaces=["workspace"])

    assert not bll.get_requests_authored_by_user(author)

    factories.write_workspace_file(workspace, "test-dir/file1.txt", contents="file1")

    call_command(
        "create_release_request",
        "author",
        "workspace",
        dirs=["test-dir"],
    )

    release_requests = bll.get_requests_authored_by_user(author)
    assert len(release_requests) == 1

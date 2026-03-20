import json

import pytest

from airlock import exceptions, permissions
from airlock.enums import RequestFileType, RequestStatus, WorkspaceFileStatus
from airlock.types import UrlPath
from tests import factories


@pytest.mark.parametrize(
    "output_checker,workspaces,copiloted_workspaces,can_view",
    [
        (True, {}, {}, True),
        (True, {"other": {}, "other1": {}}, {}, True),
        (True, {"other": {}, "other1": {}}, {"test1": {}}, True),
        (True, {"other": {}, "other1": {}}, {"test": {}}, True),
        (False, {"test": {}, "other": {}, "other1": {}}, {}, True),
        (False, {"other": {}, "other1": {}}, {}, False),
        (False, {"other": {}, "other1": {}}, {"test1": {}}, False),
        (False, {"other": {}, "other1": {}}, {"test": {}}, True),
    ],
)
def test_user_can_view_workspace(
    output_checker, workspaces, copiloted_workspaces, can_view
):
    user = factories.create_airlock_user(
        username="test",
        workspaces=workspaces,
        copiloted_workspaces=copiloted_workspaces,
        output_checker=output_checker,
    )
    assert permissions.user_can_view_workspace(user, "test") == can_view

    if not can_view:
        with pytest.raises(exceptions.WorkspacePermissionDenied):
            permissions.check_user_can_view_workspace(user, "test")


def test_user_can_view_workspace_no_user():
    assert not permissions.user_can_view_workspace(None, "test")


@pytest.mark.parametrize(
    "output_checker,workspaces,has_role",
    [
        (True, {}, False),
        (True, {"other": {}, "other1": {}}, False),
        (False, {"test": {}, "other": {}, "other1": {}}, True),
        (False, {"other": {}, "other1": {}}, False),
    ],
)
def test_user_has_role_on_workspace(output_checker, workspaces, has_role):
    user = factories.create_airlock_user(
        username="test", workspaces=workspaces, output_checker=output_checker
    )
    assert permissions.user_has_role_on_workspace(user, "test") == has_role

    if not has_role:
        with pytest.raises(exceptions.RequestPermissionDenied):
            permissions.check_user_has_role_on_workspace(user, "test")


def _details(archived=False, ongoing=True):
    return factories.create_api_workspace(
        project="Project", archived=archived, ongoing=ongoing
    )


@pytest.mark.parametrize(
    "output_checker,workspaces,can_action_request,expected_reason",
    [
        (True, {}, False, "do not have permission"),
        (
            True,
            {"other": _details(), "other1": _details()},
            False,
            "do not have permission",
        ),
        (
            False,
            {"test": _details(), "other": _details(), "other1": _details()},
            True,
            None,
        ),
        (
            False,
            {"other": _details(), "other1": _details()},
            False,
            "do not have permission",
        ),
        (
            False,
            {"test": _details(archived=True)},
            False,
            "has been archived",
        ),
        (
            False,
            {"test": _details(ongoing=False)},
            False,
            "inactive project",
        ),
        (
            False,
            {"test": _details(archived=True, ongoing=False)},
            False,
            "has been archived",
        ),
    ],
)
def test_session_user_can_action_request(
    output_checker, workspaces, can_action_request, expected_reason
):
    user = factories.create_airlock_user(
        username="test", workspaces=workspaces, output_checker=output_checker
    )
    assert (
        permissions.user_can_action_request_for_workspace(user, "test")
        == can_action_request
    )
    if not can_action_request:
        with pytest.raises(exceptions.RequestPermissionDenied, match=expected_reason):
            assert permissions.check_user_can_action_request_for_workspace(user, "test")


@pytest.mark.parametrize(
    "output_checker,author,workspaces,can_review",
    [
        # output checker with no access to workspace can review
        (True, "other", [], True),
        # output checker who is also author cannot review
        (True, "user", ["test"], False),
        # non-output-checker cannot review
        (False, "other", ["test"], False),
    ],
)
def test_user_can_review_request(output_checker, author, workspaces, can_review):
    user = factories.create_airlock_user(
        username="user", workspaces=workspaces, output_checker=output_checker
    )
    users = {
        "user": user,
        "other": factories.create_airlock_user(
            username="other", workspaces=["test"], output_checker=False
        ),
    }
    release_request = factories.create_request_at_status(
        "test",
        RequestStatus.SUBMITTED,
        author=users[author],
        files=[factories.request_file()],
    )
    assert permissions.user_can_review_request(user, release_request) == can_review

    if not can_review:
        with pytest.raises(exceptions.RequestPermissionDenied):
            permissions.check_user_can_review_request(user, release_request)


def test_user_can_change_request_file_properties(bll):
    author = factories.create_airlock_user(
        username="author", workspaces=["workspace"], output_checker=False
    )
    relpath = UrlPath("path/file.txt")
    workspace = factories.create_workspace("workspace")
    factories.write_workspace_file(workspace, relpath)
    release_request = factories.create_request_at_status(
        "workspace",
        author=author,
        status=RequestStatus.RETURNED,
        files=[
            factories.request_file(
                path=relpath,
                group="group",
                filetype=RequestFileType.OUTPUT,
                approved=True,
            )
        ],
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)

    # File properties can be changed
    assert permissions.user_can_change_request_file_properties(
        author, release_request, workspace, relpath, RequestFileType.OUTPUT
    )
    # File cannot be updated because its content hasn't changed
    assert not permissions.user_can_update_file_on_request(
        author, release_request, workspace, relpath
    )

    # change file content in workspace
    factories.write_workspace_file(workspace, relpath, contents="changed")
    assert (
        workspace.get_workspace_file_status(relpath)
        == WorkspaceFileStatus.CONTENT_UPDATED
    )

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)

    # File properties can be changed
    assert permissions.user_can_change_request_file_properties(
        author, release_request, workspace, relpath, RequestFileType.OUTPUT
    )
    # File can be updated because its content has changed
    assert permissions.user_can_update_file_on_request(
        author, release_request, workspace, relpath
    )

    # Make file an invalid workspace file by removing it from manifest.json
    manifest_path = workspace.manifest_path()
    manifest = json.loads(manifest_path.read_text())
    del manifest["outputs"]["path/file.txt"]
    manifest_path.write_text(json.dumps(manifest))

    # refresh workspace
    workspace = bll.get_workspace("workspace", author)

    # File properties can be changed
    assert permissions.user_can_change_request_file_properties(
        author, release_request, workspace, relpath, RequestFileType.OUTPUT
    )
    # File cannot be updated because there is no valid workspace file
    assert not permissions.user_can_update_file_on_request(
        author, release_request, workspace, relpath
    )

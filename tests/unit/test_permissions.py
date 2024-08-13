import pytest

from airlock import exceptions, permissions
from airlock.enums import RequestStatus
from tests import factories


@pytest.mark.parametrize(
    "output_checker,workspaces,can_view",
    [
        (True, {}, True),
        (True, {"other": {}, "other1": {}}, True),
        (False, {"test": {}, "other": {}, "other1": {}}, True),
        (False, {"other": {}, "other1": {}}, False),
    ],
)
def test_user_can_view_workspace(output_checker, workspaces, can_view):
    user = factories.create_user("test", workspaces, output_checker=output_checker)
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
    user = factories.create_user("test", workspaces, output_checker=output_checker)
    assert permissions.user_has_role_on_workspace(user, "test") == has_role

    if not has_role:
        with pytest.raises(exceptions.RequestPermissionDenied):
            permissions.check_user_has_role_on_workspace(user, "test")


def _details(archived=False, ongoing=True):
    return {
        "project_details": {"name": "Project", "ongoing": ongoing},
        "archived": archived,
    }


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
    user = factories.create_user("test", workspaces, output_checker=output_checker)
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
    user = factories.create_user("user", workspaces, output_checker=output_checker)
    users = {
        "user": user,
        "other": factories.create_user("other", ["test"], output_checker=False),
    }
    release_request = factories.create_request_at_status(
        "test", RequestStatus.SUBMITTED, author=users[author]
    )
    assert permissions.user_can_review_request(user, release_request) == can_review

    if not can_review:
        with pytest.raises(exceptions.RequestPermissionDenied):
            permissions.check_user_can_review_request(user, release_request)

from typing import TYPE_CHECKING

from airlock import exceptions
from airlock.users import User


if TYPE_CHECKING:  # pragma: no cover
    # We are avoiding circular dependencies by using forward references for
    # type annotations where necessary, and this check so that type-checkin
    # imports are not executed at runtime.
    # https://peps.python.org/pep-0484/#forward-references
    # https://mypy.readthedocs.io/en/stable/runtime_troubles.html#import-cycles`
    from airlock.business_logic import ReleaseRequest


def check_user_can_view_workspace(user: User | None, workspace_name: str):
    """
    This user can access and view the workspace and requests for it.
    Output checkers can view all workspaces.
    Authors can view all workspaces they have access to (regardless
    of the workspace's archive or project ongoing status)
    """
    if user is None or (
        not user.output_checker and workspace_name not in user.workspaces
    ):
        raise exceptions.WorkspacePermissionDenied(
            f"you do not have permission to view {workspace_name} or its requests"
        )


def user_can_view_workspace(user: User | None, workspace_name: str):
    try:
        return check_user_can_view_workspace(user, workspace_name) is None
    except exceptions.WorkspacePermissionDenied:
        return False


def check_user_has_role_on_workspace(user: User | None, workspace_name: str):
    """
    This user has an explicit role on the workspace and has permission
    to create or modify requests.
    """
    if user is None or workspace_name not in user.workspaces:
        raise exceptions.RequestPermissionDenied(
            f"you do not have permission to author requests for {workspace_name}"
        )


def user_has_role_on_workspace(user: User | None, workspace_name: str):
    try:
        return check_user_has_role_on_workspace(user, workspace_name) is None
    except exceptions.RequestPermissionDenied:
        return False


def check_user_can_action_request_for_workspace(user: User | None, workspace_name: str):
    """
    This user has permission to create or modify requests
    AND the workspace is active.
    """
    check_user_has_role_on_workspace(user, workspace_name)
    assert user is not None
    if user.workspaces[workspace_name]["archived"]:
        raise exceptions.RequestPermissionDenied(f"{workspace_name} has been archived")
    if not user.workspaces[workspace_name]["project_details"]["ongoing"]:
        raise exceptions.RequestPermissionDenied(
            f"{workspace_name} is part of an inactive project"
        )


def user_can_action_request_for_workspace(user: User | None, workspace_name: str):
    try:
        return check_user_can_action_request_for_workspace(user, workspace_name) is None
    except exceptions.RequestPermissionDenied:
        return False


def check_user_can_review_request(user: User, request: "ReleaseRequest"):
    """
    This user can be a reviewer for the request.
    """
    if not (user.output_checker and request.author != user.username):
        raise exceptions.RequestPermissionDenied(
            "You do not have permission to review this request"
        )


def user_can_review_request(user: User, request: "ReleaseRequest"):
    try:
        return check_user_can_review_request(user, request) is None
    except exceptions.RequestPermissionDenied:
        return False

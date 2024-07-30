from pathlib import Path
from typing import TYPE_CHECKING

from airlock import exceptions
from airlock.types import UrlPath
from airlock.users import User
from airlock.utils import is_valid_file_type


if TYPE_CHECKING:  # pragma: no cover
    # We are avoiding circular dependencies by using forward references for
    # type annotations where necessary, and this check so that type-checkin
    # imports are not executed at runtime.
    # https://peps.python.org/pep-0484/#forward-references
    # https://mypy.readthedocs.io/en/stable/runtime_troubles.html#import-cycles`
    from airlock.business_logic import ReleaseRequest, Workspace


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


def check_user_can_review(user: User):
    """This user can be a reviewer"""
    if not user.output_checker:
        raise exceptions.RequestPermissionDenied("Only ouput-checkers allowed")


def user_can_review(user: User):
    try:
        return check_user_can_review(user) is None
    except exceptions.RequestPermissionDenied:
        return False


def check_user_can_review_request(user: User, request: "ReleaseRequest"):
    """This user can be a reviewer for a specific request"""
    if not (user_can_review(user) and request.author != user.username):
        raise exceptions.RequestPermissionDenied(
            "You do not have permission to review this request"
        )


def user_can_review_request(user: User, request: "ReleaseRequest"):
    try:
        return check_user_can_review_request(user, request) is None
    except exceptions.RequestPermissionDenied:
        return False


def check_user_can_edit_request(user: User, request: "ReleaseRequest"):
    """
    This user has permission to edit the request, AND the request is in an
    editable state
    """
    if user.username != request.author:
        raise exceptions.RequestPermissionDenied(
            f"only author {request.author} can modify the files in this request"
        )
    if not request.is_editing():
        raise exceptions.RequestPermissionDenied(
            f"cannot modify files in request that is in state {request.status.name}"
        )
    check_user_can_action_request_for_workspace(user, request.workspace)


def user_can_edit_request(user: User, request: "ReleaseRequest"):
    try:
        return check_user_can_edit_request(user, request) is None
    except exceptions.RequestPermissionDenied:
        return False


def check_user_can_add_file_to_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):
    check_user_can_edit_request(user, request)

    if not is_valid_file_type(Path(relpath)):
        raise exceptions.RequestPermissionDenied(
            f"Cannot add file of type {relpath.suffix} to request"
        )

    if workspace.file_has_been_released(relpath):
        raise exceptions.RequestPermissionDenied("Cannot add released file to request")


def user_can_add_file_to_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        return (
            check_user_can_add_file_to_request(user, request, workspace, relpath)
            is None
        )
    except exceptions.RequestPermissionDenied:
        return False


def check_user_can_update_file_on_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):
    check_user_can_edit_request(user, request)

    if not is_valid_file_type(Path(relpath)):
        raise exceptions.RequestPermissionDenied(
            f"Cannot update file of type {relpath.suffix} in request"
        )

    if not workspace.file_can_be_updated(relpath):
        raise exceptions.RequestPermissionDenied(
            "Cannot update file in request if it is not updated on disk"
        )


def user_can_update_file_on_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        return (
            check_user_can_update_file_on_request(user, request, workspace, relpath)
            is None
        )
    except exceptions.RequestPermissionDenied:
        return False

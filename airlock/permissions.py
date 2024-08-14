"""
Check if a user has permission to perfom an action.
"""

from typing import TYPE_CHECKING

from airlock import exceptions, policies
from airlock.types import UrlPath
from airlock.users import User


if TYPE_CHECKING:  # pragma: no cover
    # We are avoiding circular dependencies by using forward references for
    # type annotations where necessary, and this check so that type-checkin
    # imports are not executed at runtime.
    # https://peps.python.org/pep-0484/#forward-references
    # https://mypy.readthedocs.io/en/stable/runtime_troubles.html#import-cycles`
    from airlock.business_logic import Comment, ReleaseRequest, Workspace


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
        check_user_can_view_workspace(user, workspace_name)
    except exceptions.WorkspacePermissionDenied:
        return False
    return True


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
        check_user_has_role_on_workspace(user, workspace_name)
    except exceptions.RequestPermissionDenied:
        return False
    return True


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
        check_user_can_action_request_for_workspace(user, workspace_name)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_review(user: User):
    """This user can be a reviewer"""
    if not user.output_checker:
        raise exceptions.RequestPermissionDenied("Only ouput-checkers allowed")


def user_can_review(user: User):
    try:
        check_user_can_review(user)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_review_request(user: User, request: "ReleaseRequest"):
    """This user can be a reviewer for a specific request"""
    if not (user_can_review(user) and request.author != user.username):
        raise exceptions.RequestPermissionDenied(
            "You do not have permission to review this request"
        )


def user_can_review_request(user: User, request: "ReleaseRequest"):
    try:
        check_user_can_review_request(user, request)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_edit_request(user: User, request: "ReleaseRequest"):
    """
    This user has permission to edit the request, AND the request is in an
    editable state
    """
    if user.username != request.author:
        raise exceptions.RequestPermissionDenied(
            f"only author {request.author} can edit this request"
        )
    check_user_can_action_request_for_workspace(user, request.workspace)
    policies.check_can_edit_request(request)


def user_can_edit_request(user: User, request: "ReleaseRequest"):
    try:
        check_user_can_edit_request(user, request)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_add_file_to_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):
    check_user_can_edit_request(user, request)
    policies.check_can_add_file_to_request(workspace, relpath)


def user_can_add_file_to_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        check_user_can_add_file_to_request(user, request, workspace, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_update_file_on_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):
    check_user_can_edit_request(user, request)
    policies.check_can_update_file_on_request(workspace, relpath)


def user_can_update_file_on_request(
    user: User, request: "ReleaseRequest", workspace: "Workspace", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        check_user_can_update_file_on_request(user, request, workspace, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_withdraw_file_from_request(
    user: User, request: "ReleaseRequest", relpath: UrlPath
):
    if not user_can_edit_request(user, request):
        raise exceptions.RequestPermissionDenied(
            f"Cannot withdraw file {relpath} from request"
        )


def user_can_withdraw_file_from_request(
    user: User, request: "ReleaseRequest", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        check_user_can_withdraw_file_from_request(user, request, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_review_file(user: User, request: "ReleaseRequest", relpath: UrlPath):
    try:
        check_user_can_review_request(user, request)
    except exceptions.RequestPermissionDenied as exc:
        raise exceptions.RequestReviewDenied(str(exc))
    policies.check_can_review_file_on_request(request, relpath)


def user_can_review_file(
    user: User, request: "ReleaseRequest", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        check_user_can_review_file(user, request, relpath)
    except exceptions.RequestReviewDenied:
        return False
    return True


def check_user_can_reset_file_review(
    user: User, request: "ReleaseRequest", relpath: UrlPath
):
    check_user_can_review_file(user, request, relpath)
    if user.username in request.submitted_reviews:
        raise exceptions.RequestReviewDenied("cannot reset file from submitted review")


def user_can_reset_file_review(
    user: User, request: "ReleaseRequest", relpath: UrlPath
):  # pragma: no cover; not currently used
    try:
        check_user_can_reset_file_review(user, request, relpath)
    except exceptions.RequestReviewDenied:
        return False
    return True


def check_user_can_submit_review(user: User, request: "ReleaseRequest"):
    policies.check_can_review_request(request)
    check_user_can_review_request(user, request)
    if not request.all_files_reviewed_by_reviewer(user):
        raise exceptions.RequestReviewDenied(
            "You must review all files to submit your review"
        )

    if user.username in request.submitted_reviews:
        raise exceptions.RequestReviewDenied(
            "You have already submitted your review of this request"
        )


def user_can_submit_review(
    user: User, request: "ReleaseRequest"
):  # pragma: no cover; not currently used
    try:
        check_user_can_submit_review(user, request)
    except exceptions.RequestReviewDenied:
        return False
    return True


def check_user_can_comment_on_group(user: User, request: "ReleaseRequest"):
    # Users with no permission to view workspace can never comment
    if not user_can_view_workspace(user, request.workspace):
        raise exceptions.RequestPermissionDenied(
            f"User {user.username} does not have permission to comment"
        )

    if not user_has_role_on_workspace(user, request.workspace):
        # if user does not have a role on the workspace, they are an output-checker
        # and can only comment if the request is in under review status
        try:
            policies.check_can_review_request(request)
        except exceptions.RequestReviewDenied:
            raise exceptions.RequestPermissionDenied(
                f"User {user.username} does not have permission to comment on request in {request.status.name} status"
            )
    else:
        # if user has a role on the workspace (even if not the author), they are allowed
        # to comment on behalf of the author.
        if not user_can_review_request(user, request):
            # non-output-checkers and author can only comment in editable status
            policies.check_can_edit_request(request)
        else:
            # output-checkers who have access to the workspace can comment in both
            # editable and reviewable status, as they could be commenting as either
            # checker or collaborator
            policies.check_can_modify_request(request)


def user_can_comment_on_group(user: User, request: "ReleaseRequest"):
    try:
        check_user_can_comment_on_group(user, request)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_user_can_delete_comment(
    user: User, request: "ReleaseRequest", comment: "Comment"
):
    # Only the author of a comment can delete it
    if not user.username == comment.author:
        raise exceptions.RequestPermissionDenied(
            f"User {user.username} is not the author of this comment, so cannot delete"
        )
    # Restrictions on deleting comments are the same as for creating them. This
    # means that comments can't be changed once a user has completed their turn, and
    # comments can't be deleted at all after a request has moved into a final state
    check_user_can_comment_on_group(user, request)


def user_can_delete_comment(
    user: User, request: "ReleaseRequest", comment: "Comment"
):  # pragma: no cover; not currently used
    try:
        check_user_can_delete_comment(user, request, comment)
    except exceptions.RequestPermissionDenied:
        return False
    return True

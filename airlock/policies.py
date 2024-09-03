"""
Check if a user is allowed to perform a particular action on a request or
request file. This is dependent on our policies regarding changes that can
be made:
- while requests are in particular statuses
- for types of file
- at a certain phase in a request turn

For example, a user has permission to review a particular request and vote on
a file if they are an output checker and they are not the author of this request.
However, in addition, the file must be of type OUTPUT, and the request must be
in an editing status (submitted/partially reviewed/reviewed)
"""

from pathlib import Path
from typing import TYPE_CHECKING

from airlock import exceptions
from airlock.enums import (
    RequestFileVote,
    RequestStatus,
    WorkspaceFileStatus,
)
from airlock.types import UrlPath
from airlock.utils import is_valid_file_type


if TYPE_CHECKING:  # pragma: no cover
    # We are avoiding circular dependencies by using forward references for
    # type annotations where necessary, and this check so that type-checkin
    # imports are not executed at runtime.
    # https://peps.python.org/pep-0484/#forward-references
    # https://mypy.readthedocs.io/en/stable/runtime_troubles.html#import-cycles`
    from airlock.models import (
        Comment,
        FileReview,
        ReleaseRequest,
        Workspace,
    )


def check_can_edit_request(request: "ReleaseRequest"):
    """
    This request is in an editable state
    """
    if not request.is_editing():
        raise exceptions.RequestPermissionDenied(
            f"cannot edit request that is in state {request.status.name}"
        )


def can_add_file_to_request(workspace: "Workspace", relpath: UrlPath):
    try:
        check_can_add_file_to_request(workspace, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_can_add_file_to_request(workspace: "Workspace", relpath: UrlPath):
    """
    This file can be added to the request.
    We expect that check_can_edit_request has already been called.
    """
    # The file is an allowed type
    if not is_valid_file_type(Path(relpath)):
        raise exceptions.RequestPermissionDenied(
            f"Cannot add file of type {relpath.suffix} to request"
        )

    status = workspace.get_workspace_file_status(relpath)

    # The file hasn't already been released
    if status == WorkspaceFileStatus.RELEASED:
        raise exceptions.RequestPermissionDenied("Cannot add released file to request")

    if status not in [
        WorkspaceFileStatus.UNRELEASED,
        WorkspaceFileStatus.WITHDRAWN,
    ]:
        raise exceptions.RequestPermissionDenied(
            f"Cannot add file to request if it is in status {status}"
        )


def can_replace_file_in_request(workspace: "Workspace", relpath: UrlPath):
    try:
        check_can_replace_file_in_request(workspace, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_can_replace_file_in_request(workspace: "Workspace", relpath: UrlPath):
    """
    This file can replace an existing file in the request, which currently happens
    in two scenarios:
    * when a file on a request is updated;
    * or when a file on a request in the withdrawn state is re-added.
    We expect that check_can_edit_request has already been called.
    """
    # The file is an allowed type
    if not is_valid_file_type(Path(relpath)):
        raise exceptions.RequestPermissionDenied(
            f"Cannot add file of type {relpath.suffix} to request"
        )

    status = workspace.get_workspace_file_status(relpath)

    # The file hasn't already been released
    if status == WorkspaceFileStatus.RELEASED:
        raise exceptions.RequestPermissionDenied("Cannot add released file to request")

    if status not in [
        WorkspaceFileStatus.WITHDRAWN,
        WorkspaceFileStatus.CONTENT_UPDATED,
    ]:
        status = workspace.get_workspace_file_status(relpath)
        raise exceptions.RequestPermissionDenied(
            f"Cannot add or update file in request if it is in status {status}"
        )


def can_update_file_on_request(workspace: "Workspace", relpath: UrlPath):
    try:
        check_can_update_file_on_request(workspace, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_can_update_file_on_request(workspace: "Workspace", relpath: UrlPath):
    """
    This file can be updated on the request.
    We expect that check_can_edit_request has already been called.
    """
    if not is_valid_file_type(Path(relpath)):
        raise exceptions.RequestPermissionDenied(
            f"Cannot update file of type {relpath.suffix} in request"
        )

    status = workspace.get_workspace_file_status(relpath)

    if status != WorkspaceFileStatus.CONTENT_UPDATED:
        raise exceptions.RequestPermissionDenied(
            "Cannot update file in request if it is not updated on disk"
        )


def check_can_withdraw_file_from_request(workspace: "Workspace", relpath: UrlPath):
    """
    This file is withdrawable; i.e. it has not already been withdrawn
    """
    status = workspace.get_workspace_file_status(relpath)
    if status == WorkspaceFileStatus.WITHDRAWN:
        raise exceptions.RequestPermissionDenied(
            "file has already been withdrawn from request"
        )


def check_can_review_request(request: "ReleaseRequest"):
    """
    This request is in a reviewable state
    """
    if not request.is_under_review():
        raise exceptions.RequestReviewDenied(
            f"cannot review request in state {request.status.name}"
        )


def check_can_review_file_on_request(request: "ReleaseRequest", relpath: UrlPath):
    """
    This file is reviewable; i.e. it is on the request, and it is an output file.
    """
    check_can_review_request(request)
    if relpath not in request.output_files():
        raise exceptions.RequestReviewDenied(
            "file is not an output file on this request"
        )


def check_can_mark_file_undecided(request: "ReleaseRequest", review: "FileReview"):
    if request.status != RequestStatus.RETURNED:
        raise exceptions.RequestReviewDenied(
            f"cannot change file review to {RequestFileVote.UNDECIDED.name} from request in state {request.status.name}"
        )

    if review.status != RequestFileVote.CHANGES_REQUESTED:
        raise exceptions.RequestReviewDenied(
            f"cannot change file review from {review.status.name} to {RequestFileVote.UNDECIDED.name} from request in state {request.status.name}"
        )


def check_can_modify_request(request: "ReleaseRequest"):
    """
    This request can be modified i.e. it is in, or can be moved to, a
    status in which files can be added/withdrawn/reviewed.
    These are statuses that are considered "final" and system-owned,
    plus the APPROVED status, in which the only allowed modification
    is to move the request to RELEASED.
    """
    if request.is_final() or request.status == RequestStatus.APPROVED:
        raise exceptions.RequestPermissionDenied(
            "This request can no longer be modified."
        )


def check_can_make_comment_publicly_visible(
    request: "ReleaseRequest", comment: "Comment"
):
    if not request.review_turn == comment.review_turn:
        raise exceptions.RequestPermissionDenied(
            "Comments visibility cannot be changed after a round finishes"
        )

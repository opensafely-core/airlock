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
    RequestFileType,
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


def can_replace_file_in_request(
    workspace: "Workspace",
    relpath: UrlPath,
    filegroup: str | None = None,
    filetype: RequestFileType | None = None,
):
    try:
        check_can_replace_file_in_request(workspace, relpath, filegroup, filetype)
    except exceptions.RequestPermissionDenied:
        return False
    return True


def check_can_replace_file_in_request(
    workspace: "Workspace",
    relpath: UrlPath,
    filegroup: str | None = None,
    filetype: RequestFileType | None = None,
):
    """
    This file can replace an existing file in the request, which currently happens
    in 4 scenarios:
    * when a file on a request is updated;
    * or when a file on a request in the withdrawn state is re-added.
    * or when a file is moved to a different group
    * or when a file's type is changed
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
        request_file = (
            workspace.current_request.get_request_file_from_output_path(relpath)
            if workspace.current_request
            else None
        )

        # We can replace a file that hasn't been withdrawn/updated if we are changing
        # its filegroup or type
        if (
            request_file is not None
            and (filegroup is None or (request_file.group == filegroup))
            and (filetype is None or (request_file.filetype == filetype))
        ):
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


def check_can_submit_request(request: "ReleaseRequest"):
    check_can_edit_request(request)
    incomplete_groups = [
        f"'{filegroup.name}'"
        for filegroup in request.filegroups.values()
        if filegroup.incomplete()
    ]
    if incomplete_groups:
        groups = ", ".join(incomplete_groups)
        raise exceptions.RequestPermissionDenied(
            f"Incomplete context and/or controls for filegroup(s): {groups}"
        )

    # for resubmissions, any filegroup with changes requested
    # must have a comment this turn
    check_for_missing_filegroup_comments(request)


def check_can_return_request(request: "ReleaseRequest"):
    """
    This request can be returned
    If the requested is being returned from REVIEWED status, it must
    have a public comment in the current turn for each file group
    for which changes have been requested
    """
    check_can_review_request(request)
    check_for_missing_filegroup_comments(request)


def check_for_missing_filegroup_comments(request):
    filegroups_missing_comments = request.filegroups_missing_public_comment()
    if filegroups_missing_comments:
        groups = ", ".join(filegroups_missing_comments)
        raise exceptions.RequestPermissionDenied(
            f"Filegroup(s) are missing comments: {groups}"
        )

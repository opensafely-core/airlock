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
from airlock.enums import WorkspaceFileStatus
from airlock.types import UrlPath
from airlock.utils import is_valid_file_type


if TYPE_CHECKING:  # pragma: no cover
    # We are avoiding circular dependencies by using forward references for
    # type annotations where necessary, and this check so that type-checkin
    # imports are not executed at runtime.
    # https://peps.python.org/pep-0484/#forward-references
    # https://mypy.readthedocs.io/en/stable/runtime_troubles.html#import-cycles`
    from airlock.business_logic import ReleaseRequest, Workspace


def check_can_edit_request(request: "ReleaseRequest"):
    """
    This request is in an editable state
    """
    if not request.is_editing():
        raise exceptions.RequestPermissionDenied(
            f"cannot modify files in request that is in state {request.status.name}"
        )


def can_add_file_to_request(workspace: "Workspace", relpath: UrlPath):
    try:
        check_can_add_file_to_request(workspace, relpath)
    except exceptions.RequestPermissionDenied:
        return False
    return True


VALID_WORKSPACEFILE_STATES_TO_ADD = [
    WorkspaceFileStatus.UNRELEASED,
    WorkspaceFileStatus.CONTENT_UPDATED,
    WorkspaceFileStatus.WITHDRAWN,
]


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
    # The file hasn't already been released
    if workspace.file_has_been_released(relpath):
        raise exceptions.RequestPermissionDenied("Cannot add released file to request")

    workspace_status = workspace.get_workspace_file_status(relpath)
    if workspace_status not in VALID_WORKSPACEFILE_STATES_TO_ADD:
        raise exceptions.RequestPermissionDenied(
            f"Cannot add file in status {workspace_status}"
        )


def check_can_update_file_on_request(workspace: "Workspace", relpath: UrlPath):
    """
    This file can be updated on the request.
    We expect that check_can_edit_request has already been called.
    """
    if not is_valid_file_type(Path(relpath)):
        raise exceptions.RequestPermissionDenied(
            f"Cannot update file of type {relpath.suffix} in request"
        )

    if not workspace.file_can_be_updated(relpath):
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

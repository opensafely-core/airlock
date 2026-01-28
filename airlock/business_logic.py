from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from django.conf import settings
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string

import old_api
from airlock import exceptions, permissions, policies
from airlock.enums import (
    AuditEventType,
    NotificationEventType,
    RequestFileType,
    RequestStatus,
    RequestStatusOwner,
    Visibility,
)
from airlock.models import (
    AuditEvent,
    FileReview,
    ReleaseRequest,
    RequestFile,
    Workspace,
)
from airlock.notifications import send_notification_event
from airlock.types import UrlPath
from airlock.utils import is_valid_file_type
from airlock.visibility import filter_visible_items
from users.models import User


logger = logging.getLogger(__name__)


def store_file(release_request: ReleaseRequest, abspath: Path) -> str:
    # Make a "staging" copy of the file under a temporary name so we know it can't be
    # modified underneath us
    tmp_name = f"{datetime.now():%Y%m%d-%H%M%S}_{secrets.token_hex(8)}.tmp"
    tmp_path = release_request.root() / tmp_name
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(abspath, tmp_path)
    # Rename the staging file to the hash of its contents
    with tmp_path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    tmp_path.rename(release_request.root() / digest)
    return digest


class DataAccessLayerProtocol(Protocol):
    """
    Structural type class for the Data Access Layer

    Implementations aren't obliged to subclass this as long as they implement the
    specified methods, though it may be clearer to do so.
    """

    def get_release_request(self, request_id: str):
        raise NotImplementedError()

    def create_release_request(
        self,
        workspace: str,
        author: User,
        status: RequestStatus,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def get_active_requests_for_workspace_by_user(self, workspace: str, user: User):
        raise NotImplementedError()

    def get_requests_for_workspace(self, workspace: str):
        raise NotImplementedError()

    def get_released_files_for_workspace(self, workspace: str):
        raise NotImplementedError()

    def get_requests_authored_by_user(self, user: User):
        raise NotImplementedError()

    def get_requests_by_status(self, *states: RequestStatus):
        raise NotImplementedError()

    def set_status(self, request_id: str, status: RequestStatus, audit: AuditEvent):
        raise NotImplementedError()

    def record_review(self, request_id: str, reviewer: User):
        raise NotImplementedError()

    def start_new_turn(self, request_id: str):
        raise NotImplementedError()

    def add_file_to_request(
        self,
        request_id: str,
        relpath: UrlPath,
        file_id: str,
        group_name: str,
        filetype: RequestFileType,
        timestamp: int,
        size: int,
        commit: str,
        repo: str,
        job_id: str,
        row_count: int | None,
        col_count: int | None,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def delete_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def release_file(
        self, request_id: str, relpath: UrlPath, user: User, audit: AuditEvent
    ):
        raise NotImplementedError()

    def get_released_files_for_request(self, request_id: str):
        raise NotImplementedError()

    def register_file_upload_attempt(self, request_id: str, relpath: UrlPath):
        raise NotImplementedError()

    def register_file_upload(
        self, request_id: str, relpath: UrlPath, audit: AuditEvent
    ):
        raise NotImplementedError()

    def withdraw_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def approve_file(
        self,
        request_id: str,
        relpath: UrlPath,
        review_turn: int,
        user: User,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def request_changes_to_file(
        self,
        request_id: str,
        relpath: UrlPath,
        review_turn: int,
        user: User,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def reset_review_file(
        self, request_id: str, relpath: UrlPath, user: User, audit: AuditEvent
    ):
        raise NotImplementedError()

    def mark_file_undecided(
        self,
        request_id: str,
        relpath: UrlPath,
        review_turn: int,
        reviewer: User,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def audit_event(self, audit: AuditEvent):
        raise NotImplementedError()

    def get_audit_log(
        self,
        user: str | None = None,
        workspace: str | None = None,
        request: str | None = None,
        group: str | None = None,
        exclude: set[AuditEventType] | None = None,
        size: int | None = None,
    ) -> list[AuditEvent]:
        raise NotImplementedError()

    def hide_audit_events_for_turn(self, request_id: str, review_turn: int):
        raise NotImplementedError()

    def group_edit(
        self,
        request_id: str,
        group: str,
        context: str,
        controls: str,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def group_comment_create(
        self,
        request_id: str,
        group: str,
        comment: str,
        visibility: Visibility,
        review_turn: int,
        user: User,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def group_comment_delete(
        self,
        request_id: str,
        group: str,
        comment_id: str,
        user: User,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def group_comment_visibility_public(
        self,
        request_id: str,
        group: str,
        comment_id: str,
        user: User,
        audit: AuditEvent,
    ):
        raise NotImplementedError()


class BusinessLogicLayer:
    """
    The mechanism via which the rest of the codebase should read and write application
    state. Interacts with a Data Access Layer purely by exchanging simple values
    (dictionaries, strings etc).
    """

    def __init__(self, data_access_layer: DataAccessLayerProtocol):
        self._dal = data_access_layer

    def get_workspace(self, name: str, user: User) -> Workspace:
        """Get a workspace object."""

        permissions.check_user_can_view_workspace(user, name)

        # this is a bit awkward. If the user is an output checker, they may not
        # have the workspace metadata in their User instance, so we provide an
        # empty metadata instance.
        # Currently, the only place this metadata is used is in the workspace
        # index, to group by project, so its mostly fine that its not here.
        #
        # Metadata also contains information about whether the workspace is
        # archived, and whether the project is ongoing (note that a project
        # could be completed/closed and a workspace not archived, so we need
        # to check for both of these states);
        # The metadata is extracted as an attribute on the workspace. It is
        # used in the workspace index, to show/group
        # workspaces by project and project ongoing status, and within that,
        # by archived status. It is also used to prevent release
        # requests being created for archived workspaces or for not-ongoing projects.
        #
        # It will be None for output checkers who don't have explicit access to
        # the workspace; this is OK as they also won't be able to create requests
        # for the workspace, and they only have access to browse the files.
        # Try to get workspace metadata from the user's workspaces first, then
        # their copiloted workspaces. The actual workspace metadata does not differ
        # between users and copilots
        metadata = user.workspaces.get(name, user.copiloted_workspaces.get(name, {}))

        return Workspace.from_directory(
            name,
            metadata=metadata,
            current_request=self.get_current_request(name, user),
            released_files=self.get_released_files_for_workspace(name),
        )

    def _build_workspace_list(self, user, workspaces) -> list[Workspace]:
        valid_workspaces = []
        for workspace_name in workspaces:
            try:
                workspace = self.get_workspace(workspace_name, user)
            except exceptions.WorkspaceNotFound:
                continue

            valid_workspaces.append(workspace)

        return valid_workspaces

    def get_workspaces_for_user(self, user: User) -> list[Workspace]:
        """Get all the local workspace directories that a user has permission for."""

        return self._build_workspace_list(user, user.workspaces)

    def get_copiloted_workspaces_for_user(self, user: User) -> list[Workspace]:
        """Get all the local workspace directories that a user is a copilot for."""
        return self._build_workspace_list(user, user.copiloted_workspaces)

    def get_release_request(self, request_id: str, user: User) -> ReleaseRequest:
        """Get a ReleaseRequest object for an id."""

        release_request = ReleaseRequest.from_dict(
            self._dal.get_release_request(request_id)
        )

        permissions.check_user_can_view_workspace(user, release_request.workspace)
        return release_request

    def get_current_request(self, workspace: str, user: User) -> ReleaseRequest | None:
        """Get the current request for a workspace/user."""
        permissions.check_user_can_view_workspace(user, workspace)

        active_requests = self._dal.get_active_requests_for_workspace_by_user(
            workspace=workspace,
            user=user,
        )

        n = len(active_requests)
        if n == 0:
            return None
        elif n == 1:
            return ReleaseRequest.from_dict(active_requests[0])
        else:
            raise Exception(
                f"Multiple active release requests for user {user} in "
                f"workspace {workspace}"
            )

    def get_or_create_current_request(
        self,
        workspace: str,
        user: User,
        audit_extra: dict[str, str] | None = None,
    ) -> ReleaseRequest:
        """
        Get the current request for a workspace/user, or create a new one if there is
        none.
        """
        # get_current_request will raise exception if user has no permission
        # and is not an output-cheker
        request = self.get_current_request(workspace, user)

        if request is not None:
            return request

        # check if user has permission to create one
        permissions.check_user_can_action_request_for_workspace(user, workspace)

        audit = AuditEvent(
            type=AuditEventType.REQUEST_CREATE,
            user=user,
            workspace=workspace,
            extra=audit_extra or {},
            # for this specific audit, the DAL will set request id once its
            # created, as we do not know it yet
        )
        return ReleaseRequest.from_dict(
            self._dal.create_release_request(
                workspace=workspace,
                author=user,
                status=RequestStatus.PENDING,
                audit=audit,
            )
        )

    def get_requests_for_workspace(
        self, workspace: str, user: User
    ) -> list[ReleaseRequest]:
        """Get all release requests in workspaces a user has access to."""
        permissions.check_user_can_view_workspace(user, workspace)

        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_requests_for_workspace(workspace=workspace)
        ]

    def get_released_files_for_workspace(self, workspace: str):
        return self._dal.get_released_files_for_workspace(workspace=workspace)

    def get_requests_authored_by_user(self, user: User) -> list[ReleaseRequest]:
        """Get all current requests authored by user."""
        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_requests_authored_by_user(user=user)
        ]

    def _get_reviewable_requests_by_status(self, user: User, *statuses: RequestStatus):
        permissions.check_user_can_review(user)
        for attrs in self._dal.get_requests_by_status(*statuses):
            release_request = ReleaseRequest.from_dict(attrs)
            if permissions.user_can_review_request(user, release_request):
                yield release_request

    def get_outstanding_requests_for_review(self, user: User):
        """Get all request that need review."""
        return list(
            self._get_reviewable_requests_by_status(
                user,
                RequestStatus.SUBMITTED,
                RequestStatus.PARTIALLY_REVIEWED,
                RequestStatus.REVIEWED,
            )
        )

    def get_returned_requests(self, user: User):
        """Get all requests that have been returned."""
        return list(
            self._get_reviewable_requests_by_status(user, RequestStatus.RETURNED)
        )

    def get_approved_requests(self, user: User):
        """Get all requests that have been approved but not yet released."""
        return list(
            self._get_reviewable_requests_by_status(user, RequestStatus.APPROVED)
        )

    VALID_STATE_TRANSITIONS = {
        RequestStatus.PENDING: [
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
        ],
        RequestStatus.SUBMITTED: [
            RequestStatus.PARTIALLY_REVIEWED,
            RequestStatus.RETURNED,
        ],
        RequestStatus.PARTIALLY_REVIEWED: [
            RequestStatus.REVIEWED,
            RequestStatus.RETURNED,
        ],
        RequestStatus.REVIEWED: [
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.RETURNED,
        ],
        RequestStatus.RETURNED: [
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
        ],
        RequestStatus.APPROVED: [
            RequestStatus.RELEASED,
        ],
    }

    STATUS_AUDIT_EVENT = {
        RequestStatus.PENDING: AuditEventType.REQUEST_CREATE,
        RequestStatus.SUBMITTED: AuditEventType.REQUEST_SUBMIT,
        RequestStatus.PARTIALLY_REVIEWED: AuditEventType.REQUEST_REVIEW,
        RequestStatus.REVIEWED: AuditEventType.REQUEST_REVIEW,
        RequestStatus.APPROVED: AuditEventType.REQUEST_APPROVE,
        RequestStatus.REJECTED: AuditEventType.REQUEST_REJECT,
        RequestStatus.RETURNED: AuditEventType.REQUEST_RETURN,
        RequestStatus.RELEASED: AuditEventType.REQUEST_RELEASE,
        RequestStatus.WITHDRAWN: AuditEventType.REQUEST_WITHDRAW,
    }

    STATUS_EVENT_NOTIFICATION = {
        RequestStatus.SUBMITTED: NotificationEventType.REQUEST_SUBMITTED,
        RequestStatus.PARTIALLY_REVIEWED: NotificationEventType.REQUEST_PARTIALLY_REVIEWED,
        RequestStatus.REVIEWED: NotificationEventType.REQUEST_REVIEWED,
        RequestStatus.APPROVED: NotificationEventType.REQUEST_APPROVED,
        RequestStatus.REJECTED: NotificationEventType.REQUEST_REJECTED,
        RequestStatus.RETURNED: NotificationEventType.REQUEST_RETURNED,
        RequestStatus.RELEASED: NotificationEventType.REQUEST_RELEASED,
        RequestStatus.WITHDRAWN: NotificationEventType.REQUEST_WITHDRAWN,
    }

    def check_status(
        self, release_request: ReleaseRequest, to_status: RequestStatus, user: User
    ):
        """Check that a given status transtion is valid for this request and this user.

        This can be used to look-before-you-leap before mutating state when
        there's not transaction protection.
        """
        # validate state logic
        valid_transitions = self.VALID_STATE_TRANSITIONS.get(release_request.status, [])

        if to_status not in valid_transitions:
            raise exceptions.InvalidStateTransition(
                f"cannot change status from {release_request.status.name} to {to_status.name}"
            )

        # check permissions
        owner = release_request.status_owner()
        # author transitions
        if owner == RequestStatusOwner.AUTHOR and user != release_request.author:
            raise exceptions.RequestPermissionDenied(
                f"only the request author {release_request.author} can set status from {release_request.status} to {to_status.name}"
            )
        # reviewer transitions
        elif owner == RequestStatusOwner.REVIEWER or (
            # APPROVED and REJECTED cannot be edited by any user, but can be
            # moved to valid state transitions by a reviewer
            owner == RequestStatusOwner.SYSTEM
            and release_request.status
            in [RequestStatus.APPROVED, RequestStatus.REJECTED]
        ):
            if not user.output_checker:
                raise exceptions.RequestPermissionDenied(
                    f"only an output checker can set status to {to_status.name}"
                )

            if user == release_request.author:
                raise exceptions.RequestPermissionDenied(
                    f"Can not set your own request to {to_status.name}"
                )

            if (
                to_status == RequestStatus.APPROVED
                and not release_request.all_files_approved()
            ):
                raise exceptions.RequestPermissionDenied(
                    f"Cannot set status to {to_status.name}; request has unapproved files."
                )

    def set_status(
        self,
        release_request: ReleaseRequest,
        to_status: RequestStatus,
        user: User,
        audit_extra: dict[str, str] | None = None,
    ):
        """Set the status of the request.

        This will validate the transition, and then mutate the request object.

        As calling set_status will mutate the passed ReleaseRequest, in cases
        where we may want to call to external services (e.g. job-server) to
        mutate external state, and these calls might fail, we provide
        a look-before-you-leap API. That is, when changing status and related
        state, an implementer should call `check_status(...)` first to validate
        the desired state transition and permissions are valid, then mutate
        their own state, and then call `set_status(...)` if successful to
        mutate the passed ReleaseRequest object.
        """

        # validate first
        self.check_status(release_request, to_status, user)
        audit_extra = audit_extra or {}
        audit = AuditEvent.from_request(
            release_request,
            type=self.STATUS_AUDIT_EVENT[to_status],
            user=user,
            path=None,
            **audit_extra,
        )
        self._dal.set_status(release_request.id, to_status, audit)
        if (release_request.status, to_status) == (
            RequestStatus.RETURNED,
            RequestStatus.SUBMITTED,
        ):
            notification_event = NotificationEventType.REQUEST_RESUBMITTED
        else:
            notification_event = self.STATUS_EVENT_NOTIFICATION[to_status]

        updates = None
        if to_status == RequestStatus.APPROVED:
            output_file_count = len(release_request.output_files())
            updates = [
                {
                    "update": f"{output_file_count} file{'s' if output_file_count > 1 else ''} will be uploaded"
                }
            ]

        release_request.status = to_status
        self.send_notification(
            release_request, notification_event, user, updates=updates
        )

    def validate_file_types(self, file_paths):
        """
        Validate file types before releasing.

        This is a final safety check before files are released. It
        should never be hit in production, as file types are checked
        before display in the workspace view and on adding to a
        request.
        """
        for relpath, _ in file_paths:
            if not is_valid_file_type(Path(relpath)):
                raise exceptions.RequestPermissionDenied(
                    f"Invalid file type ({relpath}) found in request"
                )

    def add_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str = "default",
        filetype: RequestFileType = RequestFileType.OUTPUT,
        audit_extra: dict[str, str] | None = None,
    ) -> ReleaseRequest:
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        permissions.check_user_can_add_file_to_request(
            user, release_request, workspace, relpath
        )

        src = workspace.abspath(relpath)
        file_id = store_file(release_request, src)

        audit_extra = audit_extra or {}
        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_ADD,
            user=user,
            path=relpath,
            group=group_name,
            filetype=filetype.name,
            **audit_extra,
        )

        manifest = workspace.get_manifest_for_file(relpath)
        assert manifest["content_hash"] == file_id, (
            "File hash does not match manifest.json"
        )

        filegroup_data = self._dal.add_file_to_request(
            request_id=release_request.id,
            group_name=group_name,
            relpath=relpath,
            file_id=file_id,
            filetype=filetype,
            timestamp=manifest["timestamp"],
            commit=manifest["commit"],
            repo=manifest["repo"],
            size=manifest["size"],
            job_id=manifest["job_id"],
            row_count=manifest["row_count"],
            col_count=manifest["col_count"],
            audit=audit,
        )
        release_request.set_filegroups_from_dict(filegroup_data)

        return release_request

    def update_file_in_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
    ) -> ReleaseRequest:
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        permissions.check_user_can_update_file_on_request(
            user, release_request, workspace, relpath
        )

        request_file = release_request.get_request_file_from_output_path(relpath)
        return self.replace_file_in_request(
            release_request, relpath, user, request_file.group, request_file.filetype
        )

    def add_withdrawn_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str = "default",
        filetype: RequestFileType = RequestFileType.OUTPUT,
    ) -> ReleaseRequest:
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        permissions.check_user_can_add_file_to_request(
            user, release_request, workspace, relpath
        )

        return self.replace_file_in_request(
            release_request, relpath, user, group_name, filetype
        )

    def change_file_properties_in_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str = "default",
        filetype: RequestFileType = RequestFileType.OUTPUT,
    ) -> ReleaseRequest:
        """
        Change file type or move file to a different group in the request
        """
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        request_file = release_request.get_request_file_from_output_path(relpath)
        permissions.check_user_can_change_request_file_properties(
            user, release_request, workspace, relpath, request_file.filetype
        )
        return self.replace_file_in_request(
            release_request, relpath, user, group_name, filetype
        )

    def replace_file_in_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str,
        filetype: RequestFileType,
    ) -> ReleaseRequest:
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        permissions.check_user_can_replace_file_in_request(
            user, release_request, workspace, relpath, group_name, filetype
        )

        src = workspace.abspath(relpath)
        file_id = store_file(release_request, src)

        manifest = workspace.get_manifest_for_file(relpath)
        assert manifest["content_hash"] == file_id, (
            "File hash does not match manifest.json"
        )

        request_file = release_request.get_request_file_from_output_path(relpath)
        old_group = request_file.group
        old_filetype = request_file.filetype

        for reviewer_user_id in request_file.reviews:
            reviewer = User.objects.get(pk=reviewer_user_id)
            audit = AuditEvent.from_request(
                request=release_request,
                type=AuditEventType.REQUEST_FILE_RESET_REVIEW,
                user=user,
                path=relpath,
                group=old_group,
                filetype=old_filetype.name,
                reviewer=reviewer.user_id,
            )
            self._dal.reset_review_file(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
                user=reviewer,
            )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_WITHDRAW,
            user=user,
            path=relpath,
            group=old_group,
            filetype=old_filetype.name,
        )
        filegroup_data = self._dal.delete_file_from_request(
            request_id=release_request.id,
            relpath=relpath,
            audit=audit,
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_UPDATE,
            user=user,
            path=relpath,
            group=group_name,
            filetype=filetype.name,
        )
        filegroup_data = self._dal.add_file_to_request(
            request_id=release_request.id,
            group_name=group_name,
            relpath=relpath,
            file_id=file_id,
            filetype=filetype,
            timestamp=manifest["timestamp"],
            commit=manifest["commit"],
            repo=manifest["repo"],
            size=manifest["size"],
            job_id=manifest["job_id"],
            row_count=manifest["row_count"],
            col_count=manifest["col_count"],
            audit=audit,
        )
        release_request.set_filegroups_from_dict(filegroup_data)

        return release_request

    def withdraw_file_from_request(
        self,
        release_request: ReleaseRequest,
        group_path: UrlPath,
        user: User,
    ):
        relpath = UrlPath(*group_path.parts[1:])
        request_file = release_request.get_request_file_from_urlpath(group_path)
        permissions.check_user_can_withdraw_file_from_request(
            user,
            release_request,
            self.get_workspace(release_request.workspace, user),
            relpath,
            request_file.filetype,
        )
        group_name = group_path.parts[0]
        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_WITHDRAW,
            user=user,
            path=relpath,
            group=group_name,
        )

        if release_request.status == RequestStatus.PENDING:
            # the user has not yet submitted the request, so just remove the file
            filegroup_data = self._dal.delete_file_from_request(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
            )
        elif release_request.status == RequestStatus.RETURNED:
            # the request has been returned, set the file type to WITHDRAWN
            filegroup_data = self._dal.withdraw_file_from_request(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
            )
        else:
            assert False, (
                f"Invalid state {release_request.status.name}, cannot withdraw file {relpath} from request {release_request.id}"
            )

        release_request.set_filegroups_from_dict(filegroup_data)
        return release_request

    def release_files(self, release_request: ReleaseRequest, user: User):
        """Release all files from a release_request to job-server.

        This currently uses the old api, and is shared amongst provider
        implementations, but that will likely change in future.

        This creates the release on job-server, but doesn't actually upload
        the files.
        """
        # check this is valid status transition (or it's already approved)
        # *before* initiating the release
        # If a file fails to upload, we may need to re-try releasing from an
        # already-approved status
        if release_request.status != RequestStatus.APPROVED:
            bll.check_status(release_request, RequestStatus.APPROVED, user)

        file_paths = release_request.get_output_file_paths()
        self.validate_file_types(file_paths)

        filelist = old_api.create_filelist(file_paths, release_request)

        old_api.get_or_create_release(
            release_request.workspace,
            release_request.id,
            filelist.json(),
            user.username,
        )

        for relpath, _ in file_paths:
            # If a file has already been released, this is a re-release attempt due
            # to an issue with releasing (note - NOT uploading) one or more files.
            # If something goes wrong here, we may end up with some, but not all, files
            # marked as released. Updating the request status to APPROVED is the last
            # thing that's done in this method, so a request that's only partially
            # released it's files can be re-released.
            # For files that have already been released, their upload is in progress,
            # so for those we do nothing.
            request_file = release_request.get_request_file_from_output_path(relpath)
            if request_file.released_at:
                continue

            audit = AuditEvent.from_request(
                request=release_request,
                type=AuditEventType.REQUEST_FILE_RELEASE,
                user=user,
                path=relpath,
            )

            # Note: releasing the file updates its released_at and released by
            # attributes, as an indication of intent to release. Actually uploading
            # the file will be handled by the asychronous file uploader.
            self._dal.release_file(release_request.id, relpath, user, audit)

        # Change status to approved if necessary.
        if release_request.status != RequestStatus.APPROVED:
            bll.set_status(release_request, RequestStatus.APPROVED, user)

    def get_released_files_for_request(self, release_request: ReleaseRequest):
        return [
            RequestFile.from_dict(file_metadata)
            for file_metadata in self._dal.get_released_files_for_request(
                request_id=release_request.id
            )
        ]

    def get_released_files_for_upload(self, release_request: ReleaseRequest):
        return [
            request_file
            for request_file in self.get_released_files_for_request(release_request)
            if not request_file.uploaded
        ]

    def register_file_upload_attempt(
        self, release_request: ReleaseRequest, relpath: UrlPath
    ):
        """
        Register an attempt to upload a file
        """
        return RequestFile.from_dict(
            self._dal.register_file_upload_attempt(release_request.id, relpath)
        )

    def register_file_upload(
        self, release_request: ReleaseRequest, relpath: UrlPath, user: User
    ):
        """
        Register that a file has been uploaded successfully
        """
        assert relpath in release_request.output_files()
        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_UPLOAD,
            user=user,
            path=relpath,
        )
        self._dal.register_file_upload(release_request.id, relpath, audit)

    def submit_request(
        self,
        request: ReleaseRequest,
        user: User,
        audit_extra: dict[str, str] | None = None,
    ):
        """
        Change status to SUBMITTED. If the request is currently in
        RETURNED status, mark any changes-requested reviews as undecided.
        """
        permissions.check_user_can_submit_request(user, request)
        self.check_status(request, RequestStatus.SUBMITTED, user)

        # reset any previous review data
        if request.status == RequestStatus.RETURNED:
            # any unapproved files that have not been updated are set to UNDECIDED
            for rfile in request.output_files().values():
                for review in rfile.changes_requested_reviews():
                    self.mark_file_undecided(request, review, rfile.relpath, user)

        self.set_status(request, RequestStatus.SUBMITTED, user, audit_extra=audit_extra)
        self._dal.start_new_turn(request.id)

    def approve_file(
        self,
        release_request: ReleaseRequest,
        request_file: RequestFile,
        user: User,
    ):
        """ "Approve a file"""
        permissions.check_user_can_review_file(
            user, release_request, request_file.relpath
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_APPROVE,
            user=user,
            path=request_file.relpath,
            group=request_file.group,
        )

        self._dal.approve_file(
            release_request.id,
            request_file.relpath,
            release_request.review_turn,
            user,
            audit,
        )

    def request_changes_to_file(
        self,
        release_request: ReleaseRequest,
        request_file: RequestFile,
        user: User,
    ):
        """Request changes to a file"""
        permissions.check_user_can_review_file(
            user, release_request, request_file.relpath
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_REQUEST_CHANGES,
            user=user,
            path=request_file.relpath,
            group=request_file.group,
        )

        self._dal.request_changes_to_file(
            release_request.id,
            request_file.relpath,
            release_request.review_turn,
            user,
            audit,
        )

    def reset_review_file(
        self, release_request: ReleaseRequest, relpath: UrlPath, user: User
    ):
        """Reset a file to have no review from this user"""

        permissions.check_user_can_reset_file_review(user, release_request, relpath)
        request_file = release_request.get_request_file_from_output_path(relpath)
        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_RESET_REVIEW,
            user=user,
            path=relpath,
            group=request_file.group,
        )

        self._dal.reset_review_file(release_request.id, relpath, user, audit)

    def review_request(self, release_request: ReleaseRequest, user: User):
        """
        Submit a review

        Marking the request as either PARTIALLY_REVIEWED or REVIEWED, depending on whether this is the first or second review.
        """
        permissions.check_user_can_submit_review(user, release_request)

        self._dal.record_review(release_request.id, user)

        release_request = self.get_release_request(release_request.id, user)
        n_reviews = release_request.submitted_reviews_count()

        # this method is called twice, by different users. It advances the
        # state differently depending on whether its the 1st or 2nd review to
        # be submitted.
        try:
            if n_reviews == 1:
                self.set_status(release_request, RequestStatus.PARTIALLY_REVIEWED, user)
            elif n_reviews == 2:
                self.set_status(release_request, RequestStatus.REVIEWED, user)
        except exceptions.InvalidStateTransition:
            # There is a potential race condition where two reviewers hit the Submit Review
            # button at the same time, and both attempt to transition from SUBMITTED to
            # PARTIALLY_REVIEWED, or from PARTIALLY_REVIEWED to REVIEWED
            # Assuming that the request status is now either PARTIALLY_REVIEWED or REVIEWED,
            # we can verify the status by refreshing the request, getting the number of reviews
            # again, and advance it if necessary
            if release_request.status not in [
                RequestStatus.PARTIALLY_REVIEWED,
                RequestStatus.REVIEWED,
            ]:
                raise
            release_request = self.get_release_request(release_request.id, user)
            if (
                release_request.submitted_reviews_count() > 1
                and release_request.status == RequestStatus.PARTIALLY_REVIEWED
            ):
                self.set_status(release_request, RequestStatus.REVIEWED, user)

    def return_request(self, release_request: ReleaseRequest, user: User):
        permissions.check_user_can_return_request(user, release_request)
        if release_request.submitted_reviews_count() < 2:
            # This is an early return; revert activity on the current turn before we return the request
            self.revert_turn_activity(release_request, user)

        self.set_status(release_request, RequestStatus.RETURNED, user)
        self._dal.start_new_turn(release_request.id)

    def revert_turn_activity(self, release_request: ReleaseRequest, user: User):
        """
        Revert turn acitivty for an early return
        Votes: reset or mark undecided for all votes from this turn
        Comments: delete all from this turn
        Set all audit logs from this turn to hidden. Only the
        early return audit and return status change log will remain visible
        """
        reviews_to_reset = [
            (relpath, review)
            for relpath, request_file in release_request.output_files().items()
            for review in request_file.reviews.values()
            if review.review_turn == release_request.review_turn
        ]

        for relpath, review in reviews_to_reset:
            if review.reviewer.username in release_request.submitted_reviews:
                self.mark_file_undecided(
                    release_request, review, relpath, review.reviewer
                )
            else:
                self.reset_review_file(release_request, relpath, review.reviewer)

        for filegroup in release_request.filegroups.values():
            for comment in filegroup.comments:
                if comment.review_turn == release_request.review_turn:
                    bll.group_comment_delete(
                        release_request, filegroup.name, comment.id, comment.author
                    )

        self.hide_audit_events_for_turn(release_request, release_request.review_turn)
        self.audit_early_return(release_request, user)

    def mark_file_undecided(
        self,
        release_request: ReleaseRequest,
        review: FileReview,
        relpath: UrlPath,
        user: User,
    ):
        """
        Change an existing file review in a submitted review to undecided before (early) returning
        or re-submitting
        """
        policies.check_can_mark_file_undecided(release_request, review)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_UNDECIDED,
            user=user,
            reviewer=review.reviewer.user_id,
            path=relpath,
        )

        self._dal.mark_file_undecided(
            release_request.id,
            relpath,
            release_request.review_turn,
            review.reviewer,
            audit,
        )

    def group_edit(
        self,
        release_request: ReleaseRequest,
        group: str,
        context: str,
        controls: str,
        user: User,
        audit_extra: dict[str, str] | None = None,
    ):
        permissions.check_user_can_edit_request(user, release_request)

        audit_extra = audit_extra or {}
        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_EDIT,
            user=user,
            group=group,
            context=context,
            controls=controls,
            path=None,
            **audit_extra,
        )

        self._dal.group_edit(release_request.id, group, context, controls, audit)

    def group_comment_create(
        self,
        release_request: ReleaseRequest,
        group: str,
        comment: str,
        visibility: Visibility,
        user: User,
    ):
        permissions.check_user_can_comment_on_group(user, release_request)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_COMMENT,
            user=user,
            group=group,
            comment=comment,
            review_turn=str(release_request.review_turn),
            visibility=visibility.name,
        )

        self._dal.group_comment_create(
            release_request.id,
            group,
            comment,
            visibility,
            release_request.review_turn,
            user,
            audit,
        )

    def group_comment_delete(
        self, release_request: ReleaseRequest, group: str, comment_id: str, user: User
    ):
        filegroup = release_request.filegroups.get(group)
        if not filegroup:
            raise exceptions.FileNotFound(f"Filegroup {group} not found")

        comment = next(
            (c for c in filegroup.comments if c.id == comment_id),
            None,
        )
        if not comment:
            raise exceptions.FileNotFound(f"Comment {comment_id} not found")

        permissions.check_user_can_delete_comment(user, release_request, comment)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_COMMENT_DELETE,
            user=user,
            group=group,
            comment=comment.comment,
        )

        self._dal.group_comment_delete(
            release_request.id, group, comment_id, user, audit
        )

    def group_comment_visibility_public(
        self, release_request: ReleaseRequest, group: str, comment_id: str, user: User
    ):
        filegroup = release_request.filegroups.get(group)
        if not filegroup:
            raise exceptions.FileNotFound(f"Filegroup {group} not found")

        comment = next(
            (c for c in filegroup.comments if c.id == comment_id),
            None,
        )
        if not comment:
            raise exceptions.FileNotFound(f"Comment {comment_id} not found")

        permissions.check_user_can_make_comment_publicly_visible(
            user, release_request, comment
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_COMMENT_VISIBILITY_PUBLIC,
            user=user,
            group=group,
            comment=comment.comment,
        )

        self._dal.group_comment_visibility_public(
            release_request.id, group, comment_id, user, audit
        )

    # can filter out these audit events
    READONLY_EVENTS = {
        AuditEventType.WORKSPACE_FILE_VIEW,
        AuditEventType.REQUEST_FILE_VIEW,
        AuditEventType.REQUEST_FILE_UNDECIDED,
    }

    def get_request_audit_log(
        self,
        user: User,
        request: ReleaseRequest,
        group: str | None = None,
        exclude_readonly: bool = False,
        size: int | None = None,
    ) -> list[AuditEvent]:
        """Fetches the audit log for this request, filtering for what the user can see."""

        audits = self._dal.get_audit_log(
            request=request.id,
            group=group,
            exclude=self.READONLY_EVENTS if exclude_readonly else set(),
            size=size,
        )

        return list(
            filter_visible_items(
                audits,
                request.review_turn,
                request.get_turn_phase(),
                permissions.user_can_review_request(user, request),
                user,
            )
        )

    def audit_workspace_file_access(
        self, workspace: Workspace, path: UrlPath, user: User
    ):
        audit = AuditEvent(
            type=AuditEventType.WORKSPACE_FILE_VIEW,
            user=user,
            workspace=workspace.name,
            path=path,
        )
        self._dal.audit_event(audit)

    def audit_request_file_access(
        self, request: ReleaseRequest, path: UrlPath, user: User
    ):
        audit = AuditEvent.from_request(
            request,
            AuditEventType.REQUEST_FILE_VIEW,
            user=user,
            path=path,
            group=path.parts[0],
        )
        self._dal.audit_event(audit)

    def audit_request_file_download(
        self, request: ReleaseRequest, path: UrlPath, user: User
    ):
        audit = AuditEvent.from_request(
            request,
            AuditEventType.REQUEST_FILE_DOWNLOAD,
            user=user,
            path=path,
            group=path.parts[0],
        )
        self._dal.audit_event(audit)

    def audit_early_return(self, request: ReleaseRequest, user: User):
        audit = AuditEvent.from_request(
            request,
            AuditEventType.REQUEST_EARLY_RETURN,
            user=user,
        )
        self._dal.audit_event(audit)

    def hide_audit_events_for_turn(self, request: ReleaseRequest, review_turn):
        self._dal.hide_audit_events_for_turn(request.id, review_turn)

    def validate_update_dicts(self, updates):
        if updates is None:
            return
        allowed_keys = {"update", "group", "user"}
        for update_dict in updates:
            assert "update" in update_dict, (
                "Notification updates must include an `update` key"
            )
            extra_keys = set(update_dict.keys()) - allowed_keys
            assert not extra_keys, (
                f"Unexpected keys in notification update ({extra_keys})"
            )

    def send_notification(
        self,
        request: ReleaseRequest,
        event_type: NotificationEventType,
        user: User,
        updates: list[dict[str, str]] | None = None,
    ):
        """
        Send a notification about an event.
        Events can send a optional list of dicts to include in the
        notification. These must include at least one `update` key
        with a description of the update, and optional `user` and
        `group` keys.
        """
        event_data = {
            "event_type": event_type.value,
            "workspace": request.workspace,
            "request": request.id,
            "request_author": request.author.username,
            "user": user.username,
            "updates": updates,
        }
        self.validate_update_dicts(updates)
        if settings.AIRLOCK_OUTPUT_CHECKING_ORG:
            event_data.update(
                {
                    "org": settings.AIRLOCK_OUTPUT_CHECKING_ORG,
                    "repo": settings.AIRLOCK_OUTPUT_CHECKING_REPO,
                }
            )

        data = send_notification_event(json.dumps(event_data), user.username)
        logger.info(
            "Notification sent: %s %s - %s",
            request.id,
            event_type.value,
            data["status"],
        )
        if data["status"] == "error":
            logger.error(data["message"])


def _get_configured_bll():
    DataAccessLayer = import_string(settings.AIRLOCK_DATA_ACCESS_LAYER)
    return BusinessLogicLayer(DataAccessLayer())


# We follow the Django pattern of using a lazy object which configures itself on first
# access so as to avoid reading `settings` during import. The `cast` here is a runtime
# no-op, but indicates to the type-checker that this should be treated as an instance of
# BusinessLogicLayer not SimpleLazyObject.
bll = cast(BusinessLogicLayer, SimpleLazyObject(_get_configured_bll))

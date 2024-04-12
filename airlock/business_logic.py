from __future__ import annotations

import hashlib
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Protocol, Self, cast

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string

import old_api
from airlock.renderers import get_renderer
from airlock.users import User
from airlock.utils import is_valid_file_type


# We use PurePosixPath as a convenient URL path representation. In theory we could use
# `NewType` here to indicate that we want this to be treated as a distinct type without
# actually creating one. But doing so results in a number of spurious type errors for
# reasons I don't fully understand (possibly because PurePosixPath isn't itself type
# annotated?).
if TYPE_CHECKING:  # pragma: no cover

    class UrlPath(PurePosixPath): ...
else:
    UrlPath = PurePosixPath

ROOT_PATH = UrlPath()  # empty path


class RequestStatus(Enum):
    """Status for release Requests"""

    # author set statuses
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    WITHDRAWN = "WITHDRAWN"
    # output checker set statuses
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RELEASED = "RELEASED"


class RequestFileType(Enum):
    OUTPUT = "output"
    SUPPORTING = "supporting"
    WITHDRAWN = "withdrawn"


class FileReviewStatus(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class AuditEventType(Enum):
    """Audit log events."""

    # file access
    WORKSPACE_FILE_VIEW = "WORKSPACE_FILE_VIEW"
    REQUEST_FILE_VIEW = "REQUEST_FILE_VIEW"
    REQUEST_FILE_DOWNLOAD = "REQUEST_FILE_DOWNLOAD"

    # request status
    REQUEST_CREATE = "REQUEST_CREATE"
    REQUEST_SUBMIT = "REQUEST_SUBMIT"
    REQUEST_WITHDRAW = "REQUEST_WITHDRAW"
    REQUEST_APPROVE = "REQUEST_APPROVE"
    REQUEST_REJECT = "REQUEST_REJECT"
    REQUEST_RELEASE = "REQUEST_RELEASE"

    # request file status
    REQUEST_FILE_ADD = "REQUEST_FILE_ADD"
    REQUEST_FILE_WITHDRAW = "REQUEST_FILE_WITHDRAW"
    REQUEST_FILE_APPROVE = "REQUEST_FILE_APPROVE"
    REQUEST_FILE_REJECT = "REQUEST_FILE_REJECT"


@dataclass
class AuditEvent:
    type: AuditEventType
    user: str
    workspace: str | None = None
    request: str | None = None
    path: UrlPath | None = None
    extra: dict[str, str] = field(default_factory=dict)
    # this is used when querying the db for audit log times
    created_at: datetime = field(default_factory=timezone.now, compare=False)

    WIDTH = max(len(k.name) for k in AuditEventType)

    @classmethod
    def from_request(
        cls,
        request: ReleaseRequest,
        type: AuditEventType,  # noqa: A002
        user: User,
        path: UrlPath | None = None,
        **kwargs,
    ):
        event = cls(
            type=type,
            user=user.username,
            workspace=request.workspace,
            request=request.id,
            extra=kwargs,
        )
        if path:
            event.path = path

        return event

    def __str__(self):
        ts = self.created_at.isoformat()[:-13]  # seconds precision
        msg = [
            f"{ts}: {self.type.name:<{self.WIDTH}} user={self.user} workspace={self.workspace}"
        ]

        if self.request:
            msg.append(f"request={self.request}")
        if self.path:
            msg.append(f"path={self.path}")

        for k, v in self.extra.items():
            msg.append(f"{k}={v}")

        return " ".join(msg)


class AirlockContainer(Protocol):
    """Structural typing class for a instance of a Workspace or ReleaseRequest

    Provides a uniform interface for accessing information about the paths and files
    contained within this instance.
    """

    def get_id(self) -> str:
        """Get the human name for this container."""

    def get_url(self, path: UrlPath = ROOT_PATH) -> str:
        """Get the url for the container object with path"""

    def get_contents_url(
        self, path: UrlPath = ROOT_PATH, download: bool = False
    ) -> str:
        """Get the url for the contents of the container object with path"""

    def request_filetype(self, relpath: UrlPath) -> RequestFileType | None:
        """What kind of file is this, e.g. output, supporting, etc."""


@dataclass(order=True)
class Workspace:
    """Simple wrapper around a workspace directory on disk.

    Deliberately a dumb python object - the only operations are about accessing
    filepaths within the workspace directory, and related urls.
    """

    name: str
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.root().exists():
            raise BusinessLogicLayer.WorkspaceNotFound(self.name)

    def __str__(self):
        return self.get_id()

    def project(self):
        return self.metadata.get("project", None)

    def root(self):
        return settings.WORKSPACE_DIR / self.name

    def get_id(self):
        return self.name

    def get_url(self, relpath=ROOT_PATH):
        return reverse(
            "workspace_view",
            kwargs={"workspace_name": self.name, "path": relpath},
        )

    def get_requests_url(self):
        return reverse(
            "requests_for_workspace",
            kwargs={"workspace_name": self.name},
        )

    def get_contents_url(self, relpath, download=False):
        url = reverse(
            "workspace_contents",
            kwargs={"workspace_name": self.name, "path": relpath},
        )

        # what renderer would render this file?
        renderer = get_renderer(self.abspath(relpath))
        url += f"?cache_id={renderer.cache_id}"

        return url

    def abspath(self, relpath):
        """Get absolute path for file

        Protects against traversal, and ensures the path exists."""
        root = self.root()
        path = root / relpath

        # protect against traversal
        path.resolve().relative_to(root)

        # validate path exists
        if not path.exists():
            raise BusinessLogicLayer.FileNotFound(path)

        return path

    def request_filetype(self, relpath):
        return None


@dataclass(frozen=True)
class FileReview:
    """
    Represents a review of a file in the context of a release request
    """

    reviewer: str
    status: FileReviewStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, attrs):
        return cls(**attrs)


@dataclass(frozen=True)
class RequestFile:
    """
    Represents a single file within a release request
    """

    relpath: UrlPath
    file_id: str
    reviews: list[FileReview]
    filetype: RequestFileType = RequestFileType.OUTPUT

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k != "reviews"},
            reviews=[FileReview.from_dict(value) for value in attrs.get("reviews", ())],
        )

    def approved_for_release(self):
        """
        A file is approved for release if it has been approved by two reviewers
        """
        return (
            len(
                [
                    review
                    for review in self.reviews
                    if review.status == FileReviewStatus.APPROVED
                ]
            )
            >= 2
        )


@dataclass(frozen=True)
class FileGroup:
    """
    Represents a group of one or more files within a release request
    """

    name: str
    files: dict[UrlPath, RequestFile]

    @property
    def output_files(self):
        return [f for f in self.files.values() if f.filetype == RequestFileType.OUTPUT]

    @property
    def supporting_files(self):
        return [
            f for f in self.files.values() if f.filetype == RequestFileType.SUPPORTING
        ]

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k != "files"},
            files={
                UrlPath(value["relpath"]): RequestFile.from_dict(value)
                for value in attrs.get("files", ())
            },
        )


@dataclass
class ReleaseRequest:
    """Represents a release request made by a user.

    Deliberately a dumb python object. Does not operate on the state of request,
    except for the request directory on disk and the files stored within.

    All other state is provided at instantiation by the provider.
    """

    id: str
    workspace: str
    author: str
    created_at: datetime
    status: RequestStatus = RequestStatus.PENDING
    filegroups: dict[str, FileGroup] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k != "filegroups"},
            filegroups=cls._filegroups_from_dict(attrs.get("filegroups", {})),
        )

    @staticmethod
    def _filegroups_from_dict(attrs):
        return {key: FileGroup.from_dict(value) for key, value in attrs.items()}

    def __post_init__(self):
        self.root().mkdir(parents=True, exist_ok=True)

    def __str__(self):
        return self.get_id()

    def root(self):
        return settings.REQUEST_DIR / self.workspace / self.id

    def get_id(self):
        return self.id

    def get_url(self, relpath=ROOT_PATH):
        return reverse(
            "request_view",
            kwargs={
                "request_id": self.id,
                "path": relpath,
            },
        )

    def get_contents_url(self, relpath, download=False):
        url = reverse(
            "request_contents",
            kwargs={"request_id": self.id, "path": relpath},
        )
        if download:
            url += "?download"
        else:
            # what renderer would render this file?
            request_file = self.get_request_file(relpath)
            renderer = get_renderer(self.abspath(relpath), request_file)
            url += f"?cache_id={renderer.cache_id}"

        return url

    def get_request_file(self, relpath: UrlPath | str):
        relpath = UrlPath(relpath)
        group = relpath.parts[0]
        file_relpath = UrlPath(*relpath.parts[1:])

        if not (filegroup := self.filegroups.get(group)):
            raise BusinessLogicLayer.FileNotFound(f"bad group {group} in url {relpath}")

        if not (request_file := filegroup.files.get(file_relpath)):
            raise BusinessLogicLayer.FileNotFound(relpath)

        return request_file

    def abspath(self, relpath):
        """Returns abspath to the file on disk.

        The first part of the relpath is the group, so we parse and validate that first.
        """
        request_file = self.get_request_file(relpath)
        return self.root() / request_file.file_id

    def all_files_set(self):
        """Return the relpaths for all files on the request, of any filetype"""
        return {
            request_file.relpath
            for filegroup in self.filegroups.values()
            for request_file in filegroup.files.values()
        }

    def output_files_set(self):
        """Return the relpaths for output files on the request"""
        return {
            request_file.relpath
            for filegroup in self.filegroups.values()
            for request_file in filegroup.output_files
        }

    def get_file_review_for_reviewer(self, urlpath: UrlPath, reviewer: str):
        return next(
            (
                r
                for r in self.get_request_file(urlpath).reviews
                if r.reviewer == reviewer
            ),
            None,
        )

    def request_filetype(self, urlpath: UrlPath):
        try:
            return self.get_request_file(urlpath).filetype
        except BusinessLogicLayer.FileNotFound:
            return None

    def set_filegroups_from_dict(self, attrs):
        self.filegroups = self._filegroups_from_dict(attrs)

    def get_output_file_paths(self):
        paths = []
        for file_group in self.filegroups.values():
            for request_file in file_group.output_files:
                relpath = request_file.relpath
                abspath = self.abspath(file_group.name / relpath)
                paths.append((relpath, abspath))
        return paths

    def all_files_approved(self):
        return all(
            request_file.approved_for_release()
            for filegroup in self.filegroups.values()
            for request_file in filegroup.output_files
        )


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
        author: str,
        status: RequestStatus,
        audit: AuditEvent,
        id: str | None = None,  # noqa: A002
    ):
        raise NotImplementedError()

    def get_active_requests_for_workspace_by_user(self, workspace: str, username: str):
        raise NotImplementedError()

    def get_requests_for_workspace(self, workspace: str):
        raise NotImplementedError()

    def get_requests_authored_by_user(self, username: str):
        raise NotImplementedError()

    def get_outstanding_requests_for_review(self):
        raise NotImplementedError()

    def set_status(self, request_id: str, status: RequestStatus, audit: AuditEvent):
        raise NotImplementedError()

    def add_file_to_request(
        self,
        request_id: str,
        relpath: UrlPath,
        file_id: str,
        group_name: str,
        filetype: RequestFileType,
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

    def withdraw_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def approve_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def reject_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def audit_event(self, audit: AuditEvent):
        raise NotImplementedError()

    def get_audit_log(
        self,
        user: str | None = None,
        workspace: str | None = None,
        request: str | None = None,
    ) -> list[AuditEvent]:
        raise NotImplementedError()


class BusinessLogicLayer:
    """
    The mechanism via which the rest of the codebase should read and write application
    state. Interacts with a Data Access Layer purely by exchanging simple values
    (dictionaries, strings etc).
    """

    def __init__(self, data_access_layer: DataAccessLayerProtocol):
        self._dal = data_access_layer

    class APIException(Exception):
        pass

    class WorkspaceNotFound(APIException):
        pass

    class ReleaseRequestNotFound(APIException):
        pass

    class FileNotFound(APIException):
        pass

    class InvalidStateTransition(APIException):
        pass

    class WorkspacePermissionDenied(APIException):
        pass

    class RequestPermissionDenied(APIException):
        pass

    class ApprovalPermissionDenied(APIException):
        pass

    def get_workspace(self, name: str, user: User) -> Workspace:
        """Get a workspace object."""

        if user is None or not user.has_permission(name):
            raise self.WorkspacePermissionDenied()

        # this is a bit awkward. IF the user is an output checker, they may not
        # have the workspace metadata in their User instance, so we provide an
        # empty metadata instance.
        # Currently, the only place this metadata is used is in the workspace
        # index, to group by project, so its mostly fine that its not here.
        return Workspace(name, user.workspaces.get(name, {}))

    def get_workspaces_for_user(self, user: User) -> list[Workspace]:
        """Get all the local workspace directories that a user has permission for."""

        workspaces = []
        for workspace_name in user.workspaces:
            try:
                workspace = self.get_workspace(workspace_name, user)
            except self.WorkspaceNotFound:
                continue

            workspaces.append(workspace)

        return workspaces

    def _create_release_request(
        self,
        workspace: str,
        author: str,
        status: RequestStatus = RequestStatus.PENDING,
        id: str | None = None,  # noqa: A002
    ) -> ReleaseRequest:
        """Factory function to create a release_request.

        Is private because it is mean also used directly by our test factories
        to set up state - it is not part of the public API.
        """
        # id is used to set specific ids in tests. We should probbably not allow this.
        audit = AuditEvent(
            type=self.STATUS_AUDIT_EVENT[status],
            user=author,
            workspace=workspace,
            # DAL will set request id once its created
        )
        return ReleaseRequest.from_dict(
            self._dal.create_release_request(
                workspace=workspace,
                author=author,
                status=status,
                audit=audit,
                id=id,
            )
        )

    def get_release_request(self, request_id: str, user: User) -> ReleaseRequest:
        """Get a ReleaseRequest object for an id."""

        release_request = ReleaseRequest.from_dict(
            self._dal.get_release_request(request_id)
        )

        if not user.has_permission(release_request.workspace):
            raise self.WorkspacePermissionDenied()

        return release_request

    def get_current_request(self, workspace: str, user: User) -> ReleaseRequest | None:
        """Get the current request for a workspace/user."""

        if not user.has_permission(workspace):
            raise self.RequestPermissionDenied(
                f"you do not have permission to view requests for {workspace}"
            )

        active_requests = self._dal.get_active_requests_for_workspace_by_user(
            workspace=workspace,
            username=user.username,
        )

        n = len(active_requests)
        if n == 0:
            return None
        elif n == 1:
            return ReleaseRequest.from_dict(active_requests[0])
        else:
            raise Exception(
                f"Multiple active release requests for user {user.username} in "
                f"workspace {workspace}"
            )

    def get_or_create_current_request(
        self, workspace: str, user: User
    ) -> ReleaseRequest:
        """
        Get the current request for a workspace/user, or create a new one if there is
        none.
        """
        request = self.get_current_request(workspace, user)
        if request is not None:
            return request

        if not user.can_create_request(workspace):
            raise BusinessLogicLayer.RequestPermissionDenied(workspace)

        return self._create_release_request(workspace, user.username)

    def get_requests_for_workspace(
        self, workspace: str, user: User
    ) -> list[ReleaseRequest]:
        """Get all release requests in workspaces a user has access to."""

        if not user.has_permission(workspace):
            raise self.RequestPermissionDenied(
                f"you do not have permission to view requests for {workspace}"
            )

        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_requests_for_workspace(workspace=workspace)
        ]

    def get_requests_authored_by_user(self, user: User) -> list[ReleaseRequest]:
        """Get all current requests authored by user."""
        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_requests_authored_by_user(username=user.username)
        ]

    def get_outstanding_requests_for_review(self, user: User):
        """Get all request that need review."""
        # Only output checkers can see these
        if not user.output_checker:
            return []

        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_outstanding_requests_for_review()
            # Do not show output_checker their own requests
            if attrs["author"] != user.username
        ]

    VALID_STATE_TRANSITIONS = {
        RequestStatus.PENDING: [
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
        ],
        RequestStatus.SUBMITTED: [
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.PENDING,  # allow un-submission
            RequestStatus.WITHDRAWN,
        ],
        RequestStatus.APPROVED: [
            RequestStatus.RELEASED,
            RequestStatus.REJECTED,  # allow fixing mistake *before* release
            RequestStatus.WITHDRAWN,  # allow user to withdraw before released
        ],
        RequestStatus.REJECTED: [
            RequestStatus.APPROVED,  # allow mind changed
        ],
    }

    STATUS_AUDIT_EVENT = {
        RequestStatus.PENDING: AuditEventType.REQUEST_CREATE,
        RequestStatus.SUBMITTED: AuditEventType.REQUEST_SUBMIT,
        RequestStatus.APPROVED: AuditEventType.REQUEST_APPROVE,
        RequestStatus.REJECTED: AuditEventType.REQUEST_REJECT,
        RequestStatus.RELEASED: AuditEventType.REQUEST_RELEASE,
        RequestStatus.WITHDRAWN: AuditEventType.REQUEST_WITHDRAW,
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
            raise self.InvalidStateTransition(
                f"from {release_request.status.name} to {to_status.name}"
            )

        # check permissions
        # author transitions
        if to_status in [
            RequestStatus.PENDING,
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
        ]:
            if user.username != release_request.author:
                raise self.RequestPermissionDenied(
                    f"only {user.username} can set status to {to_status.name}"
                )

        # output checker transitions
        if to_status in [
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.RELEASED,
        ]:
            if not user.output_checker:
                raise self.RequestPermissionDenied(
                    f"only an output checker can set status to {to_status.name}"
                )

            if user.username == release_request.author:
                raise self.RequestPermissionDenied(
                    f"Can not set your own request to {to_status.name}"
                )

            if (
                to_status == RequestStatus.APPROVED
                and not release_request.all_files_approved()
            ):
                raise self.RequestPermissionDenied(
                    f"Cannot set status to {to_status.name}; request has unapproved files."
                )

    def set_status(
        self, release_request: ReleaseRequest, to_status: RequestStatus, user: User
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
        audit = AuditEvent.from_request(
            release_request,
            type=self.STATUS_AUDIT_EVENT[to_status],
            user=user,
        )
        self._dal.set_status(release_request.id, to_status, audit)
        release_request.status = to_status

    def _validate_editable(self, release_request, user):
        if user.username != release_request.author:
            raise self.RequestPermissionDenied(
                f"only author {release_request.author} can modify the files in this request"
            )

        if release_request.status not in [
            RequestStatus.PENDING,
            RequestStatus.SUBMITTED,
        ]:
            raise self.RequestPermissionDenied(
                f"cannot modify files in request that is in state {release_request.status.name}"
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
                raise self.RequestPermissionDenied(
                    f"Invalid file type ({relpath}) found in request"
                )

    def add_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str = "default",
        filetype: RequestFileType = RequestFileType.OUTPUT,
    ):
        self._validate_editable(release_request, user)

        relpath = UrlPath(relpath)
        if not is_valid_file_type(Path(relpath)):
            raise self.RequestPermissionDenied(
                f"Cannot add file of type {relpath.suffix} to request"
            )

        workspace = self.get_workspace(release_request.workspace, user)
        src = workspace.abspath(relpath)
        file_id = store_file(release_request, src)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_ADD,
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
        self._validate_editable(release_request, user)
        relpath = UrlPath(*group_path.parts[1:])

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_WITHDRAW,
            user=user,
            path=relpath,
            group=group_path.parts[0],
        )

        if release_request.status == RequestStatus.PENDING:
            # the user has not yet submitted the request, so just remove the file
            filegroup_data = self._dal.delete_file_from_request(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
            )
        elif release_request.status == RequestStatus.SUBMITTED:
            # the request has been submitted, set the file type to WITHDRAWN
            filegroup_data = self._dal.withdraw_file_from_request(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
            )
        else:
            assert False, f"Invalid state {release_request.status.name}, cannot withdraw file {relpath} from request {release_request.id}"

        release_request.set_filegroups_from_dict(filegroup_data)
        return release_request

    def release_files(self, request: ReleaseRequest, user: User):
        """Release all files from a request to job-server.

        This currently uses the old api, and is shared amongst provider
        implementations, but that will likely change in future.
        """

        # we check this is valid status transition *before* releasing the files
        self.check_status(request, RequestStatus.RELEASED, user)

        file_paths = request.get_output_file_paths()
        self.validate_file_types(file_paths)

        filelist = old_api.create_filelist(file_paths)
        jobserver_release_id = old_api.create_release(
            request.workspace, filelist.json(), user.username
        )

        for relpath, abspath in file_paths:
            old_api.upload_file(jobserver_release_id, relpath, abspath, user.username)

        self.set_status(request, RequestStatus.RELEASED, user)

    def _verify_permission_to_review_file(
        self, release_request: ReleaseRequest, relpath: UrlPath, user: User
    ):
        if release_request.status != RequestStatus.SUBMITTED:
            raise self.ApprovalPermissionDenied(
                f"cannot approve file from request in state {release_request.status.name}"
            )

        if user.username == release_request.author:
            raise self.ApprovalPermissionDenied(
                "cannot approve files in your own request"
            )

        if not user.output_checker:
            raise self.ApprovalPermissionDenied(
                "only an output checker can approve a file"
            )

        if relpath not in release_request.output_files_set():
            raise self.ApprovalPermissionDenied(
                "file is not an output file on this request"
            )

    def approve_file(
        self, release_request: ReleaseRequest, relpath: UrlPath, user: User
    ):
        """ "Approve a file"""

        self._verify_permission_to_review_file(release_request, relpath, user)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_APPROVE,
            user=user,
            path=relpath,
        )

        bll._dal.approve_file(release_request.id, relpath, user.username, audit)

    def reject_file(
        self, release_request: ReleaseRequest, relpath: UrlPath, user: User
    ):
        """Reject a file"""

        self._verify_permission_to_review_file(release_request, relpath, user)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_REJECT,
            user=user,
            path=relpath,
        )

        bll._dal.reject_file(release_request.id, relpath, user.username, audit)

    def get_audit_log(
        self,
        user: str | None = None,
        workspace: str | None = None,
        request: str | None = None,
    ) -> list[AuditEvent]:
        return bll._dal.get_audit_log(
            user=user,
            workspace=workspace,
            request=request,
        )

    def audit_workspace_file_access(
        self, workspace: Workspace, path: UrlPath, user: User
    ):
        audit = AuditEvent(
            type=AuditEventType.WORKSPACE_FILE_VIEW,
            user=user.username,
            workspace=workspace.name,
            path=path,
        )
        bll._dal.audit_event(audit)

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
        bll._dal.audit_event(audit)

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
        bll._dal.audit_event(audit)


def _get_configured_bll():
    DataAccessLayer = import_string(settings.AIRLOCK_DATA_ACCESS_LAYER)
    return BusinessLogicLayer(DataAccessLayer())


# We follow the Django pattern of using a lazy object which configures itself on first
# access so as to avoid reading `settings` during import. The `cast` here is a runtime
# no-op, but indicates to the type-checker that this should be treated as an instance of
# BusinessLogicLayer not SimpleLazyObject.
bll = cast(BusinessLogicLayer, SimpleLazyObject(_get_configured_bll))

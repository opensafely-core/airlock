import shutil
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as stdlib_timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from django.conf import settings
from django.shortcuts import reverse

import old_api
from airlock.users import User


def modified_time(path):
    mtime = path.stat().st_mtime
    return datetime.fromtimestamp(mtime, tz=stdlib_timezone.utc).isoformat()


class Status(Enum):
    """Status for release Requests"""

    # author set statuses
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    WITHDRAWN = "WITHDRAWN"
    # output checker set statuses
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RELEASED = "RELEASED"


class AirlockContainer:
    """Abstract class for a directory or workspace or request files.

    Provides a uniform interface for accessing information about this directory.
    """

    def root(self):
        raise NotImplementedError()

    def get_id(self):
        raise NotImplementedError()

    def get_absolute_url(self):
        raise NotImplementedError()

    def get_url_for_path(self):
        raise NotImplementedError()

    def filelist(self):
        """List all files recursively."""
        path = self.root()
        return list(
            sorted(p.relative_to(path) for p in path.glob("**/*") if p.is_file())
        )


@dataclass(frozen=True)
class Workspace(AirlockContainer):
    """Simple wrapper around a workspace directory on disk.

    Deliberately a dumb python object - the only operations are about accessing
    filepaths within the workspace directory, and related urls.
    """

    name: str

    def __post_init__(self):
        if not self.root().exists():
            raise ProviderAPI.WorkspaceNotFound(self.name)

    def root(self):
        return settings.WORKSPACE_DIR / self.name

    def get_id(self):
        return self.name

    def get_absolute_url(self):
        return reverse("workspace_home", kwargs={"workspace_name": self.name})

    def get_url_for_path(self, relpath):
        return reverse(
            "workspace_view",
            kwargs={"workspace_name": self.name, "path": relpath},
        )

    def abspath(self, relpath):
        """Get absolute path for file

        Protects against traversal, and ensures the path exists."""
        root = self.root()
        path = root / relpath

        # protect against traversal
        path.resolve().relative_to(root)

        # validate path exists for *workspace* files
        if not path.exists():
            raise ProviderAPI.FileNotFound(path)

        return path


@dataclass(frozen=True)
class RequestFile:
    """
    Represents a single file within a release request
    """

    relpath: Path


@dataclass(frozen=True)
class FileGroup:
    """
    Represents a group of one or more files within a release request
    """

    name: str
    files: list[RequestFile]


@dataclass(frozen=True)
class ReleaseRequest(AirlockContainer):
    """Represents a release request made by a user.

    Deliberately a dumb python object. Does not operate on the state of request,
    except for the request directory on disk and the files stored within.

    All other state is provided at instantiation by the provider.
    """

    id: str
    workspace: str
    author: str
    created_at: datetime
    status: Status = Status.PENDING
    filegroups: list[FileGroup] = field(default_factory=list)

    def __post_init__(self):
        self.root().mkdir(parents=True, exist_ok=True)

    def root(self):
        return settings.REQUEST_DIR / self.workspace / self.id

    def get_id(self):
        return self.id

    def get_absolute_url(self):
        return reverse(
            "request_home",
            kwargs={
                "request_id": self.id,
            },
        )

    def get_url_for_path(self, relpath):
        return reverse(
            "request_view",
            kwargs={
                "request_id": self.id,
                "path": relpath,
            },
        )

    def abspath(self, relpath):
        root = self.root()
        path = root / relpath

        # protect against traversal
        path.resolve().relative_to(root)

        # note: we do not validate path exists for *request* file, as it might
        # be a destination to copy to.
        return path


class ProviderAPI:
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

    class RequestPermissionDenied(APIException):
        pass

    def get_workspace(self, name: str) -> Workspace:
        """Get a workspace object."""
        # this almost trivial currently, but may involve more in future
        return Workspace(name)

    def get_workspaces_for_user(self, user: User) -> list[Workspace]:
        """Get all the local workspace directories that a user has permission for."""

        workspaces = []
        if user.output_checker:
            workspace_names = [
                d.name for d in settings.WORKSPACE_DIR.iterdir() if d.is_dir()
            ]
        else:
            workspace_names = user.workspaces

        for workspace_name in workspace_names:
            try:
                workspace = self.get_workspace(workspace_name)
            except self.WorkspaceNotFound:
                continue

            workspaces.append(workspace)

        return workspaces

    def _create_release_request(self, **kwargs):
        """Factory function to create a release_request.

        The kwargs should match the public ReleaseRequest fields.

        Is private because it is mean to only be used by our test factories to
        set up state - it is not part of the public API.
        """
        raise NotImplementedError()

    def get_release_request(self, request_id: str) -> ReleaseRequest:
        """Get a ReleaseRequest object for an id."""
        raise NotImplementedError()

    def get_current_request(
        self, workspace_name: str, user: User, create: bool = False
    ) -> ReleaseRequest:
        """Get the current request for the a workspace/user.

        If create is True, create one.
        """
        raise NotImplementedError()

    def get_requests_authored_by_user(self, user: User) -> list[ReleaseRequest]:
        """Get all current requests authored by user."""
        raise NotImplementedError()

    def get_outstanding_requests_for_review(self, user: User):
        """Get all request that need review."""
        raise NotImplementedError()

    VALID_STATE_TRANSITIONS = {
        Status.PENDING: [
            Status.SUBMITTED,
            Status.WITHDRAWN,
        ],
        Status.SUBMITTED: [
            Status.APPROVED,
            Status.REJECTED,
            Status.PENDING,  # allow un-submission
            Status.WITHDRAWN,
        ],
        Status.APPROVED: [
            Status.RELEASED,
            Status.REJECTED,  # allow fixing mistake *before* release
            Status.WITHDRAWN,  # allow user to withdraw before released
        ],
        Status.REJECTED: [
            Status.APPROVED,  # allow mind changed
        ],
    }

    def check_status(
        self, release_request: ReleaseRequest, to_status: Status, user: User
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
        if to_status in [Status.PENDING, Status.SUBMITTED, Status.WITHDRAWN]:
            if user.username != release_request.author:
                raise self.RequestPermissionDenied(
                    f"only {user.username} can set status to {to_status.name}"
                )

        # output checker transitions
        if to_status in [Status.APPROVED, Status.REJECTED, Status.RELEASED]:
            if not user.output_checker:
                raise self.RequestPermissionDenied(
                    f"only an output checker can set status to {to_status.name}"
                )

            if user.username == release_request.author:
                raise self.RequestPermissionDenied(
                    f"Can not set your own request to {to_status.name}"
                )

    def set_status(
        self, release_request: ReleaseRequest, to_status: Status, user: User
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

        # Ensure the state of this object is updated to reflect the change in status
        # This is deliberately bypassing the frozen aspect of the class to keep
        # things consistent. It does change the hash.
        release_request.__dict__["status"] = to_status

    def add_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: Path,
        user: User,
        group_name: Optional[str] = "default",
    ) -> ReleaseRequest:
        """Add a file to a request.

        Subclasses should do what they need to create the filegroup and
        record the file metadata as needed and THEN
        call super().add_file_to_request(...) to do the
        copying. If the copying fails (e.g. due to permission errors raised
        below), the subclasses should roll back any changes.

        After calling add_file_to_request, the current release_request's
        filegroups will be out of date. Subclasses should return a new
        ReleaseRequest to ensure filegroups are current.
        """
        if user.username != release_request.author:
            raise self.RequestPermissionDenied(
                f"only author {release_request.author} can add files to this request"
            )

        if release_request.status not in [Status.PENDING, Status.SUBMITTED]:
            raise self.RequestPermissionDenied(
                f"cannot add file to request in state {release_request.status.name}"
            )

        workspace = self.get_workspace(release_request.workspace)
        src = workspace.abspath(relpath)
        dst = release_request.abspath(relpath)
        dst.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(src, dst)

        return release_request

    def release_files(self, request: ReleaseRequest, user: User):
        """Release all files from a request to job-server.

        This currently uses the old api, and is shared amongst provider
        implementations, but that will likely change in future.
        """

        # we check this is valid status transition *before* releasing the files
        self.check_status(request, Status.RELEASED, user)

        filelist = old_api.create_filelist(request)
        jobserver_release_id = old_api.create_release(
            request.workspace, filelist.json(), user.username
        )

        for f in filelist.files:
            relpath = Path(f.name)
            old_api.upload_file(
                jobserver_release_id, relpath, request.root() / relpath, user.username
            )

        self.set_status(request, Status.RELEASED, user)

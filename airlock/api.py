import shutil
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as stdlib_timezone
from enum import Enum
from pathlib import Path

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
        """User is not allowed to create a relesae request in this workspace.

        Note: will output_checkers can view all workspaces, they can only
        create requests in workspaces they have explicit permission for.
        """

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

    def get_requests_for_user(self, user: User) -> list[ReleaseRequest]:
        """Get all current requests authored by user"""
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
            Status.RELEASED,
            Status.REJECTED,  # allow fixing mistake *before* release
            Status.WITHDRAWN,  # allow user to withdraw before released
        ],
        Status.REJECTED: [
            Status.APPROVED,  # allow mind changed
        ],
    }

    def set_status(self, request: ReleaseRequest, status: Status, user: User):
        """Set the status of the request.

        Note: the ProviderAPI implementation SHOULD first call
        super().set_status(...) to validate transition and permissions and
        update object state, and then persist the state however it needs to.
        """
        # validate state logic
        valid_transitions = self.VALID_STATE_TRANSITIONS.get(request.status, [])

        if status not in valid_transitions:
            raise self.InvalidStateTransition(
                f"from {request.status.name} to {status.name}"
            )

        # check permissions
        # author transitions
        if status in [status.PENDING, status.SUBMITTED, status.WITHDRAWN]:
            if user.username != request.author:
                raise self.RequestPermissionDenied(
                    f"only {user.username} can set status to {status.name}"
                )

        # output checker transitions
        if status in [Status.APPROVED, status.REJECTED, Status.RELEASED]:
            if not user.output_checker:
                raise self.RequestPermissionDenied(
                    f"only an output checker can set status to {status.name}"
                )

            if user.username == request.author:
                raise self.RequestPermissionDenied(
                    f"Can not set your own request to {status.name}"
                )

        # Ensure the state of this object is updated to reflect the change in status
        # This is deliberately bypassing the frozen aspect of the class to keep
        # things consistent. It does change the hash.
        request.__dict__["status"] = status

    def add_file_to_request(self, release_request: ReleaseRequest, relpath: Path):
        """Add a file to a request.

        Subclasses should call super().add_file_to_request(...) to do the
        copying, then record the file metadata as needed.
        """
        workspace = self.get_workspace(release_request.workspace)
        src = workspace.abspath(relpath)
        dst = release_request.abspath(relpath)
        dst.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(src, dst)

    def release_files(self, request: ReleaseRequest, user: User):
        """Release all files from a request to job-server.

        This currently uses the old api, and is shared amongst provider
        implementations, but that will likely change in future.
        """

        # we need to check this before releasing the files
        if request.status != Status.APPROVED:
            raise self.InvalidStateTransition(f"From {request.status.name} to RELEASED")

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

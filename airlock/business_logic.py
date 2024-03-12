import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

# we use PurePosixPath as a convenient url path representation
from pathlib import PurePosixPath as UrlPath
from typing import Optional, Protocol

from django.conf import settings
from django.shortcuts import reverse
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string

import old_api
from airlock.users import User


ROOT_PATH = UrlPath()  # empty path


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


class AirlockContainer(Protocol):
    """Structural typing class for a instance of a Workspace or ReleaseRequest

    Provides a uniform interface for accessing information about the paths and files
    contained within this instance.
    """

    # The currently selected path in this container.  Used to calculate how
    # a particular path relates to it, i.e. if path *is* the currently selected
    # path, or is one of its parents.
    selected_path: UrlPath = ROOT_PATH

    def root(self) -> Path:
        """Absolute concrete Path to root dir for files in this container."""

    def get_id(self) -> str:
        """Get the human name for this container."""

    def get_url(self, path: UrlPath = ROOT_PATH) -> str:
        """Get the url for the container object with path"""

    def get_contents_url(
        self, path: UrlPath = ROOT_PATH, download: bool = False
    ) -> str:
        """Get the url for the contents of the container object with path"""


@dataclass(order=True)
class Workspace:
    """Simple wrapper around a workspace directory on disk.

    Deliberately a dumb python object - the only operations are about accessing
    filepaths within the workspace directory, and related urls.
    """

    name: str
    metadata: dict = field(default_factory=dict)

    # can be set to mark the currently selected path in this workspace
    selected_path: UrlPath = ROOT_PATH

    def __post_init__(self):
        if not self.root().exists():
            raise BusinessLogicLayer.WorkspaceNotFound(self.name)

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

    def get_contents_url(self, relpath, download=False):
        return reverse(
            "workspace_contents",
            kwargs={"workspace_name": self.name, "path": relpath},
        )

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


@dataclass(frozen=True)
class RequestFile:
    """
    Represents a single file within a release request
    """

    relpath: UrlPath

    @classmethod
    def from_dict(cls, attrs):
        return cls(**attrs)


@dataclass(frozen=True)
class FileGroup:
    """
    Represents a group of one or more files within a release request
    """

    name: str
    files: list[RequestFile]

    @classmethod
    def from_dict(cls, attrs):
        return cls(
            **{k: v for k, v in attrs.items() if k != "files"},
            files=[RequestFile.from_dict(value) for value in attrs.get("files", ())],
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
    status: Status = Status.PENDING
    filegroups: dict[FileGroup] = field(default_factory=dict)

    # can be set to mark the currently selected path in this release request
    selected_path: UrlPath = ROOT_PATH

    @classmethod
    def from_dict(cls, attrs):
        return cls(
            **{k: v for k, v in attrs.items() if k != "filegroups"},
            filegroups=cls._filegroups_from_dict(attrs.get("filegroups", {})),
        )

    @staticmethod
    def _filegroups_from_dict(attrs):
        return {key: FileGroup.from_dict(value) for key, value in attrs.items()}

    def __post_init__(self):
        self.root().mkdir(parents=True, exist_ok=True)

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
        return url

    def abspath(self, relpath):
        """Returns abspath to the file on disk.

        The first part of the relpath is the group, so we parse and validate that first.
        """
        relpath = UrlPath(relpath)
        root = self.root()

        group = relpath.parts[0]

        if group not in self.filegroups:
            raise BusinessLogicLayer.FileNotFound(f"bad group {group} in url {relpath}")

        filepath = relpath.relative_to(group)
        path = root / filepath

        # protect against traversal
        path.resolve().relative_to(root)

        # validate path exists
        if not path.exists():
            raise BusinessLogicLayer.FileNotFound(path)

        return path

    def file_set(self):
        return {
            request_file.relpath
            for filegroup in self.filegroups.values()
            for request_file in filegroup.files
        }

    def set_filegroups_from_dict(self, attrs):
        self.filegroups = self._filegroups_from_dict(attrs)


class DataAccessLayerProtocol:
    """
    Placeholder for a structural type class we can use to define what a data access
    layer should look like, once we've settled what that is.
    """


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

    class RequestPermissionDenied(APIException):
        pass

    def get_workspace(self, name: str, metadata: dict = {}) -> Workspace:
        """Get a workspace object."""
        # this almost trivial currently, but may involve more in future
        return Workspace(name, metadata)

    def get_workspaces_for_user(self, user: User) -> list[Workspace]:
        """Get all the local workspace directories that a user has permission for."""

        workspaces = []
        for workspace_name, metadata in user.workspaces.items():
            try:
                workspace = self.get_workspace(workspace_name, metadata)
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
        return ReleaseRequest.from_dict(self._dal.create_release_request(**kwargs))

    def get_release_request(self, request_id: str) -> ReleaseRequest:
        """Get a ReleaseRequest object for an id."""
        return ReleaseRequest.from_dict(self._dal.get_release_request(request_id))

    def get_current_request(
        self, workspace_name: str, user: User, create: bool = False
    ) -> ReleaseRequest:
        """Get the current request for the a workspace/user.

        If create is True, create one.
        """
        active_requests = self._dal.get_active_requests_for_workspace_by_user(
            workspace=workspace_name,
            username=user.username,
        )

        n = len(active_requests)
        if n > 1:
            raise Exception(
                f"Multiple active release requests for user {user.username} in workspace {workspace_name}"
            )
        elif n == 1:
            return ReleaseRequest.from_dict(active_requests[0])
        elif create:
            # To create a request, you must have explicit workspace permissions.
            # Output checkers can view all workspaces, but are not allowed to
            # create requests for all workspaces.
            if workspace_name not in user.workspaces:
                raise BusinessLogicLayer.RequestPermissionDenied(workspace_name)

            new_request = self._dal.create_release_request(
                workspace=workspace_name,
                author=user.username,
            )
            return ReleaseRequest.from_dict(new_request)
        else:
            return None

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
        self._dal.set_status(release_request.id, to_status)
        release_request.status = to_status

    def add_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: Optional[str] = "default",
    ):
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
        # manually contruct target path from root
        dst = release_request.root() / relpath
        dst.parent.mkdir(exist_ok=True, parents=True)
        shutil.copy(src, dst)

        # TODO: This is not currently safe in that we modify the filesytem before
        # calling out to the DAL which could fail. We will deal with this later by
        # switching to a content-addressed storage model which avoids having mutable
        # state on the filesystem.
        filegroup_data = self._dal.add_file_to_request(
            request_id=release_request.id, group_name=group_name, relpath=relpath
        )
        release_request.set_filegroups_from_dict(filegroup_data)
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
            relpath = UrlPath(f.name)
            old_api.upload_file(
                jobserver_release_id, relpath, request.root() / relpath, user.username
            )

        self.set_status(request, Status.RELEASED, user)


def _get_configured_bll():
    DataAccessLayer = import_string(settings.AIRLOCK_DATA_ACCESS_LAYER)
    return BusinessLogicLayer(DataAccessLayer())


# We follow the Django pattern of using a lazy object which configures itself on first
# access so as to avoid reading `settings` during import
bll = SimpleLazyObject(_get_configured_bll)

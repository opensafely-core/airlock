import hashlib
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Protocol, Self, cast

from django.conf import settings
from django.urls import reverse
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string

import old_api
from airlock.users import User


# We use PurePosixPath as a convenient URL path representation (we reassign rather than
# use `import as` to satisfy mypy that we intend to export this name)
UrlPath = PurePosixPath

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
    metadata: dict[str, str] = field(default_factory=dict)

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
    file_id: str

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(**attrs)


@dataclass(frozen=True)
class FileGroup:
    """
    Represents a group of one or more files within a release request
    """

    name: str
    files: list[RequestFile]

    @classmethod
    def from_dict(cls, attrs) -> Self:
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
    filegroups: dict[str, FileGroup] = field(default_factory=dict)

    # can be set to mark the currently selected path in this release request
    selected_path: UrlPath = ROOT_PATH

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
        group = relpath.parts[0]
        file_relpath = UrlPath(*relpath.parts[1:])

        if not (filegroup := self.filegroups.get(group)):
            raise BusinessLogicLayer.FileNotFound(f"bad group {group} in url {relpath}")

        matching_files = [f for f in filegroup.files if f.relpath == file_relpath]
        if not matching_files:
            raise BusinessLogicLayer.FileNotFound(relpath)
        assert len(matching_files) == 1
        request_file = matching_files[0]

        return self.root() / request_file.file_id

    def file_set(self):
        return {
            request_file.relpath
            for filegroup in self.filegroups.values()
            for request_file in filegroup.files
        }

    def set_filegroups_from_dict(self, attrs):
        self.filegroups = self._filegroups_from_dict(attrs)

    def get_file_paths(self):
        paths = []
        for file_group in self.filegroups.values():
            for request_file in file_group.files:
                relpath = request_file.relpath
                abspath = self.abspath(file_group.name / relpath)
                paths.append((relpath, abspath))
        return paths


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

    def create_release_request(self, **kwargs):
        raise NotImplementedError()

    def get_active_requests_for_workspace_by_user(self, workspace: str, username: str):
        raise NotImplementedError()

    def get_requests_authored_by_user(self, username: str):
        raise NotImplementedError()

    def get_outstanding_requests_for_review(self):
        raise NotImplementedError()

    def set_status(self, request_id: str, status: Status):
        raise NotImplementedError()

    def add_file_to_request(
        self, request_id, relpath: UrlPath, file_id: str, group_name: str
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

    def _create_release_request(self, **kwargs):
        """Factory function to create a release_request.

        The kwargs should match the public ReleaseRequest fields.

        Is private because it is mean to only be used by our test factories to
        set up state - it is not part of the public API.
        """
        return ReleaseRequest.from_dict(self._dal.create_release_request(**kwargs))

    def get_release_request(self, request_id: str, user: User) -> ReleaseRequest:
        """Get a ReleaseRequest object for an id."""

        release_request = ReleaseRequest.from_dict(
            self._dal.get_release_request(request_id)
        )

        if not user.has_permission(release_request.workspace):
            raise self.WorkspacePermissionDenied()

        return release_request

    def get_current_request(
        self, workspace_name: str, user: User
    ) -> ReleaseRequest | None:
        """Get the current request for a workspace/user."""
        active_requests = self._dal.get_active_requests_for_workspace_by_user(
            workspace=workspace_name,
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
                f"workspace {workspace_name}"
            )

    def get_or_create_current_request(
        self, workspace_name: str, user: User
    ) -> ReleaseRequest:
        """
        Get the current request for a workspace/user, or create a new one if there is
        none.
        """
        request = self.get_current_request(workspace_name, user)
        if request is not None:
            return request

        # To create a request, you must have explicit workspace permissions.  Output
        # checkers can view all workspaces, but are not allowed to create requests for
        # all workspaces.
        if workspace_name not in user.workspaces:
            raise BusinessLogicLayer.RequestPermissionDenied(workspace_name)

        new_request = self._dal.create_release_request(
            workspace=workspace_name,
            author=user.username,
        )
        return ReleaseRequest.from_dict(new_request)

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
        group_name: str = "default",
    ):
        if user.username != release_request.author:
            raise self.RequestPermissionDenied(
                f"only author {release_request.author} can add files to this request"
            )

        if release_request.status not in [Status.PENDING, Status.SUBMITTED]:
            raise self.RequestPermissionDenied(
                f"cannot add file to request in state {release_request.status.name}"
            )

        workspace = self.get_workspace(release_request.workspace, user)
        src = workspace.abspath(relpath)
        file_id = store_file(release_request, src)

        filegroup_data = self._dal.add_file_to_request(
            request_id=release_request.id,
            group_name=group_name,
            relpath=relpath,
            file_id=file_id,
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

        file_paths = request.get_file_paths()
        filelist = old_api.create_filelist(file_paths)
        jobserver_release_id = old_api.create_release(
            request.workspace, filelist.json(), user.username
        )

        for relpath, abspath in file_paths:
            old_api.upload_file(jobserver_release_id, relpath, abspath, user.username)

        self.set_status(request, Status.RELEASED, user)


def _get_configured_bll():
    DataAccessLayer = import_string(settings.AIRLOCK_DATA_ACCESS_LAYER)
    return BusinessLogicLayer(DataAccessLayer())


# We follow the Django pattern of using a lazy object which configures itself on first
# access so as to avoid reading `settings` during import. The `cast` here is a runtime
# no-op, but indicates to the type-checker that this should be treated as an instance of
# BusinessLogicLayer not SimpleLazyObject.
bll = cast(BusinessLogicLayer, SimpleLazyObject(_get_configured_bll))

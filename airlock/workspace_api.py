import dataclasses
import pathlib
import shutil
from datetime import datetime
from datetime import timezone as stdlib_timezone

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

import old_api


def ensure_parent_dir(path):
    path.parent.mkdir(exist_ok=True, parents=True)


def generate_request_id(workspace_name, user):
    # attempt globally unique but human readable id
    ts = timezone.now().strftime("%Y-%m-%d")
    return f"{ts}-{workspace_name}-{settings.BACKEND}-{user.username}"


def get_workspaces_for_user(user):
    """Get all Workspaces for user."""
    workspaces = []
    if user.output_checker:
        workspace_names = [
            d.name for d in settings.WORKSPACE_DIR.iterdir() if d.is_dir()
        ]
    else:
        workspace_names = user.workspaces

    for workspace_name in workspace_names:
        workspace = Workspace(workspace_name)
        if not workspace.exists():
            continue

        workspaces.append(workspace)

    return workspaces


def get_requests_for_user(user):
    """Get all ReleaseRequests for this user"""

    requests = []

    if user.output_checker:
        workspace_names = [d.name for d in settings.REQUEST_DIR.iterdir() if d.is_dir()]
    else:
        workspace_names = user.workspaces

    for workspace_name in workspace_names:
        workspace = Workspace(workspace_name)
        if not workspace.exists():
            continue

        requests_dir = settings.REQUEST_DIR / workspace_name
        if not requests_dir.exists():
            continue

        releases = [r for r in requests_dir.iterdir() if r.is_dir()]
        if not user.output_checker:
            # limit to just your requests if not output checker
            releases = [r for r in releases if r.name.endswith(user.username)]

        for release_dir in releases:
            requests.append(ReleaseRequest(workspace, release_dir.name))

    return requests


class Container:
    def root(self):
        """Return root directory"""
        raise NotImplementedError()

    def get_url(self, relpath):
        """Return url for relpath"""
        raise NotImplementedError()

    def exists(self):
        root = self.root()
        return root.exists() and root.is_dir()

    def get_path(self, relpath):
        return PathItem(self, pathlib.Path(relpath))

    def filelist(self):
        """List all files recursively."""
        path = self.root()
        return list(
            sorted(p.relative_to(path) for p in path.glob("**/*") if p.is_file())
        )


@dataclasses.dataclass(frozen=True)
class Workspace(Container):
    """These are containers that must live under the settings.WORKSPACE_DIR"""

    name: str

    def root(self):
        return settings.WORKSPACE_DIR / self.name

    def get_id(self):
        return self.name

    def url(self):
        return reverse("workspace_home", kwargs={"workspace_name": self.name})

    def get_url(self, relpath):
        return reverse(
            "workspace_view", kwargs={"workspace_name": self.name, "path": relpath}
        )

    def get_current_request(self, user, create=False):
        """Get the current request for user.

        If none are found, and create is True, then create one and return it.
        """
        releases_root = settings.REQUEST_DIR / self.name
        releases_root.mkdir(exist_ok=True)

        user_releases = [
            r
            for r in releases_root.iterdir()
            if r.is_dir() and r.name.endswith(user.username)
            # TODO filter for request status
        ]

        if len(user_releases) > 1:
            # TODO: error here. For now, just return the latest request until we've got request status
            latest = max((r.stat().st_ctime, r) for r in user_releases)
            return ReleaseRequest(self, latest[1].name)
        elif len(user_releases) == 1:
            return ReleaseRequest(self, user_releases[0].name)

        if create:
            return self.create_new_request(user)

        return None

    def create_new_request(self, user):
        release_id = generate_request_id(self.name, user)
        release_request = ReleaseRequest(self, release_id)
        release_request.ensure_request_dir()
        return release_request


@dataclasses.dataclass(frozen=True)
class ReleaseRequest(Container):
    """These are container that must live under the settings.REQUEST_DIR"""

    workspace: Workspace
    request_id: str

    def root(self):
        return settings.REQUEST_DIR / self.workspace.name / self.request_id

    def get_id(self):
        return self.request_id

    def ensure_request_dir(self):
        self.root().mkdir(exist_ok=True, parents=True)

    def url(self):
        return reverse(
            "request_home",
            kwargs={
                "workspace_name": self.workspace.name,
                "request_id": self.request_id,
            },
        )

    def get_url(self, relpath):
        return reverse(
            "request_view",
            kwargs={
                "workspace_name": self.workspace.name,
                "request_id": self.request_id,
                "path": relpath,
            },
        )

    def add_file(self, relpath):
        src = self.workspace.get_path(relpath)._absolute_path()
        dst = self.get_path(relpath)._absolute_path()
        ensure_parent_dir(dst)
        shutil.copy(src, dst)

    def release_files(self, user):
        filelist = old_api.create_filelist(self)
        jobserver_release_id = old_api.create_release(
            self.workspace.name, filelist.json(), user.username
        )

        for f in filelist.files:
            relpath = pathlib.Path(f.name)
            old_api.upload_file(
                jobserver_release_id, relpath, self.root() / relpath, user.username
            )


@dataclasses.dataclass(frozen=True)
class PathItem:
    """
    This provides a thin abstraction over `pathlib.Path` objects with two goals:

        1. Paths should be enforced as being relative to a certain "container" directory
           and it should not be possible to traverse outside of this directory or to
           construct one which points outside this directory (using the designated
           constructor classmethods).

        2. The abstraction should permit us, in future, to switch the implementation to
           something which is not tied to concrete filesystem paths.
    """

    container: Container
    relpath: pathlib.Path

    def __post_init__(self):
        # ensure relpath is a Path
        object.__setattr__(self, "relpath", pathlib.Path(self.relpath))
        # ensure path is within container
        self._absolute_path().resolve().relative_to(self.container.root())

    def _absolute_path(self):
        return self.container.root() / self.relpath

    def exists(self):
        return self._absolute_path().exists()

    def is_directory(self):
        return self._absolute_path().is_dir()

    def name(self):
        return self.relpath.name

    def url(self):
        suffix = "/" if self.is_directory() else ""
        return self.container.get_url(f"{self.relpath}{suffix}")

    def parent(self):
        if self.relpath.parents:
            return PathItem(self.container, self.relpath.parent)

    def children(self):
        root = self.container.root()
        return [
            PathItem(self.container, child.relative_to(root))
            for child in self._absolute_path().iterdir()
        ]

    def siblings(self):
        if not self.relpath.parents:
            return []
        else:
            return self.parent().children()

    def contents(self):
        return self._absolute_path().read_text()

    def modified_date(self):
        mtime = self._absolute_path().stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=stdlib_timezone.utc).isoformat()

    @property
    def suffix(self):
        return self.relpath.suffix

    def breadcrumbs(self):
        item = self
        crumbs = [item]

        parent = item.parent()
        while parent:
            if parent.relpath != pathlib.Path("."):
                crumbs.append(parent)
            parent = parent.parent()

        crumbs.reverse()
        return crumbs

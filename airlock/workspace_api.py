import dataclasses
import pathlib

from django.conf import settings
from django.urls import reverse

from airlock.users import User


def get_releases_for_user(user):
    """Get ReleaseRequest for this user"""

    if user.is_output_checker:
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

        releases = [r.name for r in requests_dir.iterdir() if r.is_dir()]
        for release_id in releases:
            yield ReleaseRequest(workspace, release_id)


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


@dataclasses.dataclass(frozen=True)
class WorkspacesRoot(Container):
    """This container represents settings.WORKSPACE_DIR"""

    user: User

    def root(self):
        return settings.WORKSPACE_DIR

    @property
    def workspaces(self):
        for child in self.get_path("").children():
            if child.is_directory() and self.user.has_permission(child.name()):
                yield Workspace(child.name())


@dataclasses.dataclass(frozen=True)
class Workspace(Container):
    """These are containers that must live under the settings.WORKSPACE_DIR"""

    name: str

    def root(self):
        return settings.WORKSPACE_DIR / self.name

    def index_url(self):
        return reverse("workspace_index")

    def url(self):
        return reverse("workspace_home", kwargs={"workspace_name": self.name})

    def get_url(self, relpath):
        return reverse(
            "workspace_view", kwargs={"workspace_name": self.name, "path": relpath}
        )


@dataclasses.dataclass(frozen=True)
class ReleaseRequest(Container):
    """These are container that must live under the settings.REQUEST_DIR"""

    workspace: Workspace
    request_id: str

    def root(self):
        return settings.REQUEST_DIR / self.workspace.name / self.request_id

    def create(self):
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

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from airlock import renderers
from airlock.enums import PathType, RequestFileType, WorkspaceFileStatus
from airlock.models import (
    CodeRepo,
    ReleaseRequest,
    Workspace,
)
from airlock.types import ROOT_PATH, FileMetadata, UrlPath
from airlock.utils import is_valid_file_type
from airlock.visibility import RequestFileStatus
from services.tracing import instrument
from users.models import User


class AirlockContainer(Protocol):
    """Structural typing class for a instance of a Workspace or ReleaseRequest

    Provides a uniform interface for the file browser to accessing information
    about the paths and files contained within this container, whichever kind
    it is.
    """

    def get_id(self) -> str:
        """Get the human name for this container."""

    def get_url(self, relpath: UrlPath = ROOT_PATH) -> str:
        """Get the url for the container object with path"""

    def get_contents_url(
        self, relpath: UrlPath, download: bool = False, plaintext: bool = False
    ) -> str:
        """Get the url for the contents of the container object with path"""

    def request_filetype(self, relpath: UrlPath) -> RequestFileType | None:
        """What kind of file is this, e.g. output, supporting, etc."""

    def get_renderer(
        self, relpath: UrlPath, plaintext: bool = False
    ) -> renderers.Renderer:
        """Create and return the correct renderer for this path."""

    def get_file_metadata(self, relpath: UrlPath) -> FileMetadata | None:
        """Get the file metadata"""

    def get_manifest_hash(self) -> str | None:
        """Return the hash of the manifest file content"""


@dataclass
class PathItem:
    """
    This provides a thin abstraction over url paths and related filesystem objects with two goals:

        1. Paths should be enforced as being relative to a certain "container" directory
           and it should not be possible to traverse outside of this directory or to
           construct one which points outside this directory (using the designated
           constructor classmethods).

        2. The abstraction should permit us, in future, to switch the implementation to
           something which is not tied to concrete filesystem paths.
    """

    class PathNotFound(Exception):
        pass

    container: AirlockContainer
    relpath: UrlPath

    type: PathType | None = None
    workspace_status: WorkspaceFileStatus | None = None
    request_status: RequestFileStatus | None = None
    children: list[PathItem] = field(default_factory=list)
    parent: PathItem | None = None

    # is this the currently selected path?
    selected: bool = False
    # should this node be expanded in the tree?
    expanded: bool = False

    request_filetype: RequestFileType | None = RequestFileType.OUTPUT

    # what to display for this node when rendering the tree. Defaults to name,
    # but this allow it to be overridden.
    display_text: str | None = None

    def is_directory(self):
        """Does this contain other things?"""
        return self.type != PathType.FILE

    def fake_parent(self):
        """Create a fake parent object to pass to the tree rendering.

        This allows the tree rendering to recurse once per *directory*, rather
        than once per *file*, which is a lot less expensive recursion.
        """
        return PathItem(
            container=self.container,
            relpath=self.relpath,
            type=self.type,
            children=[self],
            expanded=True,
        )

    def name(self):
        if self.relpath == ROOT_PATH:
            return self.container.get_id()
        return self.relpath.name

    def display(self):
        """How should this node be displayed in the tree nav."""
        if self.display_text:
            return self.display_text

        return self.name()

    def display_status(self):
        """Status of this path."""
        if isinstance(self.container, Workspace) and self.workspace_status:
            return self.workspace_status.formatted()
        # TODO request states
        return ""

    def url(self):
        url = self.container.get_url(self.relpath)
        suffix = "/" if (self.is_directory() and not url.endswith("/")) else ""
        return self.container.get_url(self.relpath) + suffix

    def contents_url(self, download: bool = False, plaintext: bool = False):
        if self.type != PathType.FILE:
            raise Exception(f"contents_url called on non-file path {self.relpath}")
        return self.container.get_contents_url(
            self.relpath, download=download, plaintext=plaintext
        )

    def contents_plaintext_url(self):
        return self.contents_url(plaintext=True)

    def iframe_sandbox(self):
        # we allow csv files to use scripts, as we render those ourselves
        if self.relpath.suffix == ".csv":
            return "allow-scripts"

        # disable everything by default
        return ""

    def download_url(self):
        return self.contents_url(download=True)

    def siblings(self):
        if self.parent is None:
            return []
        else:
            return self.parent.children

    def suffix(self):
        return self.relpath.suffix

    def file_type(self):
        return self.suffix().lstrip(".")

    def metadata(self) -> FileMetadata | None:
        if self.type == PathType.FILE:
            return self.container.get_file_metadata(self.relpath)
        else:
            return None

    def size(self) -> int | None:
        metadata = self.metadata()
        if metadata is None:
            return None

        return metadata.size

    def size_mb(self) -> str:
        size = self.size()
        if size is None:
            return ""

        if size == 0:
            return "0 Mb"
        elif size < 10240:
            return "<0.01 Mb"
        # out test files are small
        else:  # pragma: no cover
            mb = round(size / (1024 * 1024), 2)
            return f"{mb} Mb"

    def modified_at(self) -> datetime | None:
        metadata = self.metadata()
        if metadata is None:
            return None

        return datetime.fromtimestamp(metadata.timestamp, tz=UTC)

    def breadcrumbs(self):
        item = self
        crumbs = [item]

        parent = item.parent
        while parent:
            crumbs.append(parent)
            parent = parent.parent

        crumbs.reverse()
        return crumbs

    def is_output(self):
        return self.request_filetype == RequestFileType.OUTPUT

    def is_supporting(self):
        return self.request_filetype == RequestFileType.SUPPORTING

    def is_withdrawn(self):
        return self.request_filetype == RequestFileType.WITHDRAWN

    def is_valid(self):
        return is_valid_file_type(Path(self.relpath))

    def html_classes(self):
        """Semantic html classes for this PathItem.

        Distinguish file/dirs, file types and file statuses, in the UI.
        """
        classes = [self.type.value.lower()] if self.type else []

        if self.workspace_status:
            classes.append(f"workspace_{self.workspace_status.value.lower()}")
            metadata = self.metadata()
            if metadata and metadata.out_of_date_action:
                classes.append("out-of-date-action")
        elif self.is_output() and self.request_status:
            classes.append(f"request_{self.request_status.decision.value.lower()}")

            if self.request_status.vote:
                classes.append(f"user_{self.request_status.vote.value.lower()}")
            else:
                classes.append("user_incomplete")

        if self.request_filetype:
            classes.append(self.request_filetype.value.lower())

        if self.type == PathType.FILE:
            classes.append(self.file_type())
            if self.request_filetype != RequestFileType.CODE:
                if not self.is_valid():
                    classes.append("invalid")

        if self.selected:
            classes.append("selected")

        return " ".join(classes)

    def get_path(self, relpath: UrlPath | str):
        """Walk the tree and return the PathItem for relpath.

        Will raise PathNotFound if the path is not found.
        """
        relpath = UrlPath(relpath)
        if relpath == ROOT_PATH:
            return self

        def walk_tree(node, head, *tail):
            for child in node.children:
                if child.name() == head:
                    break
            else:
                raise self.PathNotFound(f"could not find path {relpath}")

            if not tail:
                return child

            return walk_tree(child, *tail)

        return walk_tree(self, *relpath.parts)

    def get_selected(self):
        """Get currently selected node.

        Will raise PathNotFound if no selected node.
        """
        if self.selected:
            return self

        def walk_selected(node):
            """Traverse tree to find selected node."""
            for child in node.children:
                if child.selected:
                    return child

                if child.expanded:
                    return walk_selected(child)

            raise self.PathNotFound("No selected path found")

        return walk_selected(self)

    def __str__(self):
        """Debugging utility to inspect tree."""

        def build_string(node, indent):
            yield f"{indent}{node.name()}{'*' if node.expanded else ''}{'**' if node.selected else ''}"
            for child in node.children:
                yield from build_string(child, indent + "  ")

        return "\n".join(build_string(self, ""))


@instrument(func_attributes={"workspace": "workspace"})
def get_workspace_tree(
    workspace: Workspace,
    selected_path: UrlPath | str = ROOT_PATH,
    selected_only: bool = False,
) -> PathItem:
    """Recursively build workspace tree from the root dir.

    If selected_only==True, we do not build entire tree, as that can be
    expensive if we just want to partially render one node.

    Instead, we build just the tree down to the selected path, and then all its
    immediate children, if it has any. We include children so that if
    selected_path is a directory, its contents can be partially rendered.
    """
    selected_path = UrlPath(selected_path)
    leaf_directories = set()

    if selected_only:
        pathlist = []

        # root path is implicit
        if selected_path != ROOT_PATH:
            pathlist.append(selected_path)

        if not workspace.is_valid_tree_path(selected_path):
            raise PathItem.PathNotFound(f"not current output {selected_path}")

        for child in workspace.workspace_child_map[selected_path]:
            pathlist.append(child)
            if workspace.workspace_child_map[
                child
            ]:  # has children, therefore is directory
                leaf_directories.add(child)
    else:
        pathlist = list(set(workspace.workspace_child_map) - {ROOT_PATH})

    root_node = PathItem(
        container=workspace,
        relpath=ROOT_PATH,
        type=PathType.WORKSPACE,
        parent=None,
        selected=(selected_path == ROOT_PATH),
        expanded=True,
    )

    root_node.children = get_path_tree(
        workspace,
        pathlist,
        parent=root_node,
        selected_path=selected_path,
        leaf_directories=leaf_directories,
    )
    return root_node


@instrument(func_attributes={"release_request": "release_request"})
def get_request_tree(
    release_request: ReleaseRequest,
    user: User,
    selected_path: UrlPath | str = ROOT_PATH,
    selected_only: bool = False,
):
    """Build a tree recursively for a ReleaseRequest

    For each group, we create a node for that group, and then build a sub-tree
    for its file groups.

    If selected_only=True, we avoid building the entire tree. Instead, we just
    build part of the tree on the selected_path, and its immediate children.
    """
    # ensure selected_path is UrlPath
    selected_path = UrlPath(selected_path)
    root_node = PathItem(
        container=release_request,
        relpath=ROOT_PATH,
        type=PathType.REQUEST,
        parent=None,
        selected=(selected_path == ROOT_PATH),
        expanded=True,
    )

    def _pluralise(input_list):
        if len(input_list) != 1:
            return "s"
        return ""

    for name, group in release_request.filegroups.items():
        group_path = UrlPath(name)
        selected = group_path == selected_path
        expanded = selected or (group_path in (selected_path.parents or []))
        group_node = PathItem(
            container=release_request,
            relpath=UrlPath(name),
            type=PathType.FILEGROUP,
            parent=root_node,
            display_text=(
                f"Group: {name} ({len(group.output_files)} requested file{_pluralise(group.output_files)})"
            ),
            selected=selected,
            expanded=True,
        )

        if expanded or not selected_only:
            # we want to list the children of this group,
            pathlist = list(group.files)

            group_node.children = get_path_tree(
                release_request,
                pathlist=pathlist,
                parent=group_node,
                selected_path=selected_path,
                expand_all=True,
                user=user,
            )

        root_node.children.append(group_node)

    return root_node


def get_code_tree(
    repo: CodeRepo, selected_path: UrlPath = ROOT_PATH, selected_only: bool = False
) -> PathItem:
    root_node = PathItem(
        container=repo,
        relpath=ROOT_PATH,
        type=PathType.REPO,
        parent=None,
        selected=(selected_path == ROOT_PATH),
        expanded=True,
    )

    leaf_directories = set()

    if selected_only and selected_path != ROOT_PATH:
        # we only want the selected path, and its immediate children if it has any
        pathlist = [selected_path]

        # we only have paths, so we find any child paths of the selected_path
        len_selected = len(selected_path.parts)
        for path in repo.pathlist:
            if path == selected_path:
                continue
            if path.parts[:len_selected] == selected_path.parts:
                # same prefix, so is a child
                child_path = UrlPath(*path.parts[: len_selected + 1])
                pathlist.append(child_path)

                # if this child has >1 additional path segment, it is
                # a directory.  So, mark it as a leaf directory from this
                # limited tree view, so it is correctly classified by
                # get_path_tree as a directory.
                if len(path.parts) > len_selected + 1:
                    leaf_directories.add(child_path)
    else:
        pathlist = repo.pathlist

    root_node.children = get_path_tree(
        repo,
        pathlist,
        parent=root_node,
        selected_path=selected_path,
        leaf_directories=leaf_directories,
    )
    return root_node


def get_path_tree(
    container: AirlockContainer,
    pathlist: list[UrlPath],
    parent: PathItem,
    selected_path: UrlPath = ROOT_PATH,
    expand_all: bool = False,
    leaf_directories: set[UrlPath] | None = None,
    user: User | None = None,
):
    """Walk a flat list of paths and create a tree from them."""

    def build_path_tree(
        path_parts: list[list[str]], parent: PathItem
    ) -> list[PathItem]:
        # group multiple paths into groups by first part of path
        grouped: dict[str, list[list[str]]] = dict()
        for child, *descendant_parts in path_parts:
            if child not in grouped:
                grouped[child] = []
            if descendant_parts:
                grouped[child].append(descendant_parts)

        tree = []

        # now we have them grouped by first path element, we can create a node
        # in the tree for them
        for child, descendants in grouped.items():
            path = parent.relpath / child
            selected = path == selected_path
            node = PathItem(
                container=container,
                relpath=path,
                parent=parent,
                selected=selected,
                request_filetype=container.request_filetype(path),
            )

            if descendants or (leaf_directories and path in leaf_directories):
                node.type = PathType.DIR

                # recurse down the tree
                node.children = build_path_tree(descendants, parent=node)

                # expand all regardless of selected state, used for request filegroup trees
                if expand_all:
                    node.expanded = True
                else:
                    node.expanded = selected or (path in (selected_path.parents or []))
            else:
                node.type = PathType.FILE
                # get_path_tree needs to work with both Workspace and
                # ReleaseRequest containers, so we have these container specfic
                # calls
                if isinstance(container, Workspace):
                    node.workspace_status = container.get_workspace_file_status(path)

                # user is required for request status, due to visibility
                if isinstance(container, ReleaseRequest) and user:
                    node.request_status = container.get_request_file_status(path, user)

            tree.append(node)

        tree.sort(key=children_sort_key)
        return tree

    path_parts = [list(p.parts) for p in pathlist]
    return build_path_tree(path_parts, parent)


def children_sort_key(node: PathItem):
    """Sort children first by directory, then files.

    The name metadata is sorted first, as its special."""
    # this works because True == 1 and False == 0
    name = node.name()
    return (name != "metadata", node.type == PathType.FILE, name)

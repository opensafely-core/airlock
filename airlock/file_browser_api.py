from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from airlock.business_logic import (
    ROOT_PATH,
    AirlockContainer,
    ReleaseRequest,
    RequestFileType,
    UrlPath,
    Workspace,
)
from airlock.utils import is_valid_file_type
from services.tracing import instrument


class PathType(Enum):
    """Types of PathItems in a tree."""

    FILE = "file"
    DIR = "directory"
    WORKSPACE = "workspace"
    REQUEST = "request"
    FILEGROUP = "filegroup"


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

    def name(self):
        if self.relpath == ROOT_PATH:
            return self.container.get_id()
        return self.relpath.name

    def display(self):
        """How should this node be displayed in the tree nav."""
        if self.display_text:
            return self.display_text

        return self.name()

    def url(self):
        suffix = "/" if self.is_directory() else ""
        return self.container.get_url(self.relpath) + suffix

    def contents_url(self, download: bool = False):
        if self.type != PathType.FILE:
            raise Exception(f"contents_url called on non-file path {self.relpath}")
        return self.container.get_contents_url(self.relpath, download=download)

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
        return self.container.request_filetype(self.relpath) == RequestFileType.OUTPUT

    def is_supporting(self):
        return (
            self.container.request_filetype(self.relpath) == RequestFileType.SUPPORTING
        )

    def is_withdrawn(self):
        return (
            self.container.request_filetype(self.relpath) == RequestFileType.WITHDRAWN
        )

    def is_valid(self):
        return is_valid_file_type(Path(self.relpath))

    def html_classes(self):
        """Semantic html classes for this PathItem.

        Currently, only "selected" is used, but it made sense to be able to
        distinguish file/dirs, and maybe even file types, in the UI, in case we
        need to.
        """
        classes = [self.type.value.lower()] if self.type else []

        if self.request_filetype:
            classes.append(self.request_filetype.value.lower())

        if self.type == PathType.FILE:
            classes.append(self.file_type())
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


def scantree(root: Path) -> tuple[list[UrlPath], set[UrlPath]]:
    """Use os.scandir to quickly walk a file tree.

    Basically, its faster because it effectively just opens every directory,
    not every file. And its in C code.  But that gives us whether the entry is
    a file or a directory, which is all we need.

    We are only really interested in file paths - those include any parent
    directories we need for the tree for free.  However, we do need to manually
    track empty directories, or else they will be excluded.
    """

    paths = []
    directories = set()

    def scan(current: str) -> int:
        children = 0

        for entry in os.scandir(current):
            children += 1
            path = UrlPath(entry.path).relative_to(root)

            if entry.is_dir():
                dir_children = scan(entry.path)
                if dir_children == 0:
                    # add empty dir to pathlist, or else it will not be shown
                    paths.append(path)
                    directories.add(path)
            else:
                paths.append(path)

        return children

    scan(str(root))

    return paths, directories


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
    root = workspace.root()

    if selected_only:
        pathlist = [selected_path]
        directories = set()

        # if directory, we also need to also load our children to display in
        # the content area. We don't mind using stat() on the children here, as
        # we are only loading a single directory, not an entire tree
        abspath = workspace.abspath(selected_path)
        if abspath.is_dir():
            for child in abspath.iterdir():
                path = child.relative_to(root)
                pathlist.append(path)
                if child.is_dir():
                    directories.add(path)

    else:
        pathlist, directories = scantree(root)

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
        directories=directories,
    )
    return root_node


@instrument(func_attributes={"release_request": "release_request"})
def get_request_tree(
    release_request: ReleaseRequest,
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
                f"{name} ({len(group.output_files)} requested file{_pluralise(group.output_files)})"
            ),
            selected=selected,
            expanded=selected or expanded,
        )

        if expanded or not selected_only:
            # we want to list the children of this group,
            pathlist = list(group.files)

            group_node.children = get_path_tree(
                release_request,
                pathlist=pathlist,
                parent=group_node,
                selected_path=selected_path,
                expanded=expanded,
            )

        root_node.children.append(group_node)

    return root_node


def filter_files(selected, files):
    """Filter the list of file paths for the selected file and any immediate children."""
    n = len(selected.parts)
    for f in files:
        head, tail = f.parts[:n], f.parts[n:]
        if head == selected.parts and len(tail) <= 1:
            yield f


def get_path_tree(
    container: AirlockContainer,
    pathlist: list[UrlPath],
    parent: PathItem,
    selected_path: UrlPath = ROOT_PATH,
    expanded: bool = False,
    directories: set[UrlPath] | None = None,
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

            if descendants or (directories and path in directories):
                node.type = PathType.DIR

                # recurse down the tree
                node.children = build_path_tree(descendants, parent=node)

                # expand all regardless of selected state, used for request filegroup trees
                if expanded:
                    node.expanded = True
                else:
                    node.expanded = selected or (path in (selected_path.parents or []))
            else:
                node.type = PathType.FILE

            tree.append(node)

        tree.sort(key=children_sort_key)
        return tree

    path_parts = [list(p.parts) for p in pathlist]
    return build_path_tree(path_parts, parent)


def children_sort_key(node: PathItem):
    """Sort children first by directory, then files."""
    # this works because True == 1 and False == 0
    return (node.type == PathType.FILE, node.name())

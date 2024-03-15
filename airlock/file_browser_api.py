from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from airlock.business_logic import (
    ROOT_PATH,
    AirlockContainer,
    ReleaseRequest,
    UrlPath,
    Workspace,
)


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

    # what to display for this node when rendering the tree. Defaults to name,
    # but this allow it to be overridden.
    display_text: str | None = None

    DISPLAY_TYPES = {
        "html": "iframe",
        "jpeg": "image",
        "jpg": "image",
        "png": "image",
        "svg": "image",
        "csv": "table",
        "tsv": "table",
        "txt": "preformatted",
        "log": "preformatted",
    }

    def __post_init__(self):
        # ensure is UrlPath
        self.relpath = UrlPath(self.relpath)

    def _absolute_path(self) -> Path:
        return self.container.abspath(self.relpath)

    def is_directory(self) -> bool:
        """Does this contain other things?"""
        return self.type != PathType.FILE

    def name(self) -> str:
        if self.relpath == ROOT_PATH:
            return self.container.get_id()
        return self.relpath.name

    def display(self):
        """How should this node be displayed in the tree nav."""
        if self.display_text:
            return self.display_text

        return self.name()

    def url(self) -> str:
        suffix = "/" if self.is_directory() else ""
        return self.container.get_url(self.relpath) + suffix

    def contents_url(self, download=False) -> str:
        if self.type != PathType.FILE:
            raise Exception(f"contents_url called on non-file path {self.relpath}")
        return self.container.get_contents_url(self.relpath, download=download)

    def download_url(self):
        return self.contents_url(download=True)

    def siblings(self):
        if self.parent is None:
            return []
        else:
            return self.parent.children

    def contents(self) -> str:
        if self.type == PathType.FILE:
            abspath = self._absolute_path()

            # backstop against an empty directory with a suffix being
            # missclassified as a file when building the tree. See build_path_tree.
            if abspath.is_file():
                return abspath.read_text()

            return f"{self.relpath} is not a file"

        raise Exception(
            f"contents() called on {self.relpath}, which is of type {self.type}"
        )

    def suffix(self) -> str:
        return self.relpath.suffix

    def file_type(self) -> str:
        return self.suffix().lstrip(".")

    def display_type(self) -> str:
        return self.DISPLAY_TYPES.get(self.file_type(), "preformatted")

    def breadcrumbs(self):
        item = self
        crumbs = [item]

        parent = item.parent
        while parent:
            crumbs.append(parent)
            parent = parent.parent

        crumbs.reverse()
        return crumbs

    def html_classes(self):
        """Semantic html classes for this PathItem.

        Currently, only "selected" is used, but it made sense to be able to
        distinguish file/dirs, and maybe even file types, in the UI, in case we
        need to.
        """
        classes = [self.type.value.lower()] if self.type else []

        if self.type == PathType.FILE:
            classes.append(self.file_type())

        if self.selected:
            classes.append("selected")

        return " ".join(classes)

    def get_path(self, relpath: UrlPath | str) -> PathItem:
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

    def __str__(self, render_selected=False):
        """Debugging utility to inspect tree."""

        def build_string(node, indent):
            yield f"{indent}{node.name()}{'*' if node.expanded else ''}{'**' if node.selected else ''}"
            for child in node.children:
                yield from build_string(child, indent + "  ")

        return "\n".join(build_string(self, ""))


def get_workspace_tree(
    workspace: Workspace, selected_path: UrlPath | str = ROOT_PATH, selected_only=False
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

        # if directory, we also need to also load children to display in the content area
        abspath = workspace.abspath(selected_path)
        if abspath.is_dir():
            pathlist.extend(child.relative_to(root) for child in abspath.iterdir())

    else:
        # listing all files in one go is much faster than walking the tree
        pathlist = [p.relative_to(root) for p in root.glob("**/*")]

    root_node = PathItem(
        container=workspace,
        relpath=ROOT_PATH,
        type=PathType.WORKSPACE,
        parent=None,
        selected=(selected_path == ROOT_PATH),
        expanded=True,
    )

    root_node.children = get_path_tree(
        workspace, pathlist, parent=root_node, selected_path=selected_path
    )
    return root_node


def get_request_tree(
    release_request: ReleaseRequest,
    selected_path: UrlPath | str = ROOT_PATH,
    selected_only=False,
) -> PathItem:
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

    for name, group in release_request.filegroups.items():
        group_path = UrlPath(name)
        selected = group_path == selected_path
        expanded = selected or (group_path in (selected_path.parents or []))
        group_node = PathItem(
            container=release_request,
            relpath=UrlPath(name),
            type=PathType.FILEGROUP,
            parent=root_node,
            display_text=f"{name} ({len(group.files)} files)",
            selected=selected,
            expanded=selected or expanded,
        )

        group_paths = [f.relpath for f in group.files]

        if selected_only:
            if expanded:
                if group_path == selected_path:
                    # we just need the group's immediate child paths
                    pathlist = [UrlPath(p.parts[0]) for p in group_paths]
                else:
                    # filter for just the selected path and any immediate children
                    selected_subpath = selected_path.relative_to(group_path)
                    pathlist = list(filter_files(selected_subpath, group_paths))
            else:
                # we don't want any children for unselected groups
                pathlist = []
        else:
            pathlist = group_paths

        group_node.children = get_path_tree(
            release_request,
            pathlist=pathlist,
            parent=group_node,
            selected_path=selected_path,
            expanded=expanded,
        )

        root_node.children.append(group_node)

    return root_node


def filter_files(selected: UrlPath, files: list[UrlPath]) -> Iterator[UrlPath]:
    """Filter the list of file paths for the selected file and any immediate children."""
    n = len(selected.parts)
    for f in files:
        head, tail = f.parts[:n], f.parts[n:]
        if head == selected.parts and len(tail) <= 1:
            yield f


NestedStrList = list[str] | list["NestedStrList"]


def get_path_tree(
    container,
    pathlist,
    parent,
    selected_path=ROOT_PATH,
    expanded=False,
) -> list[PathItem]:
    """Walk a flat list of paths and create a tree from them."""

    def build_path_tree(
        path_parts: list[tuple[str, ...]], parent: PathItem
    ) -> list[PathItem]:
        # group multiple paths into groups by first part of path
        grouped: dict[str, NestedStrList] = dict()
        for child, *descendants in path_parts:
            if child not in grouped:
                grouped[child] = []
            if descendants:
                grouped[child].append(descendants)

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
            )

            # If it has decendants, it is a directory. However, an empty
            # directory in workspace still needs to be classed as a PathType.DIR.
            # So we infer if it is by checking for lack of suffix. All output files
            # *must* have a suffix, so this is a reasonable check.
            #
            # However, in theory, there could be an empty directory with
            # a suffix in its name, so this will treat these as a file rather
            # than a directory.  If this is a problem, we could instead call
            # is_dir(), but we are trying to avoid hitting the filesystem in
            # the tree recursion for speed.
            #
            # We have a backstop check to not blow up in this case
            # PathItem.contents()
            if descendants or path.suffix == "":
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

        # sort directories first then files
        tree.sort(key=children_sort_key)
        return tree

    path_parts = [p.parts for p in pathlist]
    return build_path_tree(path_parts, parent)


def children_sort_key(node):
    """Sort children first by directory, then files."""
    # this works because True == 1 and False == 0
    return (node.type == PathType.FILE, node.name())

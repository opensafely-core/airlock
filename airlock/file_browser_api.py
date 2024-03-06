from dataclasses import dataclass, field
from enum import Enum

from airlock.api import ROOT_PATH, AirlockContainer, UrlPath


class PathType(Enum):
    """Types of PathItems in a tree."""

    FILE = "file"
    DIR = "dir"
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

    type: PathType = None
    children: list["PathItem"] = field(default_factory=list)
    parent: "PathItem" = None

    # is this the currently selected path?
    selected: bool = False
    # should this node be expanded in the tree?
    expanded: bool = False

    # what to display for this node when rendering the tree. Defaults to name,
    # but this allow it to be overridden.
    display_text: str = None

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

    def _absolute_path(self):
        return self.container.abspath(self.relpath)

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
        return self.container.get_url(f"{self.relpath}{suffix}")

    def contents_url(self, download=False):
        if self.type != PathType.FILE:
            raise Exception(f"contents_url called on non-file path {self.relpath}")
        return self.container.get_contents_url(f"{self.relpath}", download=download)

    def siblings(self):
        if not self.relpath.parents:
            return []
        else:
            return self.parent.children

    def contents(self):
        if self.type == PathType.FILE:
            return self._absolute_path().read_text()

        raise Exception(
            f"contents() called on {self.relpath}, which is of type {self.type}"
        )

    def suffix(self):
        return self.relpath.suffix

    def file_type(self):
        return self.suffix().lstrip(".")

    def display_type(self):
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
        classes = [self.type.name.lower()]

        if self.type == PathType.FILE:
            classes.append(self.file_type())

        if self.selected:
            classes.append("selected")

        return " ".join(classes)

    def get_path(self, relpath):
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


def get_workspace_tree(workspace, selected_path=ROOT_PATH):
    """Recursively build a workspace tree from files on disk."""

    def build_workspace_tree(path, parent):
        selected = path == selected_path
        node = PathItem(
            container=workspace,
            relpath=path,
            parent=parent,
            selected=selected,
        )

        if node._absolute_path().is_dir():
            if path == ROOT_PATH:
                node.type = PathType.WORKSPACE
            else:
                node.type = PathType.DIR

            # recurse and build children nodes
            node.children = [
                build_workspace_tree(
                    child.relative_to(workspace.root()),
                    parent=node,
                )
                for child in node._absolute_path().iterdir()
            ]
            node.children.sort(key=children_sort_key)
            node.expanded = selected or (path in (selected_path.parents or []))
        else:
            node.type = PathType.FILE

        return node

    # ensure selected_path is UrlPath
    selected_path = UrlPath(selected_path)
    return build_workspace_tree(ROOT_PATH, parent=None)


def get_request_tree(release_request, selected_path=ROOT_PATH):
    """Build a tree recursively for a ReleaseRequest

    For each group, we create a node for that group, and then build a sub-tree
    for its file groups.
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

        group_node.children = get_filegroup_tree(
            release_request,
            selected_path,
            group,
            group_node.relpath,
            parent=group_node,
            expanded=expanded,
        )

        root_node.children.append(group_node)

    return root_node


def get_filegroup_tree(
    container, selected_path, group_data, group_path, parent, expanded
):
    """Get the tree for a filegroup's files.

    This is more than just a walk the disk. The FileGroup.files is a flat list of
    relative paths. So we need to group those by common prefix and descend down
    the tree.
    """

    def build_filegroup_tree(file_parts, path, parent):
        """Walk a flat list of paths and create a directories tree for them."""

        # group multiple paths into groups by first part of path
        grouped = dict()
        for child, *descendants in file_parts:
            if child not in grouped:
                grouped[child] = []
            if descendants:
                grouped[child].append(descendants)

        tree = []

        # now we have them grouped by first path element, we can create a node
        # in the tree for them
        for child, descendants in grouped.items():
            child_path = path / child

            selected = child_path == selected_path
            node = PathItem(
                container=container,
                relpath=child_path,
                parent=parent,
                selected=selected,
            )

            abspath = node._absolute_path()
            assert abspath.exists()

            if descendants:
                assert abspath.is_dir()
                node.type = PathType.DIR
                # recurse down the tree
                node.children = build_filegroup_tree(
                    descendants, child_path, parent=node
                )
                node.expanded = expanded

            else:
                assert abspath.is_file()
                node.type = PathType.FILE

            tree.append(node)

        # sort directories first then files
        tree.sort(key=children_sort_key)
        return tree

    file_parts = [f.relpath.parts for f in group_data.files]
    return build_filegroup_tree(file_parts, group_path, parent)


def children_sort_key(node):
    """Sort children first by directory, then files."""
    # this works because True == 1 and False == 0
    return (node.type == PathType.FILE, node.name())

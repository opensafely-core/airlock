from dataclasses import dataclass

from airlock.api import ROOT_PATH, AirlockContainer, UrlPath


@dataclass(frozen=True)
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

    container: AirlockContainer
    relpath: UrlPath = ROOT_PATH

    def __post_init__(self):
        # ensure relpath is a Path
        object.__setattr__(self, "relpath", UrlPath(self.relpath))
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
        return self.container.get_url_for_path(f"{self.relpath}{suffix}")

    def parent(self):
        if self.relpath.parents:
            return PathItem(self.container, self.relpath.parent)

    def children(self):
        if not self.is_directory():
            return []
        root = self.container.root()
        children = [
            PathItem(self.container, child.relative_to(root))
            for child in self._absolute_path().iterdir()
        ]
        # directories first, then alphabetical, aks what windows does
        children.sort(key=lambda p: (not p.is_directory(), p.relpath.name))
        return children

    def siblings(self):
        if not self.relpath.parents:
            return []
        else:
            return self.parent().children()

    def contents(self):
        return self._absolute_path().read_text()

    def suffix(self):
        return self.relpath.suffix

    def file_type(self):
        return self.suffix().lstrip(".")

    def breadcrumbs(self):
        item = self
        crumbs = [item]

        parent = item.parent()
        while parent:
            if parent.relpath != ROOT_PATH:
                crumbs.append(parent)
            parent = parent.parent()

        crumbs.reverse()
        return crumbs

    def html_classes(self):
        """Semantic html classes for this PathItem.

        Currently, only "selected" is used, but it made sense to be able to
        distinguish file/dirs, and maybe even file types, in the UI, in case we
        need to.
        """
        classes = []

        if self.is_directory():
            classes.append("dir")
        else:
            classes.append("file")
            classes.append(self.file_type())

        if self.is_selected():
            classes.append("selected")

        return " ".join(classes)

    def is_selected(self):
        return self.relpath == self.container.selected_path

    def is_on_selected_path(self):
        return self.relpath in self.container.selected_path.parents

    def is_open(self):
        return self.is_selected() or self.is_on_selected_path()

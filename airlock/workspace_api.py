import dataclasses
import pathlib

from django.conf import settings
from django.urls import reverse


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

    relpath: pathlib.Path

    @classmethod
    def from_relative_path(cls, path: str | pathlib.Path):
        return cls._from_absolute_path(settings.WORKSPACE_DIR / path)

    @classmethod
    def _from_absolute_path(cls, path: pathlib.Path):
        return cls(path.resolve().relative_to(settings.WORKSPACE_DIR))

    def _absolute_path(self):
        return settings.WORKSPACE_DIR / self.relpath

    def exists(self):
        return self._absolute_path().exists()

    def is_directory(self):
        return self._absolute_path().is_dir()

    def name(self):
        return self.relpath.name

    def url(self):
        suffix = "/" if self.is_directory() else ""
        return reverse("file_browser", kwargs={"path": f"{self.relpath}{suffix}"})

    def parent(self):
        if self.relpath.parents:
            return PathItem(self.relpath.parent)

    def children(self):
        return [
            PathItem._from_absolute_path(child)
            for child in self._absolute_path().iterdir()
        ]

    def siblings(self):
        if not self.relpath.parents:
            return []
        else:
            return self.parent().children()

    def contents(self):
        return self._absolute_path().read_text()

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any


# We use PurePosixPath as a convenient URL path representation. In theory we could use
# `NewType` here to indicate that we want this to be treated as a distinct type without
# actually creating one. But doing so results in a number of spurious type errors for
# reasons I don't fully understand (possibly because PurePosixPath isn't itself type
# annotated?).
if TYPE_CHECKING:  # pragma: no cover

    class FilePath(PurePosixPath): ...
else:
    FilePath = PurePosixPath


ROOT_PATH = FilePath()  # empty path


@dataclass
class GroupPath:
    group: str
    file_path: FilePath

    @classmethod
    def from_str(cls, path: str):
        path = PurePosixPath(path)
        return cls.from_path(path)

    @classmethod
    def from_path(cls, path: Path):
        group = path.parts[0]
        file_path = FilePath(*path.parts[1:])
        return cls(group=group, file_path=file_path)


@dataclass
class FileMetadata:
    """Represents the base properties of file metadata.

    Often these are in the manifest.json, so can be read from there. But
    sometimes they are not, so need reading from the filesystem.
    """

    size: int
    timestamp: int
    _content_hash: str | None = None
    path: Path | None = None

    @classmethod
    def from_manifest(cls, metadata: dict[str, Any]) -> FileMetadata:
        return cls(
            size=int(metadata["size"]),
            timestamp=int(metadata["timestamp"]),
            _content_hash=str(metadata["content_hash"]),
        )

    @classmethod
    def from_path(cls, path: Path) -> FileMetadata:
        assert path.is_file()
        stat = path.stat()
        return cls(
            size=stat.st_size,
            timestamp=int(stat.st_mtime),
            path=path,
        )

    @cached_property
    def content_hash(self) -> str:
        if self._content_hash is not None:
            return self._content_hash
        elif self.path is not None:
            assert self.path.is_file()
            return hashlib.file_digest(self.path.open("rb"), "sha256").hexdigest()
        else:  # pragma: no cover
            # should never get here due to constructor's validation
            raise Exception("no content_hash available. FileMetadata.path: {self.path}")

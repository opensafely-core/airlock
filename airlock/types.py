from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any


# We use PurePosixPath as a convenient URL path representation. In theory we could use
# `NewType` here to indicate that we want this to be treated as a distinct type without
# actually creating one. But doing so results in a number of spurious type errors for
# reasons I don't fully understand (possibly because PurePosixPath isn't itself type
# annotated?).
if TYPE_CHECKING:  # pragma: no cover

    class UrlPath(PurePosixPath): ...
else:
    UrlPath = PurePosixPath


@dataclass
class FileMetadata:
    """Represents the base properties of file metadata.

    Often these are in the manifest.json, so can be read from there. But
    sometimes they are not, so need reading from the filesystem.
    """

    size: int | None
    timestamp: int | None
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
        if path.is_dir():
            return cls.empty()

        stat = path.stat()
        return cls(
            size=stat.st_size,
            timestamp=int(stat.st_mtime),
            path=path,
        )

    @classmethod
    def empty(cls) -> FileMetadata:
        return cls(size=None, timestamp=None)

    @cached_property
    def content_hash(self):
        if self._content_hash is not None:
            return self._content_hash
        elif self.path is not None:
            assert self.path.is_file()
            return hashlib.file_digest(self.path.open("rb"), "sha256").hexdigest()
        else:
            return None

    @property
    def size_mb(self) -> str:
        if self.size is None:
            return ""
        elif self.size == 0:
            return "0 Mb"
        elif self.size < 10240:
            return "<0.01 Mb"
        else:
            mb = round(self.size / (1024 * 1024), 2)
            return f"{mb} Mb"

    @property
    def modified_at(self) -> datetime | None:
        if self.timestamp is None:
            return None

        return datetime.utcfromtimestamp(self.timestamp)

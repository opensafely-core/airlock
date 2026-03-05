# This file is currently maintained in the release-hatch project, until we can
# extract it into it's own library.
#
# https://github.com/opensafely-core/release-hatch
#
# Until then, do not make local changes, rather copy the latest version of this
# file into your project.
from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, field_serializer


def sanitize_url(value: str) -> str:
    return str(value).replace("\\", "/")


UrlFileName = Annotated[str, BeforeValidator(sanitize_url)]


class ReviewStatus(Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class FileReview(BaseModel):
    status: ReviewStatus
    comments: dict[str, str]


class FileMetadata(BaseModel):
    """Metadata for a workspace file."""

    name: UrlFileName
    url: UrlFileName | None = None  # Url to path on release-hatch instance
    size: int  # size in bytes
    sha256: str  # sha256 of file
    date: datetime  # last modified in ISO date format
    metadata: dict[str, str] | None = None  # user supplied metadata about this file
    review: FileReview | None = None  # any review metadata for this file

    @field_serializer("date", mode="plain")
    def ser_date(self, value: datetime) -> str:
        return value.isoformat()


class FileList(BaseModel):
    """An index of files in a workspace.

    This must match the json format that the SPA's client API expects.
    """

    files: list[FileMetadata]
    metadata: dict[str, str] | None = None  # user supplied metadata about thse Release
    review: dict[str, str] | None = None  # review comments for the whole Release

    def get(self, name):  # pragma: no cover
        name = str(name)
        for f in self.files:
            if f.name == name:
                return f
        return None


class ReleaseFile(BaseModel):
    """File to upload to job-server.

    This schema is unique to the osrelease release-hatch API. The SPA uses
    a background upload process, rather than an user API to trigger it.
    """

    name: UrlFileName

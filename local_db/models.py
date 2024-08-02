import enum
from pathlib import Path
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

# We could create our own type stubs for this module but our use of it is so simple that
# it's not really worth it
from ulid import ulid  # type: ignore

from airlock.enums import (
    AuditEventType,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    Visibility,
)


def local_request_id():
    return str(ulid())


if TYPE_CHECKING:  # pragma: no cover
    # This works around a couple of issues with Django/Django-stubs/mypy:
    #
    # 1. Django-stubs requires `Field` subclasses to provide type arguments, but the
    #    actual Django `Field` class doesn't accept type arguments so it will pass type
    #    checking but fail at runtime. You can work around this by applying
    #    `django_stubs_ext.monkeypatch()` but yuck no thanks.
    #
    # 2. Django-stubs sets the type arguments on `TextField` to `str` and we don't seem
    #    to be able to override this to say that we accept/return enums.
    #
    # Even so, the type signature below is not actually quite what we want. Each
    # instance of `EnumField` accepts/returns just a single class of enum, not all enums
    # in general. And it should be a type error to use the wrong kind of enum with the
    # field. It's perfectly possible to specify this kind of behaviour in Python using
    # generics and type variables, but for whatever reason Django-stubs doesn't support
    # this (see issues below) so we just enforce that the field is used with _some_ enum
    # class rather than, say, a string – which should at least catch some errors.
    # https://github.com/typeddjango/django-stubs/issues/545
    # https://github.com/typeddjango/django-stubs/issues/336
    BaseTextField = models.Field[enum.Enum, enum.Enum]
else:
    BaseTextField = models.TextField


class EnumField(BaseTextField):
    """Custom field that ensures correct types for a column, defined by an Enum.

    Specifically, data is stored in the db as the string name, e.g.
    "PENDING"`, and when loaded from the db, is deserialized into the correct
    enum instance e.g. `RequestStatus.PENDING`.
    """

    def __init__(self, *args, enum=RequestStatus, **kwargs):
        self.enum = enum
        self.choices = [(i.value, i.name) for i in self.enum]
        super().__init__(*args, **kwargs)

    def from_db_value(self, value, expression, connection):
        if value is None:  # pragma: no cover
            return value
        return self.enum[value]

    def get_prep_value(self, value):
        try:
            return value.name
        except Exception as exc:
            raise exc.__class__(f"value should be instance of {self.enum}") from exc

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        # Only include kwarg if it's not the default
        if self.enum != RequestStatus:
            kwargs["enum"] = self.enum
        return name, path, args, kwargs


class RequestMetadata(models.Model):
    """A request for a set of files to be reviewed and potentially released."""

    id = models.TextField(  # noqa: A003
        primary_key=True, editable=False, default=local_request_id
    )

    workspace = models.TextField()
    status = EnumField(default=RequestStatus.PENDING, enum=RequestStatus)
    author = models.TextField()  # just username, as we have no User model
    created_at = models.DateTimeField(default=timezone.now)
    submitted_reviews = models.JSONField(default=dict)
    review_turn = models.IntegerField(default=0)
    # comma-separated list of submitted reviewers' usernames
    # we need to store this at the end of a turn
    turn_reviewers = models.TextField(null=True)

    def get_filegroups_to_dict(self):
        return {
            group_metadata.name: group_metadata.to_dict()
            for group_metadata in self.filegroups.all()
        }

    def to_dict(self):
        """Unpack the db data into a dict for the Request object."""
        return dict(
            id=self.id,
            workspace=self.workspace,
            status=self.status,
            author=self.author,
            created_at=self.created_at,
            filegroups=self.get_filegroups_to_dict(),
            submitted_reviews=self.submitted_reviews,
            review_turn=self.review_turn,
            turn_reviewers=set(self.turn_reviewers.split(","))
            if self.turn_reviewers
            else set(),
        )


class FileGroupMetadata(models.Model):
    """A group of files that share context and controls"""

    request = models.ForeignKey(
        RequestMetadata, related_name="filegroups", on_delete=models.CASCADE
    )
    name = models.TextField(default="default")
    context = models.TextField(default="")
    controls = models.TextField(default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("request", "name")

    def to_dict(self):
        """Unpack file group db data for FileGroup, RequestFile & Comment objects."""
        return dict(
            name=self.name,
            context=self.context,
            controls=self.controls,
            updated_at=self.updated_at,
            comments=[
                comment.to_dict()
                for comment in self.comments.all().order_by("created_at")
            ],
            files=[
                file_metadata.to_dict() for file_metadata in self.request_files.all()
            ],
        )


class FileGroupComment(models.Model):
    filegroup = models.ForeignKey(
        FileGroupMetadata, related_name="comments", on_delete=models.CASCADE
    )

    comment = models.TextField()
    author = models.TextField()  # just username, as we have no User model
    visibility = EnumField(enum=Visibility)
    review_turn = models.IntegerField()
    created_at = models.DateTimeField(default=timezone.now)

    def to_dict(self):
        return {
            "id": self.id,
            "comment": self.comment,
            "author": self.author,
            "created_at": self.created_at,
            "visibility": self.visibility,
            "review_turn": self.review_turn,
        }


class RequestFileMetadata(models.Model):
    """Represents attributes of a single file in a request"""

    request = models.ForeignKey(
        RequestMetadata,
        related_name="request_files",
        on_delete=models.CASCADE,
    )
    relpath = models.TextField()
    filegroup = models.ForeignKey(
        FileGroupMetadata, related_name="request_files", on_delete=models.CASCADE
    )
    # An opaque string use to identify the specific version of the file (in practice, a
    # hash – but we should not rely on that)
    file_id = models.TextField()
    filetype = EnumField(default=RequestFileType.OUTPUT, enum=RequestFileType)
    timestamp = models.FloatField()
    size = models.IntegerField()
    job_id = models.TextField()
    commit = models.TextField()
    repo = models.URLField()
    row_count = models.IntegerField(null=True)
    col_count = models.IntegerField(null=True)
    # released_at to be null if file has not been released
    released_at = models.DateTimeField(default=None, null=True)
    # just username, as we have no User model
    released_by = models.TextField(null=True)

    class Meta:
        unique_together = ("relpath", "request")

    def to_dict(self):
        return dict(
            relpath=Path(self.relpath),
            group=self.filegroup.name,
            file_id=self.file_id,
            filetype=self.filetype,
            timestamp=self.timestamp,
            size=self.size,
            commit=self.commit,
            repo=self.repo,
            job_id=self.job_id,
            row_count=self.row_count,
            col_count=self.col_count,
            reviews=[file_review.to_dict() for file_review in self.reviews.all()],
            released_at=self.released_at,
            released_by=self.released_by,
        )


class FileReview(models.Model):
    """An output checker's review of a file"""

    file = models.ForeignKey(
        RequestFileMetadata, related_name="reviews", on_delete=models.RESTRICT
    )
    reviewer = models.TextField()
    status = EnumField(default=RequestFileVote.CHANGES_REQUESTED, enum=RequestFileVote)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("file", "reviewer")

    def to_dict(self):
        """Convert a FileReview object into a dict"""
        return dict(
            reviewer=self.reviewer,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class AuditLog(models.Model):
    type = EnumField(enum=AuditEventType)
    user = models.TextField()
    workspace = models.TextField(null=True)
    request = models.TextField(null=True)
    path = models.TextField(null=True)
    extra = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["workspace"]),
            models.Index(fields=["request"]),
        ]

    # TODO: pretend to be append-only by overriding save() and delete()?

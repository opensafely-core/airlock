import enum
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone

# We could create our own type stubs for this module but our use of it is so simple that
# it's not really worth it
from ulid import ulid  # type: ignore

from airlock.business_logic import (
    AuditEventType,
    FileReviewStatus,
    RequestFileType,
    RequestStatus,
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


class RequestMetadata(models.Model):
    """A request for a set of files to be reviewed and potentially released."""

    id = models.TextField(  # noqa: A003
        primary_key=True, editable=False, default=local_request_id
    )

    workspace = models.TextField()
    status = EnumField(default=RequestStatus.PENDING, enum=RequestStatus)
    author = models.TextField()  # just username, as we have no User model
    created_at = models.DateTimeField(default=timezone.now)


class FileGroupMetadata(models.Model):
    """A group of files that share context and controls"""

    request = models.ForeignKey(
        RequestMetadata, related_name="filegroups", on_delete=models.CASCADE
    )
    name = models.TextField(default="default")

    class Meta:
        unique_together = ("request", "name")


class RequestFileMetadata(models.Model):
    """Represents attributes of a single file in a request"""

    relpath = models.TextField()
    filegroup = models.ForeignKey(
        FileGroupMetadata, related_name="request_files", on_delete=models.CASCADE
    )
    # An opaque string use to identify the specific version of the file (in practice, a
    # hash – but we should not rely on that)
    file_id = models.TextField()
    filetype = EnumField(default=RequestFileType.OUTPUT, enum=RequestFileType)

    class Meta:
        unique_together = ("relpath", "filegroup")


class FileReview(models.Model):
    """An output checker's review of a file"""

    file = models.ForeignKey(
        RequestFileMetadata, related_name="reviews", on_delete=models.CASCADE
    )
    reviewer = models.TextField()
    status = EnumField(default=FileReviewStatus.REJECTED, enum=FileReviewStatus)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("file", "reviewer")


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

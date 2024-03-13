from django.db import models
from django.utils import timezone
from ulid import ulid

from airlock.business_logic import Status


def local_request_id():
    return str(ulid())


class StatusField(models.TextField):
    """Custom field that ensures correct types for status column.

    Specifically, dasta is stored in the db as the string name, e.g.
    "PENDING"`, and when loaded from the db, is deserialized into the correct
    enum instance e.g. `Status.PENDING`.
    """

    choices = [(i.value, i.name) for i in Status]

    def from_db_value(self, value, expression, connection):
        if value is None:  # pragma: no cover
            return value

        return Status[value]

    def get_prep_value(self, value):
        try:
            return value.name
        except Exception as exc:
            raise exc.__class__("value should be instance of Status") from exc


class RequestMetadata(models.Model):
    """A request for a set of files to be reviewed and potentially released."""

    id = models.TextField(  # noqa: A003
        primary_key=True, editable=False, default=local_request_id
    )

    workspace = models.TextField()
    status = StatusField(default=Status.PENDING)
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

    class Meta:
        unique_together = ("relpath", "filegroup")

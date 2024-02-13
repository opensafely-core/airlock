from django.db import models
from django.utils import timezone
from ulid import ulid


def local_request_id():
    return str(ulid())


class RequestMetadata(models.Model):
    """A request for a set of files to be reviewed and potentially released."""

    id = models.TextField(  # noqa: A003
        primary_key=True, editable=False, default=local_request_id
    )

    workspace = models.TextField()
    author = models.TextField()  # just username, as we have no User model
    created_at = models.DateTimeField(default=timezone.now)

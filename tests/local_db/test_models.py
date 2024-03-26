import pytest

from local_db import models


@pytest.mark.django_db
def test_enum_field():
    # use RequestMetadata to test bad status
    with pytest.raises(AttributeError):
        models.RequestMetadata.objects.create(
            workspace="workspace",
            author="user",
            status="unknown",
        )

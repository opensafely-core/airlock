import pytest
from django.core.management import call_command

from local_db.models import RequestFileMetadata
from tests import factories


@pytest.mark.django_db
def test_command(bll):
    workspace = factories.create_workspace("workspace")
    release_request = factories.create_release_request(workspace)
    factories.write_request_file(
        release_request, "group", "subdir/file.txt", "some content"
    )

    # its current manifest data will reflect the generated manifest.json
    # change them back to the defaults from the migration that added the fields
    file_meta = RequestFileMetadata.objects.get()
    file_meta.commit = "abcd"
    file_meta.repo = "http://example.com/test"
    file_meta.size = 1
    file_meta.job_id = "1234"
    file_meta.timestamp = 1
    file_meta.save()

    workspace = bll.get_workspace(
        "workspace", factories.create_user(workspaces=["workspace"])
    )
    manifest = workspace.get_manifest_for_file(file_meta.relpath)
    for attr in ["commit", "size", "job_id", "timestamp", "repo"]:
        assert getattr(file_meta, attr) != manifest[attr]

    call_command("backpopulate_file_manifest_data")

    # Confirm the object has been updated with the data from the manifest.json
    file_meta.refresh_from_db()
    for attr in ["commit", "size", "job_id", "timestamp", "repo"]:
        assert getattr(file_meta, attr) == manifest[attr]

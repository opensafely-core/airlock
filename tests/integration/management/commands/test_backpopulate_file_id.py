import pytest
from django.core.management import call_command

from local_db.models import RequestFileMetadata
from tests import factories


@pytest.mark.django_db
def test_command():
    # Create a ReleaseRequest with a single file
    factories.write_workspace_file("workspace", "subdir/file.txt", "some content")
    release_request = factories.create_release_request("workspace")
    factories.create_filegroup(
        release_request, group_name="default_group", filepaths=["subdir/file.txt"]
    )

    # Determine its current path and its "old style" path
    file_meta = RequestFileMetadata.objects.get()
    relpath_with_group = f"{file_meta.filegroup.name}/{file_meta.relpath}"
    path_with_hash = release_request.abspath(relpath_with_group)
    old_style_path = release_request.root() / file_meta.relpath

    # Move the file to its old location
    old_style_path.parent.mkdir(parents=True, exist_ok=True)
    path_with_hash.rename(old_style_path)

    # Remove the file hash from the database record
    file_meta.file_id = ""
    file_meta.save()

    call_command("backpopulate_file_id")

    # Confirm the database record now contains a hash
    file_meta.refresh_from_db()
    assert file_meta.file_id != ""

    # Confirm that the file is in its expected location and not in its old location
    assert path_with_hash.exists()
    assert not old_style_path.exists()

    # Confirm that now empty directory has been removed
    assert not old_style_path.parent.exists()

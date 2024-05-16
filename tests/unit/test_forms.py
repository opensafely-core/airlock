import pytest

from airlock.forms import AddFilesForm
from tests import factories


pytestmark = pytest.mark.django_db


def test_add_files_form_no_release_request():
    form = AddFilesForm(release_request=None)
    assert form.fields["filegroup"].choices == [("default", "default")]


def test_add_files_form_with_files():
    form = AddFilesForm(release_request=None, files=["foo.txt", "bar.txt"])
    assert form.fields["file_0"].initial == "foo.txt"
    assert form.fields["filetype_0"].initial == "OUTPUT"
    assert form.fields["filetype_0"].choices == [
        ("OUTPUT", "Output"),
        ("SUPPORTING", "Supporting"),
    ]
    assert form.fields["file_1"].initial == "bar.txt"
    assert form.fields["filetype_1"].initial == "OUTPUT"
    assert form.fields["filetype_1"].choices == [
        ("OUTPUT", "Output"),
        ("SUPPORTING", "Supporting"),
    ]


def test_add_files_form_empty_release_request():
    release_request = factories.create_release_request("workspace")
    form = AddFilesForm(release_request=release_request)
    assert form.fields["filegroup"].choices == [("default", "default")]


def test_add_files_form_filegroup_choices():
    release_request = factories.create_release_request("workspace")
    for group in ["b_group", "a_group"]:
        factories.create_filegroup(release_request, group)
    release_request = factories.refresh_release_request(release_request)

    other_release_request = factories.create_release_request("workspace1")
    factories.create_filegroup(other_release_request, "other_group")

    form = AddFilesForm(release_request=release_request)
    # default group is always first, other choices are sorted
    assert form.fields["filegroup"].choices == [
        ("default", "default"),
        ("a_group", "a_group"),
        ("b_group", "b_group"),
    ]


@pytest.mark.parametrize(
    "new_group_name,is_valid",
    [
        # Can create a new default group if one doesn't already exist
        ("default", True),
        # Can't create a duplicate group
        ("test", False),
        # Can't create a duplicate group, case insensitive
        ("Test", False),
        # Can create a group with the same name as a group on another request
        ("test 1", True),
    ],
)
def test_add_files_form_new_filegroup(new_group_name, is_valid):
    release_request = factories.create_release_request("workspace")
    factories.create_filegroup(release_request, "test")
    release_request = factories.refresh_release_request(release_request)

    other_release_request = factories.create_release_request("workspace1")
    factories.create_filegroup(other_release_request, "test 1")

    data = {
        # a filegroup is always in the POST data, ignored if
        # new_filegroup also present
        "filegroup": "default",
        "new_filegroup": new_group_name,
    }
    form = AddFilesForm(data, release_request=release_request)
    assert form.is_valid() == is_valid

import pytest
from django import forms

from airlock.forms import AddFileForm, FileTypeFormSet, MultiselectForm
from tests import factories


pytestmark = pytest.mark.django_db


def test_add_files_form_no_release_request():
    form = AddFileForm(release_request=None)
    # Use type narrowing to persuade mypy this has a `choices` attr
    assert isinstance(form.fields["filegroup"], forms.ChoiceField)
    assert form.fields["filegroup"].choices == [("default", "default")]


def test_filetype_formset():
    formset = FileTypeFormSet(initial=[{"file": f} for f in ["foo.txt", "bar.txt"]])
    form1 = list(formset)[0]
    form2 = list(formset)[1]

    assert form1.fields["file"].get_bound_field(form1, "file").value() == "foo.txt"
    assert form1.fields["filetype"].initial == "OUTPUT"
    assert form1.fields["filetype"].choices == [
        ("OUTPUT", "Output"),
        ("SUPPORTING", "Supporting"),
    ]
    assert form2.fields["file"].get_bound_field(form2, "file").value() == "bar.txt"
    assert form2.fields["filetype"].initial == "OUTPUT"
    assert form2.fields["filetype"].choices == [
        ("OUTPUT", "Output"),
        ("SUPPORTING", "Supporting"),
    ]


def test_add_files_form_empty_release_request():
    release_request = factories.create_release_request("workspace")
    form = AddFileForm(release_request=release_request)
    # Use type narrowing to persuade mypy this has a `choices` attr
    assert isinstance(form.fields["filegroup"], forms.ChoiceField)
    assert form.fields["filegroup"].choices == [("default", "default")]


def test_add_files_form_filegroup_choices():
    release_request = factories.create_release_request("workspace")
    for group in ["b_group", "a_group"]:
        factories.create_filegroup(release_request, group)
    release_request = factories.refresh_release_request(release_request)

    other_release_request = factories.create_release_request("workspace1")
    factories.create_filegroup(other_release_request, "other_group")

    form = AddFileForm(release_request=release_request)
    # Use type narrowing to persuade mypy this has a `choices` attr
    assert isinstance(form.fields["filegroup"], forms.ChoiceField)
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
        "next_url": "/next",
    }
    form = AddFileForm(data, release_request=release_request)
    assert form.is_valid() == is_valid


def test_multiselect_validation():
    form = MultiselectForm({})
    assert form.is_valid() is False
    assert form.errors["next_url"] == ["This field is required."]

    form = MultiselectForm({"next_url": "https://evil.com/"})
    assert form.is_valid() is False
    assert form.errors["next_url"] == [
        "Must be relative url (no scheme/hostname) but with absolute url path"
    ]

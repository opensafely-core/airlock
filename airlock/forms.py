from django import forms

from airlock.business_logic import FileGroup, RequestFileType


class ListField(forms.Field):
    # adding a multiselect widget means Django magically inferes that this is
    # multi value field, and parses values into a list.
    #
    # We don't even use the widget to render the form at any point.
    widget = forms.SelectMultiple


class InternalRedirectField(forms.CharField):
    """Ensure is internal url path, not absolute url."""

    def validate(self, value):
        super().validate(value)
        if value[0] != "/":
            raise forms.ValidationError("Must be absolute url path")


class TokenLoginForm(forms.Form):
    user = forms.CharField()
    token = forms.CharField()


class MultiselectForm(forms.Form):
    """This is used to perform an action on multiple files."""

    action = forms.CharField()  # which submit button was used
    next_url = InternalRedirectField()  # where do we return to when complete
    selected = ListField()  # the list of files selected


class AddFilesForm(forms.Form):
    FILETYPE_CHOICES = [
        (RequestFileType.OUTPUT.name, RequestFileType.OUTPUT.name.title()),
        (RequestFileType.SUPPORTING.name, RequestFileType.SUPPORTING.name.title()),
    ]

    next_url = InternalRedirectField()
    filegroup = forms.ChoiceField(required=False)
    new_filegroup = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        release_request = kwargs.pop("release_request")
        self.files_to_add = kwargs.pop("files", [])
        super().__init__(*args, **kwargs)

        if release_request:
            self.filegroup_names = release_request.filegroups.keys()
        else:
            self.filegroup_names = set()
        group_names = sorted(self.filegroup_names - {"default"})
        group_choices = [(name, name) for name in ["default", *group_names]]
        # Use type narrowing to persuade mpy this has a `choices` attr
        assert isinstance(self.fields["filegroup"], forms.ChoiceField)
        self.fields["filegroup"].choices = group_choices
        self.fields["new_filegroup"]

        # dynamically add 2 enumerated fields per file
        for i, filename in enumerate(self.files_to_add):
            # filename as hidden field
            self.fields[f"file_{i}"] = forms.CharField(
                required=True,
                initial=filename,
                widget=forms.HiddenInput(),
            )
            # filetype for this file
            self.fields[f"filetype_{i}"] = forms.ChoiceField(
                choices=self.FILETYPE_CHOICES,
                required=True,
                initial=RequestFileType.OUTPUT.name,
            )

    def clean_new_filegroup(self):
        new_filegroup = self.cleaned_data.get("new_filegroup", "").lower()
        if new_filegroup in [fg.lower() for fg in self.filegroup_names]:
            self.add_error(
                "new_filegroup",
                f"File group with name '{new_filegroup}' already exists",
            )
        else:
            return new_filegroup

    def file_fields(self):
        """Template helper to loop through each files fields."""
        for i, filename in enumerate(self.files_to_add):
            yield {
                "file": self.fields[f"file_{i}"],
                "filetype": self.fields[f"filetype_{i}"],
            }


class GroupEditForm(forms.Form):
    context = forms.CharField(required=False)
    controls = forms.CharField(required=False)

    @classmethod
    def from_filegroup(cls, filegroup: FileGroup, *args, **kwargs):
        data = {
            "context": filegroup.context,
            "controls": filegroup.controls,
        }
        return cls(data, *args, **kwargs)


class GroupCommentForm(forms.Form):
    comment = forms.CharField()

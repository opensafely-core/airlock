from django import forms

from airlock.business_logic import RequestFileType


class TokenLoginForm(forms.Form):
    user = forms.CharField()
    token = forms.CharField()


class AddFileForm(forms.Form):
    filegroup = forms.ChoiceField(required=False)
    new_filegroup = forms.CharField(required=False)
    filetype = forms.ChoiceField(
        required=True,
        choices=[
            (i.name, i.name.title())
            for i in RequestFileType
            if i != RequestFileType.WITHDRAWN
        ],
        initial=RequestFileType.OUTPUT.name,
    )

    def __init__(self, *args, **kwargs):
        release_request = kwargs.pop("release_request")
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

    def clean_new_filegroup(self):
        new_filegroup = self.cleaned_data.get("new_filegroup", "").lower()
        if new_filegroup in [fg.lower() for fg in self.filegroup_names]:
            self.add_error(
                "new_filegroup",
                f"File group with name '{new_filegroup}' already exists",
            )
        else:
            return new_filegroup


class GroupEditForm(forms.Form):
    context = forms.CharField(required=False)
    controls = forms.CharField(required=False)


class GroupCommentForm(forms.Form):
    comment = forms.CharField()

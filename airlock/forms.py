from django import forms
from django.forms.formsets import BaseFormSet, formset_factory
from django.utils.safestring import mark_safe

from airlock.enums import RequestFileType, Visibility
from airlock.models import FileGroup


class ListField(forms.Field):
    # adding a multiselect widget means Django magically inferes that this is
    # multi value field, and parses values into a list.
    #
    # We don't even use the widget to render the form at any point.
    widget = forms.SelectMultiple


class InternalRedirectField(forms.CharField):
    """Ensure is internal url path, not absolute url."""

    widget = forms.HiddenInput

    def validate(self, value):
        super().validate(value)
        if value[0] != "/":
            raise forms.ValidationError(
                "Must be relative url (no scheme/hostname) but with absolute url path"
            )


class TokenLoginForm(forms.Form):
    user = forms.CharField()
    token = forms.CharField()


class MultiselectForm(forms.Form):
    """This is used to perform an action on multiple files."""

    action = forms.CharField()  # which submit button was used
    next_url = InternalRedirectField()  # where do we return to when complete
    # the list of files selected
    selected = ListField(
        error_messages={"required": "You must select at least one file"},
    )


class AddFileForm(forms.Form):
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
        # Use type narrowing to persuade mypy this has a `choices` attr
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


class FileForm(forms.Form):
    file = forms.CharField(
        required=True,
        widget=forms.HiddenInput(),
    )


class FileTypeForm(forms.Form):
    FILETYPE_CHOICES = [
        (RequestFileType.OUTPUT.name, RequestFileType.OUTPUT.name.title()),
        (RequestFileType.SUPPORTING.name, RequestFileType.SUPPORTING.name.title()),
    ]

    file = forms.CharField(
        required=True,
        widget=forms.HiddenInput(),
    )
    filetype = forms.ChoiceField(
        choices=FILETYPE_CHOICES,
        required=True,
        initial=RequestFileType.OUTPUT.name,
        widget=forms.RadioSelect(
            attrs={"class": "filetype-radio flex items-center gap-2"}
        ),
    )


class RequiredOneBaseFormSet(BaseFormSet):  # type: ignore
    def clean(self):
        """
        Add custom validation to ensure at least one form has data.
        """
        if any(self.errors):
            return  # pragma: no cover

        # Check that at least one form has data
        valid_forms = 0
        for form in self.forms:  # pragma: no cover
            if form.cleaned_data and any(form.cleaned_data.values()):
                valid_forms += 1

        if valid_forms < 1:
            raise forms.ValidationError("At least one form must be completed.")


FileTypeFormSet = formset_factory(FileTypeForm, extra=0, formset=RequiredOneBaseFormSet)
FileFormSet = formset_factory(FileForm, extra=0, formset=RequiredOneBaseFormSet)


class GroupEditForm(forms.Form):
    context = forms.CharField(
        required=False,
        help_text=mark_safe(
            """
            <div class="c2-help">
                <p>Please describe the contents of these outputs to help reviewers. This includes:</p>
                <ul>
                    <li>Variable descriptions</li>
                    <li>A description and count of the underlying population for each output if it is not included in the output.</li>
                    <li>Specify population size and degrees of freedom for all regression outputs.</li>
                    <li>Relationship to other data/tables which through combination may introduce secondary disclosive risks.</li>
                </ul>
            </div>
            """
        ),
    )
    controls = forms.CharField(
        required=False,
        help_text=mark_safe(
            """
            <div class="c2-help">
                <p>Please describe the statistical disclosure controls you have applied to these outputs. This includes:</p>
                <ul>
                    <li>The threshold used for low number suppression.</li>
                    <li>The rounding approach used.</li>
                </ul>
            </div>
            """
        ),
    )

    def __init__(self, *args, **kwargs):
        inline = kwargs.pop("inline", False)
        super().__init__(*args, **kwargs)
        if inline:
            # Hide the help text if the form is inlined (i.e. it's)
            # being opened as readonly from a file view
            for field in ["context", "controls"]:
                self.fields[field].help_text = ""

    @classmethod
    def from_filegroup(cls, filegroup: FileGroup, *args, **kwargs):
        data = {
            "context": filegroup.context,
            "controls": filegroup.controls,
        }
        return cls(data, *args, **kwargs)


class GroupCommentForm(forms.Form):
    comment = forms.CharField()
    visibility = forms.ChoiceField(
        choices=[],
        required=True,
        widget=forms.RadioSelect(
            attrs={"class": "filetype-radio flex items-center gap-2"}
        ),
    )

    def __init__(self, visibilities, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # filter only the supplied visibilities, as it can vary depending on
        # user and request state
        choices = [
            (k.name, v) for k, v in Visibility.choices().items() if k in visibilities
        ]
        self.fields["visibility"].choices = choices  # type: ignore
        # choose first in list as default selected value
        self.fields["visibility"].initial = choices[0][0]  # type: ignore


class GroupCommentDeleteForm(forms.Form):
    comment_id = forms.CharField()

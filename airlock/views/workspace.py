from collections import defaultdict

from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers
from opentelemetry import trace

from airlock import exceptions, permissions, policies
from airlock.business_logic import bll
from airlock.enums import PathType, RequestFileType, WorkspaceFileStatus
from airlock.file_browser_api import get_workspace_tree
from airlock.forms import (
    AddFileForm,
    FileFormSet,
    FileTypeFormSet,
    MultiselectForm,
)
from airlock.types import FilePath
from airlock.views.helpers import (
    ButtonContext,
    display_form_errors,
    display_multiple_messages,
    get_next_url_from_form,
    get_path_item_from_tree_or_404,
    get_workspace_or_raise,
    serve_file,
)
from services.tracing import instrument


tracer = trace.get_tracer_provider().get_tracer("airlock")


def grouped_workspaces(workspaces):
    workspaces_by_project = defaultdict(list)
    for workspace in workspaces:
        workspaces_by_project[workspace.project()].append(workspace)
    # sort projects by ongoing status, then name
    for project, workspaces in sorted(
        workspaces_by_project.items(), key=lambda x: (not x[0].is_ongoing, x[0].name)
    ):
        # for each project, sort workspaces by archived status, then name
        yield project, list(sorted(workspaces, key=lambda x: (x.is_archived(), x.name)))


@instrument
def workspace_index(request):
    workspaces = bll.get_workspaces_for_user(request.user)
    projects = dict(grouped_workspaces(workspaces))
    return TemplateResponse(
        request,
        "workspaces.html",
        {"projects": projects, "workspace_type": "workspaces"},
    )


@instrument
def copilot_workspace_index(request):
    workspaces = bll.get_copiloted_workspaces_for_user(request.user)
    projects = dict(grouped_workspaces(workspaces))
    return TemplateResponse(
        request,
        "workspaces.html",
        {"projects": projects, "workspace_type": "copiloted workspaces"},
    )


def _get_dir_button_context(user, workspace):
    multiselect_add_btn = ButtonContext.with_workspace_defaults(
        workspace.name, "workspace_multiselect"
    )
    context = {"multiselect_add": multiselect_add_btn}

    # We always show the button unless the workspace is inactive (but it may be disabled)
    if not workspace.is_active():
        return context

    multiselect_add_btn.show = True

    if permissions.user_can_action_request_for_workspace(user, workspace.name):
        if workspace.current_request is None or workspace.current_request.is_editing():
            multiselect_add_btn.disabled = False
        else:
            multiselect_add_btn.tooltip = (
                "There is currently a request under review for this workspace and you "
                "cannot modify it or start a new one until it is reviewed."
            )
    else:
        multiselect_add_btn.tooltip = (
            "You do not have permission to add files to a request."
        )
    return context


def _get_file_button_context(user, workspace, path_item):
    # The add-file button on the file view also uses the multiselect
    add_file_btn = ButtonContext.with_workspace_defaults(
        workspace.name, "workspace_multiselect"
    )

    # The button may show Add File or Update File, depending on the
    # state of the file
    file_status = workspace.get_workspace_file_status(path_item.relpath)
    update_file = file_status == WorkspaceFileStatus.CONTENT_UPDATED
    add_file = not update_file

    context = {
        "add_file": add_file,
        "update_file": update_file,
        "add_file_button": add_file_btn,
    }
    # We always show the button unless the workspace is inactive (but it may be disabled)
    if not workspace.is_active():
        return context

    add_file_btn.show = True
    # Enable the add file form button if the user has permission to add a
    # file and/or create a request
    # and also this pathitem is a file that can be either added or
    # replaced
    # We first check the context for files generally by looking at the
    # dir context. If the button was disabled there, it will be disabled
    # for the file context too, with the same tooltips
    dir_button = _get_dir_button_context(user, workspace)["multiselect_add"]
    if dir_button.disabled:
        add_file_btn.tooltip = dir_button.tooltip
    else:
        # Check we can add or update the specific file
        if policies.can_add_file_to_request(workspace, path_item.relpath):
            add_file_btn.disabled = False
        elif policies.can_replace_file_in_request(workspace, path_item.relpath):
            add_file_btn.disabled = False
        else:
            # disabled due to specific file state; update the tooltips to say why
            if not path_item.is_valid():
                add_file_btn.tooltip = "This file type cannot be added to a request"
            elif file_status == WorkspaceFileStatus.RELEASED:
                add_file_btn.tooltip = "This file has already been released"
            else:
                # if it's a valid file, and it's not already released,
                # but the uer can's add or update it, it must already
                # be on the request
                assert file_status == WorkspaceFileStatus.UNDER_REVIEW
                add_file_btn.tooltip = (
                    "This file has already been added to the current request"
                )

    return context


def get_button_context(path_item, user, workspace):
    """
    Return a context dict defining the status of the buttons
    shown at the top of the content panel
    """
    match path_item.type:
        case PathType.FILE:
            return _get_file_button_context(user, workspace, path_item)
        case PathType.DIR:
            return _get_dir_button_context(user, workspace)
        case _:
            return {}


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
@instrument(func_attributes={"workspace": "workspace_name"})
def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = get_workspace_or_raise(request.user, workspace_name)
    template_dir = "file_browser/workspace/"
    template = template_dir + "index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/contents.html"
        selected_only = True

    tree = get_workspace_tree(workspace, path, selected_only)

    path_item = get_path_item_from_tree_or_404(tree, path)

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    content_buttons = get_button_context(path_item, request.user, workspace)

    project = workspace.project()

    if path_item.is_directory() or path not in workspace.manifest["outputs"]:
        code_url = None
    else:
        code_url = (
            reverse(
                "code_view",
                kwargs={
                    "workspace_name": workspace.name,
                    "commit": workspace.get_manifest_for_file(path).get("commit"),
                    "path": "project.yaml",
                },
            )
            + f"?return_url={workspace.get_url(path)}"
        )

    return TemplateResponse(
        request,
        template,
        {
            "template_dir": template_dir,
            "workspace": workspace,
            "root": tree,
            "path_item": path_item,
            "title": f"Files for workspace {workspace.display_name()}",
            "current_request": workspace.current_request,
            # for add file buttons
            "content_buttons": content_buttons,
            # for workspace summary page
            "project": project,
            # for code button
            "code_url": code_url,
            "include_code": code_url is not None,
        },
    )


@instrument(func_attributes={"workspace": "workspace_name"})
@xframe_options_sameorigin
@require_http_methods(["GET"])
def workspace_contents(request, workspace_name: str, path: str):
    workspace = get_workspace_or_raise(request.user, workspace_name)

    try:
        abspath = workspace.abspath(path)
    except exceptions.FileNotFound:
        raise Http404()

    if not abspath.is_file():
        return HttpResponseBadRequest()

    bll.audit_workspace_file_access(workspace, FilePath(path), request.user)

    plaintext = request.GET.get("plaintext", False)
    renderer = workspace.get_renderer(FilePath(path), plaintext=plaintext)
    return serve_file(request, renderer)


@instrument(func_attributes={"workspace": "workspace_name"})
@require_http_methods(["POST"])
def workspace_multiselect(request, workspace_name: str):
    """User has selected multiple files and wishes to perform an action on them.


    In some cases, we need to return a further form as a modal for more information for each file.
    In others, we can just perform the requested action and redirect.
    """
    workspace = get_workspace_or_raise(request.user, workspace_name)

    multiform = MultiselectForm(request.POST)

    if multiform.is_valid():
        action = multiform.cleaned_data["action"]

        if action == "add_files":
            return multiselect_add_files(request, multiform, workspace)
        if action == "update_files":
            return multiselect_update_files(request, multiform, workspace)
        # TODO: withdraw action
        else:
            raise Http404(f"Invalid action {action}")
    else:
        display_form_errors(request, multiform.errors)

        # redirect back where we came from
        url = get_next_url_from_form(workspace, multiform)

        # tell HTMX to redirect us
        response = HttpResponse("", status=400)
        response.headers["HX-Redirect"] = url
        return response


def multiselect_add_files(request, multiform, workspace):
    files_to_add = []
    files_ignored = {}

    # validate which files can be added
    for f in multiform.cleaned_data["selected"]:
        workspace.abspath(f)  # validate path

        relpath = FilePath(f)
        state = workspace.get_workspace_file_status(relpath)
        if policies.can_add_file_to_request(workspace, relpath):
            files_to_add.append(f)
        elif state == WorkspaceFileStatus.RELEASED:
            files_ignored[f] = "already released"
        else:
            rfile = workspace.current_request.get_request_file_from_output_path(f)
            files_ignored[f] = f"already in group {rfile.group}"

    add_file_form = AddFileForm(
        release_request=workspace.current_request,
        initial={"next_url": multiform.cleaned_data["next_url"]},
    )

    filetype_formset = FileTypeFormSet(
        initial=[{"file": f} for f in files_to_add],
    )

    return TemplateResponse(
        request,
        template="add_or_change_files.html",
        context={
            "form": add_file_form,
            "formset": filetype_formset,
            "files_ignored": files_ignored,
            "no_valid_files": len(files_to_add) == 0,
            "form_url": reverse(
                "workspace_add_file",
                kwargs={"workspace_name": workspace.name},
            ),
            "modal_title": "Add Files to Request",
            "modal_button_text": "Add Files to Request",
        },
    )


def multiselect_update_files(request, multiform, workspace):
    files_to_add = []
    files_ignored = {}

    # validate which files can be added
    for f in multiform.cleaned_data["selected"]:
        workspace.abspath(f)  # validate path

        if policies.can_update_file_on_request(workspace, FilePath(f)):
            files_to_add.append(f)
        else:
            files_ignored[f] = "file cannot be updated"

    add_file_form = AddFileForm(
        release_request=workspace.current_request,
        initial={"next_url": multiform.cleaned_data["next_url"]},
    )

    filetype_formset = FileFormSet(
        initial=[{"file": f} for f in files_to_add],
    )

    return TemplateResponse(
        request,
        template="update_files.html",
        context={
            "form": add_file_form,
            "formset": filetype_formset,
            "files_ignored": files_ignored,
            "no_valid_files": len(files_to_add) == 0,
            # "update": True,
            "add_file_url": reverse(
                "workspace_update_file",
                kwargs={"workspace_name": workspace.name},
            ),
        },
    )


# also displays errors if present
def add_or_update_form_is_valid(request, form, formset):
    errors = False

    if not form.is_valid():
        display_form_errors(request, form.errors)
        errors = True

    if not formset.is_valid():
        form_errors = [f.errors for f in formset]
        non_form_errors = formset.non_form_errors()
        if non_form_errors:  # pragma: no cover
            form_errors = [{"": non_form_errors}] + form_errors
        display_form_errors(request, *form_errors)
        errors = True

    return errors


def check_all_files_exist(workspace, formset):
    try:
        for formset_form in formset:
            relpath = formset_form.cleaned_data["file"]
            workspace.abspath(relpath)
    except exceptions.FileNotFound:
        raise Http404(f"file {relpath} does not exist")


@instrument(func_attributes={"workspace": "workspace_name"})
@require_http_methods(["POST"])
def workspace_add_file_to_request(request, workspace_name):
    workspace = get_workspace_or_raise(request.user, workspace_name)
    release_request = bll.get_or_create_current_request(workspace_name, request.user)
    form = AddFileForm(request.POST, release_request=release_request)
    formset = FileTypeFormSet(request.POST)

    errors = add_or_update_form_is_valid(request, form, formset)
    next_url = get_next_url_from_form(workspace, form)

    if errors:
        # redirect back where we came from with errors
        return redirect(next_url)

    check_all_files_exist(workspace, formset)

    group_name = (
        form.cleaned_data.get("new_filegroup")
        or form.cleaned_data.get("filegroup")
        or ""
    )
    error_msgs = []
    success_msgs = []
    for formset_form in formset:
        relpath = formset_form.cleaned_data["file"]
        filetype = RequestFileType[formset_form.cleaned_data["filetype"]]

        status = workspace.get_workspace_file_status(FilePath(relpath))
        try:
            if status == WorkspaceFileStatus.WITHDRAWN:
                bll.add_withdrawn_file_to_request(
                    release_request, relpath, request.user, group_name, filetype
                )
                success_msg = f"added to request (file group '{group_name}')"
            else:
                bll.add_file_to_request(
                    release_request, relpath, request.user, group_name, filetype
                )
                success_msg = f"added to request (file group '{group_name}')"
        except exceptions.APIException as err:
            # This exception can be raised if the file has already been added
            # (to any group on the request)
            error_msgs.append(f"{relpath}: {err}")
        else:
            success_msgs.append(
                f"{relpath}: {filetype.name.title()} file has been {success_msg}"
            )

    display_multiple_messages(request, success_msgs, "success")
    display_multiple_messages(request, error_msgs, "error")

    # redirect back where we came from
    return redirect(next_url)


@instrument(func_attributes={"workspace": "workspace_name"})
@require_http_methods(["POST"])
def workspace_update_file_in_request(request, workspace_name):
    workspace = get_workspace_or_raise(request.user, workspace_name)
    release_request = bll.get_or_create_current_request(workspace_name, request.user)
    form = AddFileForm(request.POST, release_request=release_request)
    formset = FileFormSet(request.POST)

    errors = add_or_update_form_is_valid(request, form, formset)
    next_url = get_next_url_from_form(workspace, form)

    if errors:
        # redirect back where we came from with errors
        return redirect(next_url)

    check_all_files_exist(workspace, formset)

    error_msgs = []
    success_msgs = []
    for formset_form in formset:
        relpath = formset_form.cleaned_data["file"]

        try:
            bll.update_file_in_request(release_request, relpath, request.user)
        except exceptions.APIException as err:
            error_msgs.append(f"{relpath}: {err}")
        else:
            success_msgs.append(f"{relpath}: file has been updated in request")

    display_multiple_messages(request, success_msgs, "success")
    display_multiple_messages(request, error_msgs, "error")

    # redirect back where we came from
    return redirect(next_url)

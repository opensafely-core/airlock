from collections import defaultdict

from django.http import Http404, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers
from opentelemetry import trace

from airlock.business_logic import (
    RequestFileType,
    bll,
)
from airlock.file_browser_api import get_workspace_tree
from airlock.forms import AddFileForm, FileTypeFormSet, MultiselectForm
from airlock.types import UrlPath, WorkspaceFileStatus
from airlock.users import ActionDenied
from airlock.views.helpers import (
    display_form_errors,
    display_multiple_messages,
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
    return TemplateResponse(request, "workspaces.html", {"projects": projects})


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
@instrument(func_attributes={"workspace": "workspace_name"})
def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = get_workspace_or_raise(request.user, workspace_name)
    template = "file_browser/index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/contents.html"
        selected_only = True

    tree = get_workspace_tree(workspace, path, selected_only)

    path_item = get_path_item_from_tree_or_404(tree, path)

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    # Add file / add files buttons
    #
    # Only show the add files multiselect button if the user is allowed to
    # create a request (if they are an output-checker they are allowed to
    # view all workspaces, but not necessarily create requests for them)
    # If there already is a current request, only show the multiselect add
    # if the request is in an author-editable state (pending/returned)
    #
    try:
        request.user.verify_can_action_request(workspace_name)
        can_action_request = True
    except ActionDenied:
        can_action_request = False

    multiselect_add = can_action_request and (
        workspace.current_request is None or workspace.current_request.is_in_draft()
    )

    valid_states_to_add = [
        WorkspaceFileStatus.UNRELEASED,
        WorkspaceFileStatus.CONTENT_UPDATED,
    ]

    # Only show the add file form button if the multiselect_add condition is true,
    # and also this pathitem is a file that can be added to a request - i.e. it is a
    # file and it's not already on the current request for the user
    add_file = (
        multiselect_add
        and path_item.is_valid()
        and workspace.get_workspace_file_status(path_item.relpath)
        in valid_states_to_add
    )

    activity = []
    project = workspace.project()

    # we are viewing the root, so load workspace audit log
    if path == "":
        activity = bll.get_audit_log(
            workspace=workspace.name, exclude_readonly=True, size=20
        )

    if path_item.is_directory() or path not in workspace.manifest["outputs"]:
        code_url = None
    else:
        code_url = (
            reverse(
                "code_view",
                kwargs={
                    "workspace_name": workspace.name,
                    "commit": workspace.get_manifest_for_file(path).get("commit"),
                },
            )
            + f"?return_url={workspace.get_url(path)}"
        )

    return TemplateResponse(
        request,
        template,
        {
            "workspace": workspace,
            "root": tree,
            "path_item": path_item,
            "is_supporting_file": False,
            "context": "workspace",
            "title": f"Files for workspace {workspace.display_name()}",
            "request_file_url": reverse(
                "workspace_add_file",
                kwargs={"workspace_name": workspace_name},
            ),
            "current_request": workspace.current_request,
            # for add file buttons
            "add_file": add_file,
            "multiselect_add": multiselect_add,
            "multiselect_url": reverse(
                "workspace_multiselect",
                kwargs={"workspace_name": workspace_name},
            ),
            # for workspace summary page
            "activity": activity,
            "project": project,
            # for code button
            "code_url": code_url,
            "return_url": "",
            "is_output_checker": request.user.output_checker,
            # "is_author": False
        },
    )


@instrument(func_attributes={"workspace": "workspace_name"})
@xframe_options_sameorigin
@require_http_methods(["GET"])
def workspace_contents(request, workspace_name: str, path: str):
    workspace = get_workspace_or_raise(request.user, workspace_name)

    try:
        abspath = workspace.abspath(path)
    except bll.FileNotFound:
        raise Http404()

    if not abspath.is_file():
        return HttpResponseBadRequest()

    bll.audit_workspace_file_access(workspace, UrlPath(path), request.user)

    plaintext = request.GET.get("plaintext", False)
    renderer = workspace.get_renderer(UrlPath(path), plaintext=plaintext)
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
        # TODO: withdraw action
        else:
            raise Http404(f"Invalid action {action}")
    else:
        display_form_errors(request, multiform.errors)

        # redirect back where we came from
        if "next_url" not in multiform.errors:
            url = multiform.cleaned_data["next_url"]
        else:
            url = workspace.get_url()

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

        state = workspace.get_workspace_file_status(UrlPath(f))
        if state in [
            WorkspaceFileStatus.UNRELEASED,
            WorkspaceFileStatus.CONTENT_UPDATED,
        ]:
            files_to_add.append(f)
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
        template="add_files.html",
        context={
            "form": add_file_form,
            "formset": filetype_formset,
            "files_ignored": files_ignored,
            "no_valid_files": len(files_to_add) == 0,
            "add_file_url": reverse(
                "workspace_add_file",
                kwargs={"workspace_name": workspace.name},
            ),
        },
    )


@instrument(func_attributes={"workspace": "workspace_name"})
@require_http_methods(["POST"])
def workspace_add_file_to_request(request, workspace_name):
    workspace = get_workspace_or_raise(request.user, workspace_name)
    release_request = bll.get_or_create_current_request(workspace_name, request.user)
    form = AddFileForm(request.POST, release_request=release_request)
    formset = FileTypeFormSet(request.POST)

    # default redirect in case of error
    next_url = workspace.get_url()
    errors = False

    if not form.is_valid():
        display_form_errors(request, form.errors)
        errors = True

    if "next_url" not in form.errors:
        next_url = form.cleaned_data["next_url"]

    if not formset.is_valid():
        form_errors = [f.errors for f in formset]
        non_form_errors = formset.non_form_errors()
        if non_form_errors:  # pragma: no cover
            form_errors = [{"": non_form_errors}] + form_errors
        display_form_errors(request, *form_errors)
        errors = True

    if errors:
        # redirect back where we came from with errors
        return redirect(next_url)

    # check the files all exist
    try:
        for formset_form in formset:
            relpath = formset_form.cleaned_data["file"]
            workspace.abspath(relpath)
    except bll.FileNotFound:
        raise Http404(f"file {relpath} does not exist")

    group_name = (
        form.cleaned_data.get("new_filegroup")
        or form.cleaned_data.get("filegroup")
        or ""
    )
    msgs = []
    success = False
    for formset_form in formset:
        relpath = formset_form.cleaned_data["file"]
        filetype = RequestFileType[formset_form.cleaned_data["filetype"]]

        status = workspace.get_workspace_file_status(UrlPath(relpath))
        if status == WorkspaceFileStatus.CONTENT_UPDATED:
            try:
                bll.update_file_in_request(
                    release_request, relpath, request.user, group_name, filetype
                )
            except bll.APIException as err:  # pragma: no cover
                # it's pretty difficult to hit this error
                msgs.append(f"{relpath}: {err}")
            else:
                success = True
                msgs.append(
                    f"{relpath}: {filetype.name.title()} file has been updated in request (file group '{group_name}')",
                )
        else:
            try:
                bll.add_file_to_request(
                    release_request, relpath, request.user, group_name, filetype
                )
            except bll.APIException as err:
                # This exception is raised if the file has already been added
                # (to any group on the request)
                msgs.append(f"{relpath}: {err}")
            else:
                success = True
                msgs.append(
                    f"{relpath}: {filetype.name.title()} file has been added to request (file group '{group_name}')",
                )

    # if any succeeded, show as success
    level = "success" if success else "error"
    display_multiple_messages(request, msgs, level)

    # redirect back where we came from
    return redirect(next_url)

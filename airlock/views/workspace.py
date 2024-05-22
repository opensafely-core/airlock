from collections import defaultdict

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers
from opentelemetry import trace

from airlock.business_logic import RequestFileType, bll
from airlock.file_browser_api import get_workspace_tree
from airlock.forms import AddFileForm, FileTypeFormSet, MultiselectForm
from airlock.types import UrlPath
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

    for project, workspaces in sorted(workspaces_by_project.items()):
        yield project, list(sorted(workspaces))


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

    # Only show the add form button this pathitem is a file that can be added
    # to a request - i.e. it is a file and it's not already on the curent
    # request for the user, and the user is allowed to add it to a request (if
    # they are an output-checker they are allowed to view all workspaces, but
    # not necessarily create requests for them.)
    # Currently we can just rely on checking the relpath against
    # the files on the request. In future we'll likely also need to
    # check file metadata to allow updating a file if the original has
    # changed.
    file_in_request = (
        workspace.current_request
        and path_item.relpath in workspace.current_request.all_files_set()
    )
    add_file = (
        path_item.is_valid()
        and request.user.can_create_request(workspace_name)
        and (workspace.current_request is None or not file_in_request)
    )

    activity = []
    project = request.user.workspaces.get(workspace_name, {}).get(
        "project", "Unknown project"
    )

    # we are viewing the root, so load workspace audit log
    if path == "":
        activity = bll.get_audit_log(
            workspace=workspace.name, exclude_readonly=True, size=20
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
            "title": f"Files for workspace {workspace_name}",
            "request_file_url": reverse(
                "workspace_add_file",
                kwargs={"workspace_name": workspace_name},
            ),
            "current_request": workspace.current_request,
            "file_in_request": file_in_request,
            # for add file buttons
            "add_file": add_file,
            "multiselect_url": reverse(
                "workspace_multiselect",
                kwargs={"workspace_name": workspace_name},
            ),
            # for workspace summary page
            "activity": activity,
            "project": project,
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

    renderer = workspace.get_renderer(UrlPath(path))
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
            add_file_form = AddFileForm(
                release_request=bll.get_current_request(workspace_name, request.user),
                initial={"next_url": multiform.cleaned_data["next_url"]},
            )

            filetype_formset = FileTypeFormSet(
                initial=[{"file": f} for f in multiform.cleaned_data["selected"]],
            )
            return TemplateResponse(
                request,
                template="add_files.html",
                context={
                    "form": add_file_form,
                    "formset": filetype_formset,
                    "add_file_url": reverse(
                        "workspace_add_file",
                        kwargs={"workspace_name": workspace_name},
                    ),
                },
            )
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

        return redirect(url)


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

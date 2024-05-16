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
from airlock.forms import AddFilesForm, MultiselectForm
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

    current_request = bll.get_current_request(workspace_name, request.user)

    # Only include the AddFileForm if this pathitem is a file that
    # can be added to a request - i.e. it is a file and it's not
    # already on the curent request for the user, and the user
    # is allowed to add it to a request (if they are an output-checker they
    # are allowed to view all workspaces, but not necessarily create
    # requests for them.)
    # Currently we can just rely on checking the relpath against
    # the files on the request. In future we'll likely also need to
    # check file metadata to allow updating a file if the original has
    # changed.
    file_in_request = (
        current_request and path_item.relpath in current_request.all_files_set()
    )
    add_file = (
        path_item.is_valid()
        and request.user.can_create_request(workspace_name)
        and (current_request is None or not file_in_request)
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
            "current_request": current_request,
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
            add_files_form = AddFilesForm(
                release_request=bll.get_current_request(workspace_name, request.user),
                files=multiform.cleaned_data["selected"],
            )
            # this was the only way I could find to inject a value into
            # initial, which is used in the form rendering
            add_files_form.fields["next_url"].initial = multiform.cleaned_data[
                "next_url"
            ]

            return TemplateResponse(
                request,
                template="add_files.html",
                context={
                    "form": add_files_form,
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
        display_form_errors(request, multiform)
        return redirect(multiform.cleaned_data.get("next_url", workspace.get_url()))


@instrument(func_attributes={"workspace": "workspace_name"})
@require_http_methods(["POST"])
def workspace_add_file_to_request(request, workspace_name):
    workspace = get_workspace_or_raise(request.user, workspace_name)

    # pull out list of files from raw POST data to be able to create the form with
    files = [v for k, v in request.POST.items() if k.startswith("file_")]
    # get raw source of POST, to use in case form doesn't validate
    next_url = request.POST["next_url"]

    # check the files all exist
    try:
        for relpath in files:
            workspace.abspath(relpath)
    except bll.FileNotFound:
        raise Http404(f"file {relpath} does not exist")

    release_request = bll.get_or_create_current_request(workspace_name, request.user)
    form = AddFilesForm(request.POST, release_request=release_request, files=files)

    if form.is_valid():
        group_name = (
            form.cleaned_data.get("new_filegroup")
            or form.cleaned_data.get("filegroup")
            or ""
        )
        msgs = []
        success = False
        for i, relpath in enumerate(files):
            filetype = RequestFileType[form.cleaned_data[f"filetype_{i}"]]
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

    else:
        display_form_errors(request, form)

    # redirect back where we came from
    return redirect(next_url)

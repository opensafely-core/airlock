from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers

from airlock.business_logic import UrlPath, bll
from airlock.file_browser_api import get_workspace_tree
from airlock.forms import AddFileForm

from .helpers import serve_file, validate_workspace


def workspace_index(request):
    workspaces = bll.get_workspaces_for_user(request.user)
    return TemplateResponse(request, "workspaces.html", {"workspaces": workspaces})


# we return different content if it is a HTMX request.
@vary_on_headers("HX-Request")
def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = validate_workspace(request.user, workspace_name)

    template = "file_browser/index.html"
    selected_only = False

    if request.htmx:
        template = "file_browser/contents.html"
        selected_only = True

    tree = get_workspace_tree(workspace, path, selected_only)

    try:
        path_item = tree.get_path(path)
    except tree.PathNotFound:
        raise Http404()

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
    form = None
    file_in_request = (
        current_request and path_item.relpath in current_request.file_set()
    )
    if request.user.can_create_request(workspace_name) and (
        current_request is None or not file_in_request
    ):
        form = AddFileForm(release_request=current_request)

    return TemplateResponse(
        request,
        template,
        {
            "workspace": workspace,
            "root": tree,
            "path_item": path_item,
            "context": "workspace",
            "title": f"Files for workspace {workspace_name}",
            "request_file_url": reverse(
                "workspace_add_file",
                kwargs={"workspace_name": workspace_name},
            ),
            "current_request": current_request,
            "file_in_request": file_in_request,
            "form": form,
        },
    )


@require_http_methods(["GET"])
def workspace_contents(request, workspace_name: str, path: str):
    workspace = validate_workspace(request.user, workspace_name)

    try:
        abspath = workspace.abspath(path)
    except bll.FileNotFound:
        raise Http404()

    if not abspath.is_file():
        return HttpResponseBadRequest()

    return serve_file(abspath)


@require_http_methods(["POST"])
def workspace_add_file_to_request(request, workspace_name):
    workspace = validate_workspace(request.user, workspace_name)
    relpath = UrlPath(request.POST["path"])
    try:
        workspace.abspath(relpath)
    except bll.FileNotFound:
        raise Http404()

    release_request = bll.get_current_request(workspace_name, request.user, create=True)
    form = AddFileForm(request.POST, release_request=release_request)
    if form.is_valid():
        group_name = request.POST.get("new_filegroup") or request.POST.get("filegroup")
        try:
            bll.add_file_to_request(release_request, relpath, request.user, group_name)
        except bll.APIException as err:
            # This exception is raised if the file has already been added
            # (to any group on the request)
            messages.error(request, str(err))
        else:
            messages.success(
                request, f"File has been added to request (file group '{group_name}')"
            )
    else:
        for error in form.errors.values():
            messages.error(request, error)

    # Redirect to the file in the workspace
    return redirect(workspace.get_url(relpath))

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from airlock.api import UrlPath
from airlock.file_browser_api import get_workspace_tree
from airlock.forms import AddFileForm
from local_db.api import LocalDBProvider

from .helpers import serve_file, validate_workspace


api = LocalDBProvider()


def workspace_index(request):
    workspaces = api.get_workspaces_for_user(request.user)
    return TemplateResponse(request, "workspaces.html", {"workspaces": workspaces})


def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = validate_workspace(request.user, workspace_name)

    tree = get_workspace_tree(workspace, path)

    try:
        path_item = tree.get_path(path)
    except tree.PathNotFound:
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    current_request = api.get_current_request(workspace_name, request.user)

    # Only include the AddFileForm if this pathitem is a file that
    # can be added to a request - i.e. it is a file and it's not
    # already on the curent request for the user
    # Currently we can just rely on checking the relpath against
    # the files on the request. In future we'll likely also need to
    # check file metadata to allow updating a file if the original has
    # changed.
    form = None
    if current_request is None or path_item.relpath not in current_request.file_set():
        form = AddFileForm(release_request=current_request)

    return TemplateResponse(
        request,
        "file_browser/index.html",
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
            "form": form,
        },
    )


@require_http_methods(["GET"])
def workspace_contents(request, workspace_name: str, path: str):
    workspace = validate_workspace(request.user, workspace_name)

    try:
        abspath = workspace.abspath(path)
    except api.FileNotFound:
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
    except api.FileNotFound:
        raise Http404()

    release_request = api.get_current_request(workspace_name, request.user, create=True)
    form = AddFileForm(request.POST, release_request=release_request)
    if form.is_valid():
        group_name = request.POST.get("new_filegroup") or request.POST.get("filegroup")
        try:
            api.add_file_to_request(release_request, relpath, request.user, group_name)
        except api.APIException as err:
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

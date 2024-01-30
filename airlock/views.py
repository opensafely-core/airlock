from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from airlock.users import User
from airlock.workspace_api import OutputRequest, Workspace, WorkspacesRoot


def index(request):
    return TemplateResponse(request, "index.html")


def validate_workspace(user, workspace_name):
    workspace = Workspace(workspace_name)
    if not workspace.exists():
        raise Http404()
    if user is None or not user.has_permission(workspace_name):
        raise PermissionDenied()

    return workspace


def validate_output_request(user, workspace, request_id):
    output_request = OutputRequest(workspace, request_id)
    # TODO output request authorization?
    if not output_request.exists():
        raise Http404()

    return output_request


def workspace_index_view(request):
    user = User.from_session(request.session)
    return TemplateResponse(
        request,
        "file_browser/index.html",
        {
            "container": WorkspacesRoot(user=user),
        },
    )


def workspace_view(request, workspace_name: str, path: str = ""):
    user = User.from_session(request.session)
    workspace = validate_workspace(user, workspace_name)
    path_item = workspace.get_path(path)

    if not path_item.exists():
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    return TemplateResponse(
        request,
        "file_browser/index.html",
        {
            "workspace": workspace,
            "path_item": path_item,
            "context": "workspace",
            "title": f"{workspace_name} workspace files",
        },
    )


def request_view(request, workspace_name: str, request_id: str, path: str = ""):
    user = User.from_session(request.session)
    workspace = validate_workspace(user, workspace_name)
    output_request = validate_output_request(user, workspace, request_id)

    path_item = output_request.get_path(path)

    if not path_item.exists():
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    context = {
        "workspace": workspace,
        "output_request": output_request,
        "path_item": path_item,
        "context": "request",
        "title": f"{request_id} request files",
        # TODO file these in from user/models
        "is_author": request.GET.get("is_author", False),
        "is_output_checker": user.is_output_checker,
    }

    return TemplateResponse(request, "file_browser/index.html", context)

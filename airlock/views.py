from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from airlock.workspace_api import Workspace


def index(request):
    return TemplateResponse(request, "index.html")


def workspace_view(request, workspace_name: str, path: str = ""):
    workspace = Workspace(workspace_name)
    # TODO workspace authorization
    if not workspace.exists():
        raise Http404()

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
            "container": workspace,
            "path_item": path_item,
        },
    )

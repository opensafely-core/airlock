from django.http import Http404
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from airlock.workspace_api import PathItem


def index(request):
    return TemplateResponse(request, "index.html")


def file_browser(request, path: str = ""):
    path_item = PathItem.from_relative_path(path)

    if not path_item.exists():
        raise Http404()

    is_directory_url = path.endswith("/") or path == ""
    if path_item.is_directory() != is_directory_url:
        return redirect(path_item.url())

    return TemplateResponse(
        request, "file_browser/index.html", {"path_item": path_item}
    )

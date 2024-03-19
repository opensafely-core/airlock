import csv
import functools
from email.utils import formatdate, parsedate
from pathlib import Path

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseNotModified
from django.template import loader
from django.template.response import SimpleTemplateResponse

from airlock.business_logic import UrlPath, bll


class ServeFileException(Exception):
    pass


def login_exempt(view):
    view.login_exempt = True
    return view


def get_workspace_or_raise(user, workspace_name):
    """Get the workspace, converting any errors to http codes."""
    try:
        workspace = bll.get_workspace(workspace_name, user)
    except bll.WorkspaceNotFound:
        raise Http404()
    except bll.WorkspacePermissionDenied:
        raise PermissionDenied()

    return workspace


def get_release_request_or_raise(user, request_id):
    """Get the release request, converting any errors to http codes."""
    try:
        release_request = bll.get_release_request(request_id, user)
    except bll.ReleaseRequestNotFound:
        raise Http404()
    except bll.WorkspacePermissionDenied:
        raise PermissionDenied()

    return release_request


def download_file(abspath, filename=None):
    """Simple Helper to download file."""
    return FileResponse(abspath.open("rb"), as_attachment=True, filename=filename)


def render_with_template(template):
    """Micro framework for rendering content.

    The main purpose is to be able to check to see if the template has changed
    ahead of calling the render function and doing the work.

    It loads the template path used, and stores it on the wrapper function
    object for later inspection.
    """
    django_template = loader.get_template(template)
    template_path = Path(django_template.template.origin.name)

    def decorator(func):
        @functools.wraps(func)
        def wrapper(abspath, suffix):
            context = func(abspath, suffix)
            return SimpleTemplateResponse(template, context)

        wrapper.template_path = template_path

        return wrapper

    return decorator


@render_with_template("file_browser/csv.html")
def csv_renderer(abspath, suffix):
    reader = csv.reader(abspath.open())
    headers = next(reader)
    return {"headers": headers, "rows": reader}


@render_with_template("file_browser/text.html")
def text_renderer(abspath, suffix):
    return {
        "text": abspath.read_text(),
        "class": suffix.lstrip("."),
    }


FILE_RENDERERS = {
    ".csv": csv_renderer,
    ".log": text_renderer,
    ".txt": text_renderer,
    ".json": text_renderer,
}


def build_etag(content_stat, template=None):
    # Like whitenoise, use filesystem metadata rather than hash as its faster
    etag = f"{int(content_stat.st_mtime):x}-{content_stat.st_size:x}"
    if template:
        # add the renderer's template etag so cache is invalidated if we change it
        template_stat = Path(template).stat()
        etag = f"{etag}-{int(template_stat.st_mtime):x}-{template_stat.st_size:x}"

    # quote as per spec
    return f'"{etag}"'


def serve_file(request, abspath, filename=None):
    """Serve file contents in a form the browser can render.

    For html and images, just serve directly.

    For csv and text, render that to html then serve.
    """
    if filename:
        suffix = UrlPath(filename).suffix
    else:
        suffix = abspath.suffix

    if not suffix:
        raise ServeFileException(
            f"Cannot serve file {abspath}, filename {filename}, as there is no suffix on either"
        )

    renderer = FILE_RENDERERS.get(suffix)
    stat = abspath.stat()
    last_modified = formatdate(stat.st_mtime, usegmt=True)
    etag = build_etag(stat, getattr(renderer, "template_path", None))
    last_requested = request.headers.get("If-Modified-Since")

    if request.headers.get("If-None-Match") == etag:
        response = HttpResponseNotModified()
    elif last_requested and parsedate(last_requested) >= parsedate(last_modified):
        response = HttpResponseNotModified()
    elif renderer:
        response = renderer(abspath, suffix)
    else:
        response = FileResponse(abspath.open("rb"), filename=filename)

    response.headers["Last-Modified"] = last_modified
    response.headers["ETag"] = etag

    return response


def get_path_item_from_tree_or_404(tree, path):
    try:
        return tree.get_path(path)
    except tree.PathNotFound:
        raise Http404()

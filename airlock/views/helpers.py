import csv
from email.utils import formatdate

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponse
from django.template.response import TemplateResponse

from airlock.business_logic import UrlPath, bll


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


def render_csv(request, abspath):
    reader = csv.reader(abspath.open())
    headers = next(reader)

    return TemplateResponse(
        request,
        "file_browser/csv.html",
        {
            "headers": headers,
            "rows": list(reader),
        },
    )


def render_text(request, abspath):
    # TODO: synatax highlighting?
    content = f"<pre>{abspath.read_text()}</pre>"
    return HttpResponse(
        content,
        content_type="text/html",
        headers={"Content-Length": len(content)},
    )


FILE_RENDERERS = {
    ".csv": render_csv,
    ".log": render_text,
    ".txt": render_text,
    ".json": render_text,
}


def download_file(abspath, filename=None):
    return FileResponse(abspath.open("rb"), as_attachment=True, filename=filename)


def serve_file(request, abspath, filename=None):
    """Serve file contents in a form the browser can render.

    For html and images, just serve directly.

    For csv and text, render that to html then serve.
    """
    # use same ETag format as whitenoise
    stat = abspath.stat()
    headers = {
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "ETag": f'"{int(stat.st_mtime):x}-{stat.st_size:x}"',
    }

    if filename:
        suffix = UrlPath(filename).suffix
    else:
        suffix = abspath.suffix

    if not suffix:
        raise Exception(
            f"Cannot serve file {abspath}, filename {filename}, as there is no suffix on either"
        )

    renderer = FILE_RENDERERS.get(suffix)
    if renderer:
        response = renderer(request, abspath)
    else:
        response = FileResponse(abspath.open("rb"), filename=filename)

    for k, v in headers.items():
        response.headers[k] = v

    return response

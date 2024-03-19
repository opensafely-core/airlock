from email.utils import parsedate

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404, HttpResponseNotModified

from airlock.business_logic import bll
from airlock.renderers import get_renderer


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


def serve_file(request, abspath, request_file=None):
    """Serve file contents in a form the browser can render.

    For html and images, just serve directly.

    For csv and text, render that to html then serve.
    """

    renderer = get_renderer(abspath, request_file)

    last_requested = request.headers.get("If-Modified-Since")

    if request.headers.get("If-None-Match") == renderer.etag:
        response = HttpResponseNotModified(headers=renderer.headers())
    elif last_requested and parsedate(last_requested) >= parsedate(
        renderer.last_modified
    ):
        response = HttpResponseNotModified(headers=renderer.headers())
    else:
        response = renderer.get_response()

    return response


def get_path_item_from_tree_or_404(tree, path):
    try:
        return tree.get_path(path)
    except tree.PathNotFound:
        raise Http404()

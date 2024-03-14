from email.utils import formatdate

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404

from airlock.business_logic import bll


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


def serve_file(abspath, download=False, filename=None):
    stat = abspath.stat()
    # use same ETag format as whitenoise
    headers = {
        "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        "ETag": f'"{int(stat.st_mtime):x}-{stat.st_size:x}"',
    }
    return FileResponse(
        abspath.open("rb"),
        headers=headers,
        as_attachment=download,
        filename=filename,
    )

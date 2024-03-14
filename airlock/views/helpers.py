from email.utils import formatdate

from django.core.exceptions import PermissionDenied
from django.http import FileResponse, Http404

from airlock.business_logic import bll


def login_exempt(view):
    view.login_exempt = True
    return view


def validate_workspace(user, workspace_name):
    """Ensure the workspace exists and the current user has permissions to access it."""
    try:
        workspace = bll.get_workspace(workspace_name, user)
    except bll.WorkspaceNotFound:
        raise Http404()
    except bll.WorkspacePermissionDenied:
        raise PermissionDenied()

    return workspace


def validate_release_request(user, request_id):
    """Ensure the release request exists for this workspace."""
    try:
        release_request = bll.get_release_request(request_id)
    except bll.ReleaseRequestNotFound:
        raise Http404()

    # check user permissions for this workspace
    validate_workspace(user, release_request.workspace)

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

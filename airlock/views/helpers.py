from django.core.exceptions import PermissionDenied
from django.http import Http404

from local_db.api import LocalDBProvider


api = LocalDBProvider()


def login_exempt(view):
    view.login_exempt = True
    return view


def validate_workspace(user, workspace_name):
    """Ensure the workspace exists and the current user has permissions to access it."""
    try:
        workspace = api.get_workspace(workspace_name)
    except api.WorkspaceNotFound:
        raise Http404()

    if user is None or not user.has_permission(workspace_name):
        raise PermissionDenied()

    return workspace


def validate_release_request(user, request_id):
    """Ensure the release request exists for this workspace."""
    try:
        release_request = api.get_release_request(request_id)
    except api.ReleaseRequestNotFound:
        raise Http404()

    # check user permissions for this workspace
    validate_workspace(user, release_request.workspace)

    return release_request

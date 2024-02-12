from django.conf import settings

from airlock.users import User
from airlock.workspace_api import ReleaseRequest, Workspace, generate_request_id


default_user = User(1, "testuser")


def ensure_workspace(workspace_or_name):
    if isinstance(workspace_or_name, str):
        return create_workspace(workspace_or_name)
    elif isinstance(workspace_or_name, Workspace):
        return workspace_or_name

    raise Exception(f"Invalid workspace: {workspace_or_name})")  # pragma: nocover


def create_workspace(name):
    workspace_dir = settings.WORKSPACE_DIR / name
    workspace_dir.mkdir(exist_ok=True, parents=True)
    return Workspace(name)


def create_release_request(workspace, user=default_user, request_id=None):
    workspace = ensure_workspace(workspace)
    if request_id is None:
        request_id = generate_request_id(workspace.name, user)
    release_request = ReleaseRequest(workspace, request_id)
    release_request.root().mkdir(parents=True, exist_ok=True)
    return release_request


def write_workspace_file(workspace, path, contents=""):
    workspace = ensure_workspace(workspace)
    path = workspace.root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)


def write_request_file(request, path, contents=""):
    path = request.root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)

from django.conf import settings

from airlock.api import Workspace
from airlock.users import User
from local_db.api import LocalDBProvider


default_user = User(1, "testuser")

api = LocalDBProvider()


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


def create_release_request(workspace, user=default_user, **kwargs):
    workspace = ensure_workspace(workspace)
    release_request = api._create_release_request(
        workspace=workspace.name, author=user.username, **kwargs
    )
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


def create_filegroup(release_request, group_name, filepaths=None):
    for filepath in filepaths or []:
        api.add_file_to_request(
            release_request, filepath, User(1, release_request.author), group_name
        )
    return api._get_or_create_filegroupmetadata(release_request.id, group_name)


def refresh_release_request(release_request):
    return api.get_release_request(release_request.id)

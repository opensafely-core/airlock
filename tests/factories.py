from django.conf import settings

from airlock.api import Workspace
from airlock.users import User
from local_db.api import LocalDBProvider


default_user = User(1, "testuser")

api = LocalDBProvider()


def get_user(*, username="testuser", workspaces=[], output_checker=False):
    return User(1, username, workspaces, output_checker)


def ensure_workspace(workspace_or_name):
    if isinstance(workspace_or_name, str):
        return create_workspace(workspace_or_name)
    elif isinstance(workspace_or_name, Workspace):
        return workspace_or_name

    raise Exception(f"Invalid workspace: {workspace_or_name})")  # pragma: nocover


def create_workspace(name):
    workspace_dir = settings.WORKSPACE_DIR / name
    workspace_dir.mkdir(exist_ok=True, parents=True)
    return api.get_workspace(name)


def write_workspace_file(workspace, path, contents=""):
    workspace = ensure_workspace(workspace)
    path = workspace.root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)


def create_release_request(workspace, user=None, **kwargs):
    workspace = ensure_workspace(workspace)

    # create a default user with permission on workspace
    if user is None:
        user = get_user(workspaces=[workspace.name])

    release_request = api._create_release_request(
        workspace=workspace.name, author=user.username, **kwargs
    )
    release_request.root().mkdir(parents=True, exist_ok=True)
    return release_request


def write_request_file(request, group, path, contents="", user=None):
    workspace = ensure_workspace(request.workspace)
    try:
        workspace.abspath(path)
    except api.FileNotFound:
        write_workspace_file(workspace, path, contents)

    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = get_user(username=request.author, workspaces=[request.workspace])

    api.add_file_to_request(request, relpath=path, user=user, group_name=group)


def create_filegroup(release_request, group_name, filepaths=None):
    for filepath in filepaths or []:
        api.add_file_to_request(
            release_request, filepath, User(1, release_request.author), group_name
        )
    return api._get_or_create_filegroupmetadata(release_request.id, group_name)


def refresh_release_request(release_request):
    return api.get_release_request(release_request.id)

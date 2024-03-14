from django.conf import settings

from airlock.business_logic import Workspace, bll
from airlock.users import User


def create_user(username="testuser", workspaces=None, output_checker=False):
    """Factory to create a user.

    For ease of use, workspaces can either be a list of workspaces, which is
    converted into an appropriate dict. Or it can be an explicit dict.
    """
    if workspaces is None:
        workspaces_dict = {}
    elif isinstance(workspaces, dict):
        workspaces_dict = workspaces
    else:
        workspaces_dict = {
            workspace: {"project": "project"} for workspace in workspaces
        }
    return User(username, workspaces_dict, output_checker)


def ensure_workspace(workspace_or_name):
    if isinstance(workspace_or_name, str):
        return create_workspace(workspace_or_name)
    elif isinstance(workspace_or_name, Workspace):
        return workspace_or_name

    raise Exception(f"Invalid workspace: {workspace_or_name})")  # pragma: nocover


def create_workspace(name):
    workspace_dir = settings.WORKSPACE_DIR / name
    workspace_dir.mkdir(exist_ok=True, parents=True)
    return bll.get_workspace(name)


def write_workspace_file(workspace, path, contents=""):
    workspace = ensure_workspace(workspace)
    path = workspace.root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)


def create_release_request(workspace, user=None, **kwargs):
    workspace = ensure_workspace(workspace)

    # create a default user with permission on workspace
    if user is None:
        user = create_user("author", workspaces=[workspace.name])

    release_request = bll._create_release_request(
        workspace=workspace.name, author=user.username, **kwargs
    )
    release_request.root().mkdir(parents=True, exist_ok=True)
    return release_request


def write_request_file(request, group, path, contents="", user=None):
    workspace = ensure_workspace(request.workspace)
    try:
        workspace.abspath(path)
    except bll.FileNotFound:
        write_workspace_file(workspace, path, contents)

    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user(request.author, workspaces=[workspace.name])

    bll.add_file_to_request(request, relpath=path, user=user, group_name=group)


def create_filegroup(release_request, group_name, filepaths=None):
    user = create_user(release_request.author, [release_request.workspace])
    for filepath in filepaths or []:  # pragma: nocover
        bll.add_file_to_request(release_request, filepath, user, group_name)
    return bll._dal._get_or_create_filegroupmetadata(release_request.id, group_name)


def refresh_release_request(release_request):
    return bll.get_release_request(release_request.id)

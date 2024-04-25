import json
import subprocess
import tempfile
import time
from pathlib import Path

from django.conf import settings

from airlock.business_logic import (
    AuditEvent,
    AuditEventType,
    CodeRepo,
    RequestFileType,
    UrlPath,
    Workspace,
    bll,
)
from airlock.lib.git import ensure_git_init
from airlock.users import User


def create_user(
    username="testuser", workspaces=None, output_checker=False, last_refresh=None
):
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

    if last_refresh is None:
        last_refresh = time.time()
    return User(username, workspaces_dict, output_checker, last_refresh)


def ensure_workspace(workspace_or_name):
    if isinstance(workspace_or_name, str):
        return create_workspace(workspace_or_name)
    elif isinstance(workspace_or_name, Workspace):
        return workspace_or_name

    raise Exception(f"Invalid workspace: {workspace_or_name})")  # pragma: nocover


def create_workspace(name, user=None):
    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user("author", workspaces=[name])

    workspace_dir = settings.WORKSPACE_DIR / name
    workspace_dir.mkdir(exist_ok=True, parents=True)
    return bll.get_workspace(name, user)


def write_workspace_file(workspace, path, contents=""):
    workspace = ensure_workspace(workspace)
    path = workspace.root() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents)


def create_repo(workspace, files=None):
    workspace = ensure_workspace(workspace)
    repo_dir = settings.GIT_REPO_DIR / workspace.name

    if files is None:
        files = [
            ("project.yaml", "yaml: true"),
        ]

    env = {"GIT_DIR": str(repo_dir)}
    ensure_git_init(repo_dir)
    subprocess.run(["git", "config", "user.email", "test@example.com"], env=env)
    subprocess.run(["git", "config", "user.name", "Test"], check=True, env=env)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        for name, content in files:
            p = tmpdir / name
            p.parent.mkdir(exist_ok=True, parents=True)
            if isinstance(content, bytes):  # pragma: nocover
                p.write_bytes(content)
            else:
                p.write_text(content)

        env["GIT_WORK_TREE"] = tmpdir
        subprocess.run(["git", "add", "."], check=True, env=env)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "initial"], check=True, env=env
        )

    response = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )

    commit = response.stdout.strip()

    manifest = {"repo": str(repo_dir)}
    write_workspace_file(workspace, "metadata/manifest.json", json.dumps(manifest))

    return CodeRepo.from_workspace(workspace, commit)


def create_release_request(workspace, user=None, **kwargs):
    workspace = ensure_workspace(workspace)

    # create a default user with permission on workspace
    if user is None:
        user = create_user("author", workspaces=[workspace.name])

    release_request = bll._create_release_request(
        workspace=workspace.name, author=user, **kwargs
    )
    release_request.root().mkdir(parents=True, exist_ok=True)
    return release_request


def write_request_file(
    request,
    group,
    path,
    contents="",
    user=None,
    filetype=RequestFileType.OUTPUT,
    approved=False,
):
    workspace = ensure_workspace(request.workspace)
    try:
        workspace.abspath(path)
    except bll.FileNotFound:
        write_workspace_file(workspace, path, contents)

    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user(request.author, workspaces=[workspace.name])

    bll.add_file_to_request(
        request, relpath=path, user=user, group_name=group, filetype=filetype
    )
    if approved:
        for i in range(2):
            bll._dal.approve_file(
                request,
                relpath=UrlPath(path),
                username=f"output-checker-{i}",
                audit=create_audit_event(AuditEventType.REQUEST_FILE_APPROVE),
            )


def create_filegroup(release_request, group_name, filepaths=None):
    user = create_user(release_request.author, [release_request.workspace])
    for filepath in filepaths or []:  # pragma: nocover
        bll.add_file_to_request(release_request, filepath, user, group_name)
    return bll._dal._get_or_create_filegroupmetadata(release_request.id, group_name)


def refresh_release_request(release_request, user=None):
    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user("author", workspaces=[release_request.workspace])
    return bll.get_release_request(release_request.id, user)


def create_audit_event(
    type_,
    user="user",
    workspace="workspace",
    request="request",
    path=UrlPath("foo/bar"),
    extra={"foo": "bar"},
):
    event = AuditEvent(
        type=type_,
        user=user,
        workspace=workspace,
        request=request,
        path=UrlPath(path) if path else None,
        extra=extra,
    )
    bll._dal.audit_event(event)
    return event

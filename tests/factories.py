import csv
import json
import subprocess
import tempfile
import time
from hashlib import file_digest
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


# get_output_metadata is imported from job-runner
def get_output_metadata(
    abspath, level, job_id, job_request, action, commit, repo, excluded, message=None
):
    stat = abspath.stat()
    with abspath.open("rb") as fp:
        content_hash = file_digest(fp, "sha256").hexdigest()

    rows = cols = None
    if abspath.suffix == ".csv":
        with abspath.open() as fp:
            reader = csv.DictReader(fp)
            first_row = next(reader, None)
            if first_row:
                cols = len(first_row)
                rows = sum(1 for _ in reader) + 1
            else:  # pragma: no cover
                cols = rows = 0

    return {
        "level": level,
        "job_id": job_id,
        "job_request": job_request,
        "action": action,
        "repo": repo,
        "commit": commit,
        "size": stat.st_size,
        "timestamp": stat.st_mtime,
        "content_hash": content_hash,
        "excluded": excluded,
        "message": message,
        "row_count": rows,
        "col_count": cols,
    }


def update_manifest(workspace: Workspace | str, files=None):
    """Write a manifest based on the files currently in the directory.

    Make up action, job ids and commits.
    """
    update_object = False
    if isinstance(workspace, str):
        name = workspace
        root = settings.WORKSPACE_DIR / workspace
    else:
        update_object = True
        name = workspace.name
        root = workspace.root()

    manifest_path = root / "metadata/manifest.json"

    skip_paths = [root / "logs", root / "metadata"]

    repo = "http://example.com/org/repo"

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest["workspace"] = name
        repo = manifest["repo"] or repo
        manifest.setdefault("outputs", {})
    else:
        manifest = {"workspace": name, "repo": repo, "outputs": {}}

    manifest["repo"] = repo

    if files is None:  # pragma: nocover
        files = [
            f.relative_to(root)
            for f in root.glob("**/*")
            if f.is_file() and not any(1 for path in skip_paths if path in f.parents)
        ]

    for i, f in enumerate(files):
        manifest["outputs"][str(f)] = get_output_metadata(
            root / f,
            level="moderately_senstive",
            job_id=f"job_{i}",
            job_request=f"job_request_{i}",
            action=f"action_{i}",
            commit="abcdefgh" * 5,  # 40 characters,
            repo=repo,
            excluded=False,
        )

    manifest_path.parent.mkdir(exist_ok=True, parents=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    if update_object:
        workspace.manifest = manifest


def create_workspace(name, user=None):
    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user("author", workspaces=[name])

    workspace_dir = settings.WORKSPACE_DIR / name
    workspace_dir.mkdir(exist_ok=True, parents=True)
    update_manifest(name)
    return bll.get_workspace(name, user)


def write_workspace_file(workspace, path, contents="", manifest=True):
    workspace = ensure_workspace(workspace)
    abspath = workspace.root() / path
    abspath.parent.mkdir(parents=True, exist_ok=True)
    abspath.write_text(contents)
    if manifest:  # pragma: nocover
        update_manifest(workspace, [path])


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
    update_manifest(workspace)
    workspace.manifest["repo"] = str(repo_dir)
    write_workspace_file(
        workspace, "metadata/manifest.json", json.dumps(workspace.manifest)
    )

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

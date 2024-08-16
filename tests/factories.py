import csv
import json
import subprocess
import tempfile
import time
import typing
from dataclasses import dataclass, field
from hashlib import file_digest
from pathlib import Path

from django.conf import settings

from airlock import exceptions
from airlock.business_logic import (
    AuditEvent,
    CodeRepo,
    ReleaseRequest,
    RequestFile,
    Workspace,
    bll,
)
from airlock.enums import (
    RequestFileType,
    RequestFileVote,
    RequestStatus,
)
from airlock.lib.git import ensure_git_init
from airlock.types import UrlPath
from airlock.users import User


def create_user(
    username: str = "testuser",
    workspaces: list[str] | None = None,
    output_checker: bool = False,
    last_refresh=None,
) -> User:
    """Factory to create a user.

    For ease of use, workspaces is just a flat list of workspace name, which is
    converted into an appropriate dict with all the right keys.

    If you need to create a more complex workspace dict, you can call
    create_user_from_dict directly.
    """
    if workspaces is None:
        # default to usual workspace
        workspaces = ["workspace"]

    workspaces_dict = {
        workspace: {
            "project_details": {"name": "project", "ongoing": True},
            "archived": False,
        }
        for workspace in workspaces
    }

    return create_user_from_dict(
        username, workspaces_dict, output_checker, last_refresh
    )


def create_user_from_dict(
    username, workspaces_dict, output_checker=False, last_refresh=None
) -> User:
    if last_refresh is None:
        last_refresh = time.time()

    return User(username, workspaces_dict, output_checker, last_refresh)


def ensure_workspace(workspace_or_name) -> Workspace:
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
    if isinstance(workspace, Workspace):
        name = workspace.name
        root = workspace.root()
    else:
        name = workspace
        root = settings.WORKSPACE_DIR / workspace

    manifest_path = root / "metadata/manifest.json"

    skip_paths = [root / "metadata"]

    repo = "http://example.com/org/repo"
    commit = "abcdefgh" * 5  # 40 characters

    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        manifest["workspace"] = name
        if manifest["outputs"]:
            first_output = list(manifest["outputs"].values())[0]
            repo = first_output["repo"]
            if repo.startswith("https://github.com"):  # pragma: no cover
                commit = first_output["commit"]
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
        name = str(f)
        current = manifest.get("outputs", {}).get(name, {})
        manifest["outputs"][name] = get_output_metadata(
            root / f,
            level="moderately_senstive",
            job_id=f"job_{i}",
            job_request=f"job_request_{i}",
            action=f"action_{i}",
            commit=current.get("commit", commit),
            repo=repo,
            excluded=False,
        )

    manifest_path.parent.mkdir(exist_ok=True, parents=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    if isinstance(workspace, Workspace):
        workspace.manifest = manifest


def create_workspace(name, user=None) -> Workspace:
    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user("author", workspaces=[name])

    workspace_dir = settings.WORKSPACE_DIR / name
    if not workspace_dir.exists():
        workspace_dir.mkdir(exist_ok=True, parents=True)

    update_manifest(name)
    return bll.get_workspace(name, user)


def write_workspace_file(workspace, path, contents="", manifest=True):
    workspace = ensure_workspace(workspace)
    abspath = workspace.root() / path
    abspath.parent.mkdir(parents=True, exist_ok=True)
    abspath.write_text(contents)
    if manifest:
        update_manifest(workspace, [path])


def create_repo(workspace, files=None, temporary=True):
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
        if temporary:
            repo_content_dir = Path(tmpdir)
        else:  # pragma: nocover
            repo_content_dir = repo_dir

        for name, content in files:
            p = repo_content_dir / name
            p.parent.mkdir(exist_ok=True, parents=True)
            if not p.exists():
                if isinstance(content, bytes):  # pragma: nocover
                    p.write_bytes(content)
                else:
                    p.write_text(content)

        env["GIT_WORK_TREE"] = str(repo_content_dir)
        response: subprocess.CompletedProcess[typing.Any] = subprocess.run(
            ["git", "add", "."], capture_output=True, check=True, env=env
        )
        if b"nothing to commit" not in response.stdout:  # pragma: nocover
            response = subprocess.run(
                ["git", "commit", "--quiet", "-m", "initial"], check=False, env=env
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
    if not workspace.manifest["outputs"]:
        write_workspace_file(workspace, "foo.txt")

    workspace.manifest["repo"] = None  # match job-runner output
    for output in workspace.manifest["outputs"].values():
        output["repo"] = str(repo_dir)
        output["commit"] = str(commit)
    write_workspace_file(
        workspace,
        "metadata/manifest.json",
        json.dumps(workspace.manifest, indent=2),
        manifest=False,
    )

    return CodeRepo.from_workspace(workspace, commit)


def create_release_request(
    workspace, user=None, status=None, **kwargs
) -> ReleaseRequest:
    if status:
        assert (
            status == RequestStatus.PENDING
        ), "Use create_request_at_status to create a release request with a state other than PENDING"
    workspace = ensure_workspace(workspace)

    # create a default user with permission on workspace
    if user is None:
        user = create_user("author", workspaces=[workspace.name])

    release_request = bll.get_or_create_current_request(
        workspace=workspace.name, user=user, **kwargs
    )
    release_request.root().mkdir(parents=True, exist_ok=True)
    return release_request


def create_request_at_status(
    workspace,
    status,
    author=None,
    files=None,
    checker=None,
    withdrawn_after=None,
    **kwargs,
) -> ReleaseRequest:
    """
    Create a valid request at the given status.

    Files must be provided for any status except PENDING. To move to
    PARTIALLY_REVIEWED and beyond, you will also need to approve or request changes.

    Files are provided using a factory method for creating them. e.g.

    create_request_at_status(
        "workspace",
        RequestStatus.RELEASED,
        files=[
            request_file(approved=True),
            request_file(approved=True, filetype=RequestFileType.SUPPORTING),
        ]
    )

    `checker` is the user who will do any status changes or actions that require an
    output-checker. If not provided, a default output checker is used.

    Optionally, `request_file` can be given `checkers`, a list of users who will
    review (approve/request changes to) the file. If not provided, default output checkers
    will be used.
    """
    author = author or create_user(
        "author",
        workspaces=[workspace if isinstance(workspace, str) else workspace.name],
    )
    if status == RequestStatus.WITHDRAWN:
        assert (
            withdrawn_after is not None
        ), "pass withdrawn_after to decide when to withdraw"
        assert withdrawn_after in [
            RequestStatus.PENDING,
            RequestStatus.RETURNED,
        ], f"Invalid state transition with withdrawn_after {withdrawn_after}"

    # Get a default checker if one was not provided
    # This is the checker who does the state transitions (approved/released/returned/rejected)
    # It is not necessarily the same checker who reviews files.
    # `request_file()` can be called with an optional list of checkers; if it is
    # None, the same default checkers will be used for reviewing files (and the first one will
    # be the one that does the other state transitions.)
    if checker:
        file_reviewers = [checker, get_default_output_checkers()[1]]
    else:
        file_reviewers = get_default_output_checkers()
        checker = file_reviewers[0]

    request = create_release_request(workspace, author, **kwargs)

    # add all files
    if files:
        for testfile in files:
            testfile.add(request)

        request = refresh_release_request(request)

        # if we add files, we should add context & controls
        for filegroup in request.filegroups:
            dummy_context = "This is some testing context"
            dummy_controls = "I got rid of all the small numbers"
            bll.group_edit(request, filegroup, dummy_context, dummy_controls, author)

        request = refresh_release_request(request)

    if status == RequestStatus.PENDING:
        return request

    if status == RequestStatus.WITHDRAWN and withdrawn_after == RequestStatus.PENDING:
        bll.set_status(request, RequestStatus.WITHDRAWN, author)
        return refresh_release_request(request)

    bll.submit_request(request, author)
    request = refresh_release_request(request)

    if files:
        # apply votes to files.
        for testfile in files:
            testfile.vote(request)
        request = refresh_release_request(request)

    if status == RequestStatus.SUBMITTED:
        return request

    # If there are output files, get the usernames of all file reviewers
    # so we can submit reviews with the correct checkers.
    # Note that it is possible to review a request with no output files
    # (potentially before returning it to the reviewer so they can add some).
    # Approving or releasing requests with no output files is not allowed.
    if request.output_files():
        file_reviewers = [
            User(username, output_checker=True)
            for username in list(request.output_files().values())[0].reviews.keys()
        ]

    if status == RequestStatus.PARTIALLY_REVIEWED:
        submit_independent_review(request, file_reviewers[0])
        return refresh_release_request(request)
    # all other statuses require submitted reviews.
    submit_independent_review(request, *file_reviewers)
    request = refresh_release_request(request)

    if status == RequestStatus.REVIEWED:
        return request

    if status in [RequestStatus.RETURNED, RequestStatus.WITHDRAWN]:
        bll.set_status(request, RequestStatus.RETURNED, checker)
        request = refresh_release_request(request)

        if not (
            status == RequestStatus.WITHDRAWN
            and withdrawn_after == RequestStatus.RETURNED
        ):
            return request
        bll.set_status(request, RequestStatus.WITHDRAWN, author)
        return refresh_release_request(request)

    if status == RequestStatus.REJECTED:
        bll.set_status(request, RequestStatus.REJECTED, checker)
        return refresh_release_request(request)

    bll.set_status(request, RequestStatus.APPROVED, checker)
    request = refresh_release_request(request)

    if status == RequestStatus.APPROVED:
        return request

    if status == RequestStatus.RELEASED:
        bll.release_files(
            request,
            user=checker,
            upload=False,
        )
        return refresh_release_request(request)

    raise Exception(f"invalid state: {status}")  # pragma: no cover


def add_request_file(
    request,
    group,
    path,
    contents="",
    user: User | None = None,
    filetype=RequestFileType.OUTPUT,
    workspace=None,
) -> ReleaseRequest:
    request = refresh_release_request(request)
    # if ensure_workspace is passed a string, it will always create a
    # new workspace. Optionally pass a workspace instance, which will
    # ensure that adding a file uses the commit from the workspace's
    # manifest.json
    workspace = ensure_workspace(workspace or request.workspace)
    try:
        workspace.abspath(path)
    except exceptions.FileNotFound:
        write_workspace_file(workspace, path, contents)

    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = create_user(request.author, workspaces=[workspace.name])

    bll.add_file_to_request(
        request, relpath=path, user=user, group_name=group, filetype=filetype
    )

    return refresh_release_request(request)


def create_request_file_bad_path(request_file: RequestFile, bad_path) -> RequestFile:
    bad_request_file_dict = {
        "relpath": bad_path,
        "group": request_file.group,
        "file_id": request_file.file_id,
        "reviews": {},
        "timestamp": request_file.timestamp,
        "size": request_file.size,
        "job_id": request_file.job_id,
        "commit": request_file.commit,
        "repo": request_file.repo,
    }
    bad_request_file = RequestFile.from_dict(bad_request_file_dict)
    return bad_request_file


def get_default_output_checkers():
    return [
        create_user("output-checker-0", output_checker=True),
        create_user("output-checker-1", output_checker=True),
    ]


def review_file(
    request: ReleaseRequest, relpath: UrlPath, status: RequestFileVote, *users
):
    if not users:  # pragma: no cover
        users = get_default_output_checkers()

    request = refresh_release_request(request)

    for user in users:
        if status == RequestFileVote.APPROVED:
            bll.approve_file(
                request,
                request.get_request_file_from_output_path(relpath),
                user=user,
            )
        elif status == RequestFileVote.CHANGES_REQUESTED:
            bll.request_changes_to_file(
                request,
                request.get_request_file_from_output_path(relpath),
                user=user,
            )
        else:
            raise AssertionError(f"unrecognised status; {status}")  # pragma: no cover


@dataclass
class TestRequestFile:
    """Placeholder containing file metadata.

    Allows us to set up file states declaratively. The add() and vote()
    methods can be called with a request in the right state.
    """

    group: str
    path: UrlPath
    user: User | None
    contents: str = ""
    filetype: RequestFileType = RequestFileType.OUTPUT
    workspace: str | None = None

    # voting
    approved: bool = False
    changes_requested: bool = False
    checkers: typing.Sequence[User] = field(default_factory=list)

    def add(self, request):
        request = refresh_release_request(request)
        add_request_file(
            request,
            group=self.group,
            path=self.path,
            contents=self.contents,
            user=self.user,
            filetype=self.filetype,
            workspace=self.workspace,
        )

    def vote(self, request: ReleaseRequest):
        if self.approved:
            review_file(request, self.path, RequestFileVote.APPROVED, *self.checkers)
        elif self.changes_requested:
            review_file(
                request, self.path, RequestFileVote.CHANGES_REQUESTED, *self.checkers
            )


def request_file(
    group="group",
    path: UrlPath | str = "test/file.txt",
    contents="",
    filetype=RequestFileType.OUTPUT,
    user: User | None = None,
    approved=False,
    changes_requested=False,
    checkers=None,
    **kwargs,
) -> TestRequestFile:
    """Helper function to define some test file metadata

    At the right points, this metadata will be used to populate and act on
    a request.
    """
    return TestRequestFile(
        group=group,
        path=UrlPath(path),
        contents=contents,
        filetype=filetype,
        user=user,
        # voting
        approved=approved,
        changes_requested=changes_requested,
        checkers=checkers or [],
    )


def submit_independent_review(request, *users):
    users = users or get_default_output_checkers()

    request = refresh_release_request(request)

    # caller's job to make sure all files have been voted on
    for user in users:
        bll.review_request(request, user)


def create_filegroup(release_request, group_name, filepaths=None):
    user = create_user(release_request.author, [release_request.workspace])
    for filepath in filepaths or []:  # pragma: nocover
        bll.add_file_to_request(release_request, filepath, user, group_name)
    return bll._dal._get_or_create_filegroupmetadata(release_request.id, group_name)  # type: ignore


def refresh_release_request(release_request, user=None) -> ReleaseRequest:
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

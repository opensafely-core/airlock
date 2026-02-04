import csv
import json
import subprocess
import tempfile
import typing
from dataclasses import dataclass, field
from hashlib import file_digest
from pathlib import Path

from django.conf import settings

from airlock import exceptions
from airlock.business_logic import bll
from airlock.enums import (
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    Visibility,
)
from airlock.lib.git import ensure_git_init
from airlock.models import (
    AuditEvent,
    CodeRepo,
    ReleaseRequest,
    RequestFile,
    Workspace,
)
from airlock.types import UrlPath
from users.models import User


def create_api_project(project="project", ongoing=True):
    """Test factory for project details"""
    return dict(name=project, ongoing=ongoing)


def create_api_workspace(
    project_details=None, archived=False, project="project", ongoing=True
):
    """Test factory for workspace details"""
    if project_details is None:
        project_details = create_api_project(project, ongoing)
    return dict(
        project_details=project_details,
        archived=archived,
    )


def create_api_user(
    *,
    username: str = "testuser",
    fullname: str = "Test User",
    workspaces: dict[str, typing.Any] | list[str] | None = None,
    copiloted_workspaces: dict[str, typing.Any] | list[str] | None = None,
    output_checker: bool | None = None,
):
    """Test factory to create a user from the Auth API

    For convenience, the factory accepts the following values for workspaces:
     - None:      create a default workspace called "workspace"
     - list[str]: create a list of workspaces with given names, and default
                  workspace values
     - dict[str, dict]: pass wholesale through to APIUser

    This allows the caller complete control, but makes the common case simple.
    """

    if workspaces is None:
        # default to default test workspace
        workspaces = ["workspace"]

    copiloted_workspaces = copiloted_workspaces or []

    return dict(
        username=username,
        fullname=fullname,
        workspaces=_create_workspaces(workspaces),
        copiloted_workspaces=_create_workspaces(copiloted_workspaces),
        output_checker=output_checker or False,
    )


def _create_workspaces(workspaces):
    actual_workspaces = {}
    if isinstance(workspaces, list):
        for workspace in workspaces:
            actual_workspaces[workspace] = create_api_workspace()
    elif isinstance(workspaces, dict):
        for k, v in workspaces.items():
            actual_workspaces[k] = create_api_workspace(**v)
    else:  # pragma: nocover
        raise Exception("bad workspaces parameter, should be dict, list, or None")
    return actual_workspaces


def create_airlock_user(
    *,
    username: str = "testuser",
    fullname: str = "Test User",
    workspaces: dict[str, typing.Any] | list[str] | None = None,
    copiloted_workspaces: dict[str, typing.Any] | list[str] | None = None,
    output_checker: bool | None = None,
    last_refresh: float | None = None,
) -> User:
    """Factory to create an Airlock User in the db.

    The username, workspaces,copiloted_workspaces, and output_checker, are all
    just passed through to create_api_user.
    """
    api_user = create_api_user(
        username=username,
        fullname=fullname,
        workspaces=workspaces,
        copiloted_workspaces=copiloted_workspaces,
        output_checker=output_checker,
    )
    return User.from_api_data(api_user, last_refresh)


def get_or_create_airlock_user(
    username: str = "testuser",
    fullname: str = "Test User",
    workspaces: dict[str, typing.Any] | list[str] | None = None,
    copiloted_workspaces: dict[str, typing.Any] | list[str] | None = None,
    output_checker: bool | None = None,
    last_refresh: float | None = None,
) -> User:
    """Get or create an airlock user.

    This is used within our factories when we need a user to be able to call
    a BLL method. If the user doesn't exist, we create them with the supplied
    data, exactly like create_airlock_user.

    However, if the user does already exist, for example when we are using the
    request.author, then we do something practical but hacky.

    Firstly, if there are workspaces supplied to the arguments to this function
    that the user currently doesn't have, we add them to the users api_data.
    This is a convenience so that the test author doesn't have to add all the
    workspaces needed explilcitly ahead of time - when calling a factory
    function, it will ensure the user has the right workspace permissions.

    However, for output_checker, we are stricter, or else we could accidentally
    change that.

    The output_checker default is now None, and if so, we do nothing, as the the
    call or this function expressed no opinion about whether this user should
    be an output checker or not.  However, if it is set to True or False, we
    assert that the pre-existing user is set the same, to help us catch logic
    errors in our tests or test factories.

    Hopefully, this complexity will be short lived. Once we have users that can
    be looked up, I (smd) expect the BLL interface may change to need username,
    rather than full User objects, which will change how all this works.
    """

    api_user = create_api_user(
        username=username,
        fullname=fullname,
        workspaces=workspaces,
        copiloted_workspaces=copiloted_workspaces,
        output_checker=output_checker,
    )
    try:
        user = User.objects.get(pk=username)
    except User.DoesNotExist:
        return User.from_api_data(api_user)

    # ok we have an existing user
    #
    # add any workspaces not already present
    additional_workspaces = {
        k: v for k, v in api_user["workspaces"].items() if k not in user.workspaces
    }
    if additional_workspaces:
        user.api_data["workspaces"].update(additional_workspaces)
        user.save()
    # add any copiloted workspaces not already present
    additional_copiloted_workspaces = {
        k: v
        for k, v in api_user["copiloted_workspaces"].items()
        if k not in user.copiloted_workspaces
    }
    if additional_copiloted_workspaces:  # pragma: no cover
        user.api_data["copiloted_workspaces"].update(additional_copiloted_workspaces)
        user.save()

    # output_checker=None means the caller did not specify
    if output_checker is not None:  # pragma: nocover
        # check that we are not accidentally expecting different roles
        assert user.output_checker is output_checker

    return user


def ensure_workspace(workspace_or_name: Workspace | str) -> Workspace:
    if isinstance(workspace_or_name, str):
        return create_workspace(workspace_or_name)
    elif isinstance(workspace_or_name, Workspace):
        return workspace_or_name

    raise Exception(f"Invalid workspace: {workspace_or_name})")  # pragma: nocover


# get_output_metadata is imported from job-runner
def get_output_metadata(
    abspath,
    level,
    job_id,
    job_request,
    action,
    commit,
    repo,
    excluded,
    user,
    message=None,
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

    metadata = {
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
    # Allow mocking a manifest that predates the introduction of 'user'
    if user is not None:
        metadata["user"] = user
    return metadata


def update_manifest(workspace: Workspace | str, files=None, user="author"):
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
        ws_outputs = set(
            Workspace.get_valid_filepaths_from_manifest_outputs(manifest["outputs"])
        ) & set(manifest["outputs"])
        if ws_outputs:
            first_output = manifest["outputs"][list(ws_outputs)[0]]
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
            level="moderately_sensitive",
            job_id=f"job_{i}",
            job_request=f"job_request_{i}",
            action=f"action_{i}",
            commit=current.get("commit", commit),
            repo=repo,
            excluded=False,
            user=user,
        )

    # Include a highly_sensitive in all manifests;
    # these should never be valid workspace files or appear in the file tree
    if "output/highly_sensitive.txt" not in manifest["outputs"]:
        manifest["outputs"]["output/highly_sensitive.txt"] = {
            "level": "highly_sensitive",
            "excluded": False,
            "size": 1,
            "timestamp": 1,
            "content_hash": "content",
        }

    manifest_path.parent.mkdir(exist_ok=True, parents=True)
    manifest_path.write_text(json.dumps(manifest, indent=2))

    if isinstance(workspace, Workspace):
        workspace.manifest = manifest
        workspace_file_paths = set(
            workspace.get_valid_filepaths_from_manifest_outputs(manifest["outputs"])
        ) | set(workspace.scan_metadata_dir(workspace.name))
        workspace_child_map, workspace_files = workspace.get_workspace_child_map(
            workspace_file_paths
        )
        workspace.workspace_child_map = workspace_child_map
        workspace.workspace_files = workspace_files


def create_workspace(name: str, user=None) -> Workspace:
    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = get_or_create_airlock_user(username="author", workspaces=[name])

    workspace_dir = settings.WORKSPACE_DIR / name
    if not workspace_dir.exists():
        workspace_dir.mkdir(exist_ok=True, parents=True)

    update_manifest(name)
    return bll.get_workspace(name, user)


def refresh_workspace(name: str, user=None) -> Workspace:
    # Fetch the workspace without rewriting the manifest file on disk
    if user is None:  # pragma: nocover
        user = get_or_create_airlock_user(username="author", workspaces=[name])
    return bll.get_workspace(name, user)


def write_workspace_file(
    workspace: Workspace | str, path, contents="", manifest=True, manifest_username=None
):
    workspace = ensure_workspace(workspace)
    abspath = workspace.root() / path
    abspath.parent.mkdir(parents=True, exist_ok=True)
    abspath.write_text(contents)
    if manifest:
        update_manifest(workspace, [path], user=manifest_username)


def create_repo(workspace: Workspace | str, files=None, temporary=True) -> CodeRepo:
    workspace = ensure_workspace(workspace)
    repo_dir = settings.GIT_REPO_DIR / f"{workspace.name}-repo"

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
    if not set(
        workspace.get_valid_filepaths_from_manifest_outputs(
            workspace.manifest["outputs"]
        )
    ):
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
    workspace: Workspace | str, user=None, status=None, **kwargs
) -> ReleaseRequest:
    if status:
        assert status == RequestStatus.PENDING, (
            "Use create_request_at_status to create a release request with a state other than PENDING"
        )
    workspace = ensure_workspace(workspace)

    # create a default user with permission on workspace
    if user is None:
        user = get_or_create_airlock_user(
            username="author", workspaces=[workspace.name]
        )

    release_request = bll.get_or_create_current_request(
        workspace=workspace.name, user=user, **kwargs
    )
    release_request.root().mkdir(parents=True, exist_ok=True)
    return release_request


def create_request_at_status(
    workspace: Workspace | str,
    status,
    author=None,
    files=None,
    checker=None,
    withdrawn_after=None,
    checker_comments=None,
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
            request_file(approved=True, comment=True),
            request_file(approved=True, filetype=RequestFileType.SUPPORTING),
        ]
    )

    `checker` is the user who will do any status changes or actions that require an
    output-checker. If not provided, a default output checker is used.

    Optionally, `request_file` can be given `checkers`, a list of users who will
    review (approve/request changes to) the file. If not provided, default output checkers
    will be used.

    `request_file` can also be given a boolean `comment`, whether to comment on the
    filegroup for a file. If None, comments will be added for files for which
    changes_requested is True (which will allow the review to be submitted.)
    """
    author = author or create_airlock_user(
        username="author",
        workspaces=[workspace if isinstance(workspace, str) else workspace.name],
    )
    if status == RequestStatus.WITHDRAWN:
        assert withdrawn_after is not None, (
            "pass withdrawn_after to decide when to withdraw"
        )
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
    if files:  # pragma: no cover
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

    if checker_comments:
        # create comments
        for group, comment, visibility in checker_comments:
            bll.group_comment_create(request, group, comment, visibility, checker)

    request = refresh_release_request(request)

    # apply votes to files.
    # Note: submitted requests must have at least one output file
    for testfile in files:
        testfile.vote(request)
    request = refresh_release_request(request)

    if status == RequestStatus.SUBMITTED:
        return request

    # Get the usernames of all file reviewers so we can submit reviews with
    # the correct checkers.
    # Submitting a release request with no output files is not allowed.
    file_reviewers = [
        create_airlock_user(username=username, output_checker=True)
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
        # add a public comment for any group with changes requested
        for group_name in request.filegroups_missing_public_comment():
            bll.group_comment_create(
                request, group_name, "A public comment", Visibility.PUBLIC, checker
            )
        request = refresh_release_request(request)

        bll.return_request(request, checker)
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

    if status in [RequestStatus.APPROVED, RequestStatus.RELEASED]:
        bll.release_files(
            request,
            user=checker,
        )
        request = refresh_release_request(request)

        # upload files.
        # Note: approved requests may have some uploaded files
        # Released requests have all uploaded files
        for testfile in files:
            if status == RequestStatus.RELEASED:
                testfile.uploaded = True
            testfile.upload(request, checker)

        if status == RequestStatus.RELEASED:
            bll.set_status(request, RequestStatus.RELEASED, checker)

        return refresh_release_request(request)

    raise Exception(f"invalid state: {status}")  # pragma: no cover


def add_request_file(
    request,
    group,
    path,
    contents="",
    user: User | None = None,
    filetype=RequestFileType.OUTPUT,
    workspace: Workspace | str | None = None,
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
        user = get_or_create_airlock_user(request.author, workspaces=[workspace.name])

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
        create_airlock_user(username="output-checker-0", output_checker=True),
        create_airlock_user(username="output-checker-1", output_checker=True),
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


def comment_on_request_file_group(
    request: ReleaseRequest,
    relpath: UrlPath,
    *users,
):
    if not users:
        users = get_default_output_checkers()

    for user in users:
        request_file = request.get_request_file_from_output_path(relpath)
        bll.group_comment_create(
            request,
            request_file.group,
            f"A comment on {relpath}",
            Visibility.PRIVATE,
            user,
        )


@dataclass
class TestRequestFile:
    """Placeholder containing file metadata.

    Allows us to set up file states declaratively. The add(), vote()
    and upload() methods can be called with a request in the right state.
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
    comment: bool | None = True

    # uploading
    uploaded: bool = True

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
            review_file(
                request,
                self.path,
                RequestFileVote.APPROVED,
                *self.checkers,
            )
        elif self.changes_requested:
            review_file(
                request,
                self.path,
                RequestFileVote.CHANGES_REQUESTED,
                *self.checkers,
            )

        if (self.comment is None and self.changes_requested) or self.comment:
            comment_on_request_file_group(request, self.path, *self.checkers)

    def upload(self, request: ReleaseRequest, user: User):
        request = refresh_release_request(request)
        if self.approved and self.uploaded:
            bll.register_file_upload_attempt(request, self.path)
            bll.register_file_upload(request, self.path, user)


def request_file(
    group="group",
    path: UrlPath | str = "test/file.txt",
    contents: str = "",
    filetype=RequestFileType.OUTPUT,
    user: User | None = None,
    approved=False,
    changes_requested=False,
    checkers=None,
    comment: bool | None = None,
    uploaded=False,
    **kwargs,
) -> TestRequestFile:
    """Helper function to define some test file metadata

    At the right points, this metadata will be used to populate and act on
    a request.
    """
    # Files with the same contents cannot be uploaded in the same workspace, so if no
    # contents are provided we use the path to ensure that different files have
    # different contents.
    contents = contents or str(path)

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
        comment=comment,
        # released and uploaded
        uploaded=uploaded,
    )


def submit_independent_review(request, *users):
    users = users or get_default_output_checkers()

    request = refresh_release_request(request)

    # caller's job to make sure all files have been voted on
    # and comments added to filegroups for changes requested
    for user in users:
        bll.review_request(request, user)


def create_filegroup(release_request, group_name, filepaths=None):
    user = get_or_create_airlock_user(
        username=release_request.author, workspaces=[release_request.workspace]
    )
    for filepath in filepaths or []:  # pragma: nocover
        bll.add_file_to_request(release_request, filepath, user, group_name)
    return bll._dal._get_or_create_filegroupmetadata(release_request.id, group_name)  # type: ignore


def refresh_release_request(release_request, user=None) -> ReleaseRequest:
    # create a default user with permission on workspace
    if user is None:  # pragma: nocover
        user = get_or_create_airlock_user(
            username="author", workspaces=[release_request.workspace]
        )
    return bll.get_release_request(release_request.id, user)


def create_audit_event(
    type_,
    user=None,
    workspace: str = "workspace",
    request="request",
    path=UrlPath("foo/bar"),
    extra={"foo": "bar"},
):
    if user is None:
        user = create_airlock_user(username="user")
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


def non_metadata_workspace_filepaths(workspace):
    metadata_path = workspace.root() / "metadata"
    for fp in workspace.root().rglob("*"):
        if fp != metadata_path and metadata_path not in fp.parents:
            yield fp


def delete_workspace_files(workspace):
    # delete all files on disk other than the metadata files
    for filepath in non_metadata_workspace_filepaths(workspace):
        if filepath.is_file():
            filepath.unlink()
    # delete all (now empty) non-metadata directories
    for filepath in non_metadata_workspace_filepaths(workspace):
        filepath.rmdir()

    assert not list(non_metadata_workspace_filepaths(workspace))

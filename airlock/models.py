from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any, Self

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from airlock import exceptions, permissions, renderers
from airlock.enums import (
    AuditEventType,
    RequestFileDecision,
    RequestFileType,
    RequestFileVote,
    RequestStatus,
    RequestStatusOwner,
    ReviewTurnPhase,
    Visibility,
    WorkspaceFileStatus,
)
from airlock.lib.git import (
    GitError,
    list_files_from_repo,
    project_name_from_url,
    read_file_from_repo,
)
from airlock.types import ROOT_PATH, FileMetadata, FilePath
from airlock.visibility import RequestFileStatus, filter_visible_items
from users.models import User


AUDIT_MSG_FORMATS = {
    AuditEventType.WORKSPACE_FILE_VIEW: "Viewed file",
    AuditEventType.REQUEST_FILE_VIEW: "Viewed file",
    AuditEventType.REQUEST_FILE_DOWNLOAD: "Downloaded file",
    AuditEventType.REQUEST_CREATE: "Created request",
    AuditEventType.REQUEST_SUBMIT: "Submitted request",
    AuditEventType.REQUEST_WITHDRAW: "Withdrew request",
    AuditEventType.REQUEST_REVIEW: "Submitted review",
    AuditEventType.REQUEST_APPROVE: "Approved request",
    AuditEventType.REQUEST_REJECT: "Rejected request",
    AuditEventType.REQUEST_RETURN: "Returned request",
    AuditEventType.REQUEST_RELEASE: "Released request",
    AuditEventType.REQUEST_EDIT: "Edited the Context/Controls",
    AuditEventType.REQUEST_COMMENT: "Commented",
    AuditEventType.REQUEST_COMMENT_DELETE: "Comment deleted",
    AuditEventType.REQUEST_COMMENT_VISIBILITY_PUBLIC: "Private comment made public",
    AuditEventType.REQUEST_FILE_ADD: "Added file",
    AuditEventType.REQUEST_FILE_UPDATE: "Updated file",
    AuditEventType.REQUEST_FILE_WITHDRAW: "Withdrew file from group",
    AuditEventType.REQUEST_FILE_APPROVE: "Approved file",
    AuditEventType.REQUEST_FILE_REQUEST_CHANGES: "Changes requested to file",
    AuditEventType.REQUEST_FILE_RESET_REVIEW: "Reset review of file",
    AuditEventType.REQUEST_FILE_UNDECIDED: "File with changes requested moved to undecided",
    AuditEventType.REQUEST_FILE_RELEASE: "File released",
    AuditEventType.REQUEST_FILE_UPLOAD: "File uploaded",
}


@dataclass
class AuditEvent:
    type: AuditEventType
    user: User
    workspace: str | None = None
    request: str | None = None
    path: FilePath | None = None
    extra: dict[str, str] = field(default_factory=dict)
    # this is used when querying the db for audit log times
    created_at: datetime = field(default_factory=timezone.now, compare=False)

    WIDTH = max(len(k.name) for k in AuditEventType)

    @classmethod
    def from_request(
        cls,
        request: ReleaseRequest,
        type: AuditEventType,  # noqa: A002
        user: User,
        path: FilePath | None = None,
        **kwargs: str,
    ):
        # Note: kwargs go straight to extra
        # set review_turn from request
        kwargs["review_turn"] = str(request.review_turn)
        event = cls(
            type=type,
            user=user,
            workspace=request.workspace,
            request=request.id,
            extra=kwargs,
        )
        if path:
            event.path = path

        return event

    def __str__(self):
        ts = self.created_at.isoformat()[:-13]  # seconds precision
        msg = [
            f"{ts}: {self.type.name:<{self.WIDTH}} user={self.user} workspace={self.workspace}"
        ]

        if self.request:
            msg.append(f"request={self.request}")
        if self.path:
            msg.append(f"path={self.path}")

        for k, v in self.extra.items():
            msg.append(f"{k}={v}")

        return " ".join(msg)

    def description(self):
        return AUDIT_MSG_FORMATS[self.type]

    @property
    def review_turn(self) -> int:
        return int(self.extra.get("review_turn", 0))

    @property
    def author(self) -> User:
        return self.user

    @property
    def visibility(self) -> Visibility:
        v = self.extra.get("visibility")
        if v:
            return Visibility[v.upper()]
        else:
            return Visibility.PUBLIC


@dataclass(frozen=True)
class Project:
    name: str
    is_ongoing: bool

    def display_name(self):
        # helper for templates
        if not self.is_ongoing:
            return f"{self.name} (INACTIVE)"
        return self.name


@dataclass(order=True)
class Workspace:
    """Simple wrapper around a workspace directory on disk.

    Deliberately a dumb python object - the only operations are about accessing
    filepaths within the workspace directory, and related urls.
    """

    name: str
    manifest: dict[str, Any]
    metadata: dict[str, Any]
    current_request: ReleaseRequest | None
    released_files: set[str]

    @classmethod
    def from_directory(
        cls,
        name: str,
        metadata: dict[str, str] | None = None,
        current_request: ReleaseRequest | None = None,
        released_files: set[str] | None = None,
    ) -> Workspace:
        root = settings.WORKSPACE_DIR / name
        if not root.exists():
            raise exceptions.WorkspaceNotFound(name)

        manifest_path = root / "metadata/manifest.json"
        if not manifest_path.exists():
            raise exceptions.ManifestFileError(f"{manifest_path} does not exist")

        try:
            manifest = json.loads(manifest_path.read_text())
        except json.JSONDecodeError as exc:
            raise exceptions.ManifestFileError(
                f"Could not parse manifest.json file: {manifest_path}:\n{exc}"
            )

        if metadata is None:  # pragma: no cover
            metadata = {}

        return cls(
            name,
            manifest=manifest,
            metadata=metadata,
            current_request=current_request,
            released_files=released_files or set(),
        )

    def __str__(self):
        return self.get_id()

    def project(self) -> Project:
        details = self.metadata.get("project_details", {})
        return Project(
            name=details.get("name", "Unknown project"),
            is_ongoing=details.get("ongoing", True),
        )

    def is_archived(self):
        return self.metadata.get("archived")

    # helpers for templates
    def is_active(self):
        return self.project().is_ongoing and not self.is_archived()

    def display_name(self):
        # helper for templates
        if self.is_archived():
            return f"{self.name} (ARCHIVED)"
        return self.name

    def root(self):
        return settings.WORKSPACE_DIR / self.name

    def manifest_path(self):
        return self.root() / "metadata/manifest.json"

    def get_id(self) -> str:
        return self.name

    def get_url(self, file_path: FilePath = ROOT_PATH) -> str:
        kwargs = {"workspace_name": self.name}
        if file_path != ROOT_PATH:
            kwargs["path"] = str(file_path)
        return reverse("workspace_view", kwargs=kwargs)

    def get_workspace_file_status(
        self, file_path: FilePath
    ) -> WorkspaceFileStatus | None:
        # get_file_metadata will throw FileNotFound if we have a bad file path
        metadata = self.get_file_metadata(file_path)

        # check if file has been released once we can do that
        if metadata and metadata.content_hash in self.released_files:
            return WorkspaceFileStatus.RELEASED

        if self.current_request:
            try:
                rfile = self.current_request.get_request_file_from_output_path(
                    file_path
                )
            except exceptions.FileNotFound:
                return WorkspaceFileStatus.UNRELEASED

            if metadata is None:  # pragma: no cover
                raise exceptions.ManifestFileError(
                    f"no file metadata available for {file_path}"
                )
            if rfile.filetype is RequestFileType.WITHDRAWN:
                return WorkspaceFileStatus.WITHDRAWN
            elif rfile.file_id == metadata.content_hash:
                return WorkspaceFileStatus.UNDER_REVIEW
            else:
                return WorkspaceFileStatus.CONTENT_UPDATED

        return WorkspaceFileStatus.UNRELEASED

    def get_request_file_status(
        self, file_path: FilePath, user: User
    ) -> RequestFileStatus | None:
        return None  # pragma: nocover

    def get_requests_url(self):
        return reverse(
            "requests_for_workspace",
            kwargs={"workspace_name": self.name},
        )

    def get_contents_url(
        self, file_path: FilePath, download: bool = False, plaintext: bool = False
    ) -> str:
        url = reverse(
            "workspace_contents",
            kwargs={"workspace_name": self.name, "path": file_path},
        )

        renderer = self.get_renderer(file_path, plaintext=plaintext)
        plaintext_param = "&plaintext=true" if plaintext else ""
        url += f"?cache_id={renderer.cache_id}{plaintext_param}"

        return url

    def get_renderer(
        self, file_path: FilePath, plaintext: bool = False
    ) -> renderers.Renderer:
        renderer_class = renderers.get_renderer(file_path, plaintext=plaintext)
        return renderer_class.from_file(
            self.abspath(file_path),
            file_path=file_path,
        )

    def get_manifest_for_file(self, file_path: FilePath):
        try:
            return self.manifest["outputs"][str(file_path)]
        except KeyError:
            raise exceptions.ManifestFileError(
                f"No entry for {file_path} from manifest.json file"
            )

    def get_file_metadata(self, file_path: FilePath) -> FileMetadata | None:
        """Get file metadata, i.e. size, timestamp, hash"""
        try:
            return FileMetadata.from_manifest(self.get_manifest_for_file(file_path))
        except exceptions.ManifestFileError:
            pass

        # not in manifest, e.g. log file. Check disk
        return FileMetadata.from_path(self.abspath(file_path))

    def abspath(self, file_path):
        """Get absolute path for file

        Protects against traversal, and ensures the path exists."""
        root = self.root()
        path = root / file_path

        # protect against traversal
        path.resolve().relative_to(root)

        # validate path exists
        if not path.exists():
            raise exceptions.FileNotFound(path)

        return path

    def request_filetype(self, file_path: FilePath) -> None:
        return None


@dataclass(frozen=True)
class CodeRepo:
    workspace: str
    repo: str
    name: str
    commit: str
    directory: Path
    pathlist: list[FilePath]

    class RepoNotFound(Exception):
        pass

    class CommitNotFound(Exception):
        pass

    @classmethod
    def from_workspace(cls, workspace: Workspace, commit: str) -> CodeRepo:
        try:
            repo = list(workspace.manifest["outputs"].values())[0]["repo"]
        except (exceptions.ManifestFileError, IndexError, KeyError) as exc:
            raise cls.RepoNotFound(
                f"Could not parse manifest.json file: {workspace.manifest_path()}:\n{exc}"
            )

        try:
            pathlist = list_files_from_repo(repo, commit)
        except GitError as exc:
            raise CodeRepo.CommitNotFound(str(exc))

        return cls(
            workspace=workspace.name,
            repo=repo,
            name=project_name_from_url(repo),
            commit=commit,
            directory=settings.GIT_REPO_DIR / workspace.name,
            pathlist=pathlist,
        )

    def get_id(self) -> str:
        return f"{self.name}@{self.commit[:7]}"

    def get_url(self, file_path: FilePath = ROOT_PATH) -> str:
        kwargs = {
            "workspace_name": self.workspace,
            "commit": self.commit,
        }
        if file_path != ROOT_PATH:
            kwargs["path"] = str(file_path)
        return reverse(
            "code_view",
            kwargs=kwargs,
        )

    def get_file_state(self, file_path: FilePath) -> WorkspaceFileStatus | None:
        """Get state of path."""
        return None  # pragma: no cover

    def get_contents_url(
        self,
        file_path: FilePath = ROOT_PATH,
        download: bool = False,
        plaintext: bool = False,
    ) -> str:
        url = reverse(
            "code_contents",
            kwargs={
                "workspace_name": self.workspace,
                "commit": self.commit,
                "path": file_path,
            },
        )

        renderer = self.get_renderer(file_path, plaintext=plaintext)
        plaintext_param = "&plaintext=true" if plaintext else ""
        url += f"?cache_id={renderer.cache_id}{plaintext_param}"

        return url

    def get_renderer(self, file_path: FilePath, plaintext=False) -> renderers.Renderer:
        # we do not care about valid file types here, so we just get the base renderers

        try:
            contents = read_file_from_repo(self.repo, self.commit, file_path)
        except GitError as exc:
            raise exceptions.FileNotFound(str(exc))

        renderer_class = renderers.get_code_renderer(file_path, plaintext=plaintext)
        # note: we don't actually need an explicit cache_id here, as the commit is
        # already in the url. But we want to add the template version to the
        # cache id, so pass an empty string.
        return renderer_class.from_contents(
            contents=contents,
            file_path=file_path,
            cache_id="",
        )

    def get_file_metadata(self, file_path: FilePath) -> FileMetadata | None:
        """Get the size of a file"""
        return None  # pragma: no cover

    def request_filetype(self, file_path: FilePath) -> RequestFileType | None:
        return RequestFileType.CODE

    def get_workspace_file_status(
        self, file_path: FilePath
    ) -> WorkspaceFileStatus | None:
        return None

    def get_request_file_status(
        self, file_path: FilePath, user: User
    ) -> RequestFileStatus | None:
        return None  # pragma: nocover


@dataclass(frozen=True)
class FileReview:
    """
    Represents a review of a file in the context of a release request
    """

    reviewer: User
    status: RequestFileVote
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, attrs):
        return cls(
            **{k: v for k, v in attrs.items() if k != "reviewer"},
            reviewer=User.objects.get(pk=attrs["reviewer"]),
        )


@dataclass(frozen=True)
class RequestFile:
    """
    Represents a single file within a release request
    """

    file_path: FilePath
    group: str
    file_id: str
    reviews: dict[str, FileReview]
    timestamp: int
    size: int
    job_id: str
    commit: str
    repo: str
    released_by: User | None = None
    row_count: int | None = None
    col_count: int | None = None
    filetype: RequestFileType = RequestFileType.OUTPUT
    released_at: datetime | None = None
    uploaded: bool = False
    upload_attempts: int = 0
    uploaded_at: datetime | None = None
    upload_attempted_at: datetime | None = None

    @classmethod
    def from_dict(cls, attrs) -> Self:
        released_by = (
            User.objects.get(pk=attrs["released_by"])
            if attrs.get("released_by")
            else None
        )
        return cls(
            **{k: v for k, v in attrs.items() if k not in ["reviews", "released_by"]},
            reviews={
                value["reviewer"]: FileReview.from_dict(value)
                for value in attrs.get("reviews", ())
            },
            released_by=released_by,
        )

    def get_decision(self, submitted_reviewers) -> RequestFileDecision:
        """
        The status of RequestFile, based on multiple reviews.

        Disclosivity can only be assessed by considering all files in a release
        together. Therefore an overall decision on a file is based on votes from
        from submitted reviews only.

        We specificially only require 2 APPROVED votes (within submitted reviews),
        rather than all votes being APPROVED, as this allows a 3rd review to mark
        a file APPROVED to unblock things if one of the initial reviewers is unavailable.
        """
        all_reviews = [
            v.status
            for v in self.reviews.values()
            if v.reviewer.user_id in submitted_reviewers
        ]

        if len(all_reviews) < 2:
            # not enough votes yet
            return RequestFileDecision.INCOMPLETE

        # if we have 2+ APPROVED reviews, we are APPROVED
        if all_reviews.count(RequestFileVote.APPROVED) >= 2:
            return RequestFileDecision.APPROVED

        # do the reviews disagree?
        if len(set(all_reviews)) > 1:
            return RequestFileDecision.CONFLICTED

        # only case left is all reviews are CHANGES_REQUESTED
        return RequestFileDecision.CHANGES_REQUESTED

    def get_file_vote_for_user(self, user: User) -> RequestFileVote | None:
        if user.username in self.reviews:
            return self.reviews[user.username].status
        else:
            return None

    def changes_requested_reviews(self):
        return [
            review
            for review in self.reviews.values()
            if review.status == RequestFileVote.CHANGES_REQUESTED
        ]

    def upload_in_progress(self):
        return self.released_at is not None and not self.uploaded

    def can_attempt_upload(self):
        return self.upload_in_progress() and (
            self.upload_attempted_at is None
            or self.upload_attempted_at
            < (timezone.now() - timedelta(seconds=settings.UPLOAD_RETRY_DELAY))
        )


@dataclass(frozen=True)
class FileGroup:
    """
    Represents a group of one or more files within a release request
    """

    name: str
    files: dict[FilePath, RequestFile]
    context: str = ""
    controls: str = ""
    updated_at: datetime = field(default_factory=timezone.now)
    comments: list[Comment] = field(default_factory=list)

    @property
    def output_files(self):
        return [f for f in self.files.values() if f.filetype == RequestFileType.OUTPUT]

    @property
    def supporting_files(self):
        return [
            f for f in self.files.values() if f.filetype == RequestFileType.SUPPORTING
        ]

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k not in ["files", "comments"]},
            files={
                FilePath(value["file_path"]): RequestFile.from_dict(value)
                for value in attrs.get("files", ())
            },
            comments=[Comment.from_dict(c) for c in attrs.get("comments", [])],
        )

    def has_public_comment_for_turn(self, review_turn):
        return any(
            comment
            for comment in self.comments
            if comment.review_turn == review_turn
            and comment.visibility == Visibility.PUBLIC
        )

    def empty(self):
        return not (self.output_files or self.supporting_files)

    def incomplete(self):
        # Only consider non-empty groups
        return not self.empty() and not (self.context and self.controls)


@dataclass(frozen=True)
class Comment:
    """A user comment on a group"""

    id: str
    comment: str
    author: User
    created_at: datetime
    visibility: Visibility
    review_turn: int

    @classmethod
    def from_dict(cls, attrs):
        # `id` is implemented as an `int` in the current DAL, and as a `str`
        # in the BLL, so we need to add a conversion here (instead of just passing
        # it straight through with the other `attrs`)
        return cls(
            **{k: v for k, v in attrs.items() if k not in ["id", "author"]},
            id=str(attrs["id"]),
            author=User.objects.get(pk=attrs["author"]),
        )


@dataclass
class ReleaseRequest:
    """Represents a release request made by a user.

    Deliberately a dumb python object. Does not operate on the state of request,
    except for the request directory on disk and the files stored within.

    All other state is provided at instantiation by the provider.
    """

    id: str
    workspace: str
    author: User
    created_at: datetime
    status: RequestStatus = RequestStatus.PENDING
    filegroups: dict[str, FileGroup] = field(default_factory=dict)
    submitted_reviews: dict[str, str] = field(default_factory=dict)
    turn_reviewers: set[str] = field(default_factory=set)
    review_turn: int = 0

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k not in ["filegroups", "author"]},
            filegroups=cls._filegroups_from_dict(attrs.get("filegroups", {})),
            author=User.objects.get(pk=attrs["author"]),
        )

    @staticmethod
    def _filegroups_from_dict(attrs):
        return {key: FileGroup.from_dict(value) for key, value in attrs.items()}

    def __post_init__(self):
        self.root().mkdir(parents=True, exist_ok=True)

    def __str__(self):
        return self.get_id()

    def root(self):
        return settings.REQUEST_DIR / self.workspace / self.id

    def get_id(self):
        return self.id

    def get_short_id(self):
        return f"{self.id[:3]}...{self.id[-6:]}"

    def get_url(self, file_path=""):
        return reverse(
            "request_view",
            kwargs={
                "request_id": self.id,
                "path": file_path,
            },
        )

    def get_contents_url(
        self, file_path: FilePath, download: bool = False, plaintext: bool = False
    ):
        url = reverse(
            "request_contents",
            kwargs={"request_id": self.id, "path": file_path},
        )
        if download:
            url += "?download"
        else:
            # what renderer would render this file?
            renderer = self.get_renderer(file_path, plaintext=plaintext)
            plaintext_param = "&plaintext=true" if plaintext else ""
            url += f"?cache_id={renderer.cache_id}{plaintext_param}"

        return url

    def get_renderer(
        self, file_path: FilePath, plaintext: bool = False
    ) -> renderers.Renderer:
        request_file = self.get_request_file_from_urlpath(file_path)
        renderer_class = renderers.get_renderer(file_path, plaintext=plaintext)
        return renderer_class.from_file(
            self.abspath(file_path),
            file_path=request_file.file_path,
            cache_id=request_file.file_id,
        )

    def get_file_metadata(self, file_path: FilePath) -> FileMetadata | None:
        rfile = self.get_request_file_from_urlpath(file_path)
        return FileMetadata(
            rfile.size,
            rfile.timestamp,
            _content_hash=rfile.file_id,
        )

    def get_workspace_file_status(
        self, file_path: FilePath
    ) -> WorkspaceFileStatus | None:
        return None

    def get_request_file_status(
        self, file_path: FilePath, user: User
    ) -> RequestFileStatus | None:
        rfile = self.get_request_file_from_urlpath(file_path)
        phase = self.get_turn_phase()
        decision = RequestFileDecision.INCOMPLETE
        can_review = permissions.user_can_review_request(user, self)
        submitted_reviewers_this_turn = self.submitted_reviews.keys()

        # If we're in the AUTHOR phase of a turn (i.e. the request is being
        # edited by the author, it's not in an under-review status), we need
        # to show the decision from the previous turn, if there is one. For
        # all other (reviewing) phases, we show the current decision based on
        # the submitted reviews in this turn.
        match phase:
            case ReviewTurnPhase.INDEPENDENT:
                # already set - no one knows the current status
                pass
            case ReviewTurnPhase.CONSOLIDATING:
                # only users who can review this request know the current status
                if can_review:
                    decision = rfile.get_decision(submitted_reviewers_this_turn)
            case ReviewTurnPhase.COMPLETE:
                # everyone knows the current status
                decision = rfile.get_decision(submitted_reviewers_this_turn)
            case ReviewTurnPhase.AUTHOR:
                # everyone can the status at the end of the previous turn
                decision = rfile.get_decision(self.turn_reviewers)
            case _:  # pragma: nocover
                assert False

        return RequestFileStatus(
            user=user,
            decision=decision,
            vote=rfile.get_file_vote_for_user(user),
        )

    def get_visible_comments_for_group(
        self, group: str, user: User
    ) -> list[tuple[Comment, str]]:
        filegroup = self.filegroups[group]
        current_phase = self.get_turn_phase()

        comments = []
        visible_comments = filter_visible_items(
            filegroup.comments,
            self.review_turn,
            current_phase,
            permissions.user_can_review_request(user, self),
            user,
        )

        for comment in visible_comments:
            # does this comment need to be blinded?
            if (
                comment.review_turn == self.review_turn
                and current_phase == ReviewTurnPhase.INDEPENDENT
            ):
                html_class = "comment_blinded"
            else:
                html_class = f"comment_{comment.visibility.name.lower()}"

            comments.append((comment, html_class))

        return comments

    def get_request_file_from_urlpath(self, file_path: FilePath | str) -> RequestFile:
        """Get the request file from the url, which includes the group."""
        file_path = FilePath(file_path)
        group = file_path.parts[0]
        file_file_path = FilePath(*file_path.parts[1:])

        if not (filegroup := self.filegroups.get(group)):
            raise exceptions.FileNotFound(f"bad group {group} in url {file_path}")

        if not (request_file := filegroup.files.get(file_file_path)):
            raise exceptions.FileNotFound(file_path)

        return request_file

    def get_request_file_from_file_path(self, file_path: FilePath | str):
        """Get the request file from the output path, which does not include the group"""
        file_path = FilePath(file_path)
        if file_path not in self.all_files_by_name:
            raise exceptions.FileNotFound(file_path)

        return self.all_files_by_name[file_path]

    def get_turn_phase(self) -> ReviewTurnPhase:
        if self.status in [RequestStatus.PENDING, RequestStatus.RETURNED]:
            return ReviewTurnPhase.AUTHOR

        if self.status in [RequestStatus.SUBMITTED, RequestStatus.PARTIALLY_REVIEWED]:
            return ReviewTurnPhase.INDEPENDENT

        if self.status in [RequestStatus.REVIEWED]:
            return ReviewTurnPhase.CONSOLIDATING

        return ReviewTurnPhase.COMPLETE

    def get_writable_comment_visibilities_for_user(
        self, user: User
    ) -> list[Visibility]:
        """What comment visibilities should this user be able to write for this request?"""
        is_author = user == self.author

        # author can only ever create public comments
        if is_author:
            return [Visibility.PUBLIC]

        # non-author non-output-checker, also only public
        if not user.output_checker:
            return [Visibility.PUBLIC]

        # in editing status, only public comments are allowed, even for output-checkers
        if self.is_editing():
            return [Visibility.PUBLIC]

        # all other cases - the output-checker can choose to write public or private comments
        return [Visibility.PRIVATE, Visibility.PUBLIC]

    def abspath(self, file_path):
        """Returns abspath to the file on disk.

        The first part of the file_path is the group, so we parse and validate that first.
        """
        request_file = self.get_request_file_from_urlpath(file_path)
        return self.root() / request_file.file_id

    @cached_property
    def all_files_by_name(self) -> dict[FilePath, RequestFile]:
        """Return the file_paths for all files on the request, of any filetype"""
        return {
            request_file.file_path: request_file
            for filegroup in self.filegroups.values()
            for request_file in filegroup.files.values()
        }

    def output_files(self) -> dict[FilePath, RequestFile]:
        """Return the file_paths for output files on the request"""
        return {
            rfile.file_path: rfile
            for rfile in self.all_files_by_name.values()
            if rfile.filetype == RequestFileType.OUTPUT
        }

    def uploaded_files_count(self) -> int:
        if self.status not in [RequestStatus.APPROVED, RequestStatus.RELEASED]:
            return 0
        return sum(1 for rfile in self.output_files().values() if rfile.uploaded)

    def uploaded_files_count_url(self):
        return reverse("uploaded_files_count", args=(self.id,))

    def supporting_files_count(self):
        return sum(
            1
            for rfile in self.all_files_by_name.values()
            if rfile.filetype == RequestFileType.SUPPORTING
        )

    def request_filetype(self, urlpath: FilePath):
        try:
            return self.get_request_file_from_urlpath(urlpath).filetype
        except exceptions.FileNotFound:
            # this includes the case when urlpath is an output directory
            # e.g. `foo` when the request contains `foo/bar.txt`
            return None

    def set_filegroups_from_dict(self, attrs):
        self.filegroups = self._filegroups_from_dict(attrs)

    def get_output_file_paths(self):
        paths = []
        for file_group in self.filegroups.values():
            for request_file in file_group.output_files:
                file_path = request_file.file_path
                abspath = self.abspath(file_group.name / file_path)
                paths.append((file_path, abspath))
        return paths

    def all_files_approved(self):
        return all(
            request_file.get_decision(self.submitted_reviews.keys())
            == RequestFileDecision.APPROVED
            for request_file in self.output_files().values()
        )

    def files_reviewed_by_reviewer_count(self, reviewer: User) -> int:
        return sum(
            1
            for rfile in self.output_files().values()
            if rfile.get_file_vote_for_user(reviewer)
            not in [None, RequestFileVote.UNDECIDED]
        )

    def all_files_reviewed_by_reviewer(self, reviewer: User) -> bool:
        return self.files_reviewed_by_reviewer_count(reviewer) == len(
            self.output_files()
        )

    def submitted_reviews_count(self):
        return len(self.submitted_reviews)

    def filegroups_missing_comment_by_reviewer(self, reviewer) -> set[str]:
        groups_with_missing_comments = set()
        comments_checked = set()
        for rfile in self.output_files().values():
            if rfile.group in groups_with_missing_comments:
                # We already know this group is missing a comment, no need to check this
                # file
                continue
            if rfile.group in comments_checked:
                # We've already checked for comments for a file with changes requested
                # and we know the group has a comment, no need to check this file
                continue
            if (
                rfile.get_file_vote_for_user(reviewer)
                != RequestFileVote.CHANGES_REQUESTED
            ):
                # comments are only required for files with changes requested
                continue
            filegroup = self.filegroups[rfile.group]
            user_comments_this_turn = [
                comment
                for comment in filegroup.comments
                if comment.author == reviewer
                and comment.review_turn == self.review_turn
            ]
            comments_checked.add(rfile.group)
            if not user_comments_this_turn:
                groups_with_missing_comments.add(rfile.group)

        return groups_with_missing_comments

    def all_filegroups_commented_by_reviewer(self, reviewer: User) -> bool:
        """
        Reviewer has commented on all filegroups that contain files for which
        they have requested changes in this turn.
        """
        return not bool(self.filegroups_missing_comment_by_reviewer(reviewer))

    def filegroups_missing_public_comment(self) -> list[str]:
        """
        A filegroup requires a public comment in the current turn if:
        - it is currently RETURNED OR
        - it is under review and independent review is complete
        AND:
        - any of its files have CONFLICTED or CHANGES_REQUESTED decisions
        - any of its output files have INCOMPLETE decisions
        (INCOMPLETE output files will be files on a returned request that have been
        newly added, moved between groups, changed from supporting to output type,
        or withdrawn and re-added)
        Note for returned requests we look at turn_reviewers - reviewers in the
        previous turn. For under-review requests, we look at submitted reviews
        in the current turn.
        """
        if self.all_files_approved():
            return []

        match self.status:
            case RequestStatus.RETURNED:
                reviewers = self.turn_reviewers
            case RequestStatus.REVIEWED:
                # public comments are not enforced until independent reivew is complete
                reviewers = set(self.submitted_reviews.keys())
            case _:
                return []

        def _requires_public_comment(rfile):
            decision = rfile.get_decision(reviewers)
            if (
                decision == RequestFileDecision.INCOMPLETE
                and rfile.filetype == RequestFileType.OUTPUT
            ):
                return True
            return decision in [
                RequestFileDecision.CHANGES_REQUESTED,
                RequestFileDecision.CONFLICTED,
            ]

        return [
            group_name
            for group_name, filegroup in self.filegroups.items()
            if not filegroup.has_public_comment_for_turn(self.review_turn)
            and any(
                _requires_public_comment(request_file)
                for request_file in filegroup.files.values()
            )
        ]

    def status_owner(self) -> RequestStatusOwner:
        return permissions.STATUS_OWNERS[self.status]

    def can_be_released(self) -> bool:
        return self.status == RequestStatus.REVIEWED and self.all_files_approved()

    def upload_in_progress(self) -> bool:
        """
        A request is uploading if it has been approved irrespective of whether all
        its files have been uploaded yet. It is still considered to be in uploading
        state until its status changed to RELEASED
        """
        return self.status == RequestStatus.APPROVED

    def is_final(self):
        return self.status_owner() == RequestStatusOwner.SYSTEM

    def is_under_review(self):
        return self.status_owner() == RequestStatusOwner.REVIEWER

    def is_editing(self):
        return self.status_owner() == RequestStatusOwner.AUTHOR

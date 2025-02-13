from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
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
from airlock.types import ROOT_PATH, FileMetadata, UrlPath
from airlock.users import User
from airlock.visibility import RequestFileStatus, filter_visible_items


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
    user: str
    workspace: str | None = None
    request: str | None = None
    path: UrlPath | None = None
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
        path: UrlPath | None = None,
        **kwargs: str,
    ):
        # Note: kwargs go straight to extra
        # set review_turn from request
        kwargs["review_turn"] = str(request.review_turn)
        event = cls(
            type=type,
            user=user.username,
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
    def author(self) -> str:
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

    def get_url(self, relpath: UrlPath = ROOT_PATH) -> str:
        kwargs = {"workspace_name": self.name}
        if relpath != ROOT_PATH:
            kwargs["path"] = str(relpath)
        return reverse("workspace_view", kwargs=kwargs)

    def get_workspace_file_status(self, relpath: UrlPath) -> WorkspaceFileStatus | None:
        # get_file_metadata will throw FileNotFound if we have a bad file path
        metadata = self.get_file_metadata(relpath)

        # check if file has been released once we can do that
        if metadata and metadata.content_hash in self.released_files:
            return WorkspaceFileStatus.RELEASED

        if self.current_request:
            try:
                rfile = self.current_request.get_request_file_from_output_path(relpath)
            except exceptions.FileNotFound:
                return WorkspaceFileStatus.UNRELEASED

            if metadata is None:  # pragma: no cover
                raise exceptions.ManifestFileError(
                    f"no file metadata available for {relpath}"
                )
            if rfile.filetype is RequestFileType.WITHDRAWN:
                return WorkspaceFileStatus.WITHDRAWN
            elif rfile.file_id == metadata.content_hash:
                return WorkspaceFileStatus.UNDER_REVIEW
            else:
                return WorkspaceFileStatus.CONTENT_UPDATED

        return WorkspaceFileStatus.UNRELEASED

    def get_request_file_status(
        self, relpath: UrlPath, user: User
    ) -> RequestFileStatus | None:
        return None  # pragma: nocover

    def get_requests_url(self):
        return reverse(
            "requests_for_workspace",
            kwargs={"workspace_name": self.name},
        )

    def get_contents_url(
        self, relpath: UrlPath, download: bool = False, plaintext: bool = False
    ) -> str:
        url = reverse(
            "workspace_contents",
            kwargs={"workspace_name": self.name, "path": relpath},
        )

        renderer = self.get_renderer(relpath, plaintext=plaintext)
        plaintext_param = "&plaintext=true" if plaintext else ""
        url += f"?cache_id={renderer.cache_id}{plaintext_param}"

        return url

    def get_renderer(
        self, relpath: UrlPath, plaintext: bool = False
    ) -> renderers.Renderer:
        renderer_class = renderers.get_renderer(relpath, plaintext=plaintext)
        return renderer_class.from_file(
            self.abspath(relpath),
            relpath=relpath,
        )

    def get_manifest_for_file(self, relpath: UrlPath):
        try:
            return self.manifest["outputs"][str(relpath)]
        except KeyError:
            raise exceptions.ManifestFileError(
                f"No entry for {relpath} from manifest.json file"
            )

    def get_file_metadata(self, relpath: UrlPath) -> FileMetadata | None:
        """Get file metadata, i.e. size, timestamp, hash"""
        try:
            return FileMetadata.from_manifest(self.get_manifest_for_file(relpath))
        except exceptions.ManifestFileError:
            pass

        # not in manifest, e.g. log file. Check disk
        return FileMetadata.from_path(self.abspath(relpath))

    def abspath(self, relpath):
        """Get absolute path for file

        Protects against traversal, and ensures the path exists."""
        root = self.root()
        path = root / relpath

        # protect against traversal
        path.resolve().relative_to(root)

        # validate path exists
        if not path.exists():
            raise exceptions.FileNotFound(path)

        return path

    def request_filetype(self, relpath: UrlPath) -> None:
        return None


@dataclass(frozen=True)
class CodeRepo:
    workspace: str
    repo: str
    name: str
    commit: str
    directory: Path
    pathlist: list[UrlPath]

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

    def get_url(self, relpath: UrlPath = ROOT_PATH) -> str:
        kwargs = {
            "workspace_name": self.name,
            "commit": self.commit,
        }
        if relpath != ROOT_PATH:
            kwargs["path"] = str(relpath)
        return reverse(
            "code_view",
            kwargs=kwargs,
        )

    def get_file_state(self, relpath: UrlPath) -> WorkspaceFileStatus | None:
        """Get state of path."""
        return None  # pragma: no cover

    def get_contents_url(
        self,
        relpath: UrlPath = ROOT_PATH,
        download: bool = False,
        plaintext: bool = False,
    ) -> str:
        url = reverse(
            "code_contents",
            kwargs={
                "workspace_name": self.workspace,
                "commit": self.commit,
                "path": relpath,
            },
        )

        renderer = self.get_renderer(relpath, plaintext=plaintext)
        plaintext_param = "&plaintext=true" if plaintext else ""
        url += f"?cache_id={renderer.cache_id}{plaintext_param}"

        return url

    def get_renderer(self, relpath: UrlPath, plaintext=False) -> renderers.Renderer:
        # we do not care about valid file types here, so we just get the base renderers

        try:
            contents = read_file_from_repo(self.repo, self.commit, relpath)
        except GitError as exc:
            raise exceptions.FileNotFound(str(exc))

        renderer_class = renderers.get_code_renderer(relpath, plaintext=plaintext)
        # note: we don't actually need an explicit cache_id here, as the commit is
        # already in the url. But we want to add the template version to the
        # cache id, so pass an empty string.
        return renderer_class.from_contents(
            contents=contents,
            relpath=relpath,
            cache_id="",
        )

    def get_file_metadata(self, relpath: UrlPath) -> FileMetadata | None:
        """Get the size of a file"""
        return None  # pragma: no cover

    def request_filetype(self, relpath: UrlPath) -> RequestFileType | None:
        return RequestFileType.CODE

    def get_workspace_file_status(self, relpath: UrlPath) -> WorkspaceFileStatus | None:
        return None

    def get_request_file_status(
        self, relpath: UrlPath, user: User
    ) -> RequestFileStatus | None:
        return None  # pragma: nocover


@dataclass(frozen=True)
class FileReview:
    """
    Represents a review of a file in the context of a release request
    """

    reviewer: str
    status: RequestFileVote
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_dict(cls, attrs):
        return cls(**attrs)


@dataclass(frozen=True)
class RequestFile:
    """
    Represents a single file within a release request
    """

    relpath: UrlPath
    group: str
    file_id: str
    reviews: dict[str, FileReview]
    timestamp: int
    size: int
    job_id: str
    commit: str
    repo: str
    released_by: str | None = None
    row_count: int | None = None
    col_count: int | None = None
    filetype: RequestFileType = RequestFileType.OUTPUT
    released_at: datetime | None = None
    uploaded: bool = False
    upload_attempts: int = 0
    uploaded_at: datetime | None = None

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k != "reviews"},
            reviews={
                value["reviewer"]: FileReview.from_dict(value)
                for value in attrs.get("reviews", ())
            },
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
            v.status for v in self.reviews.values() if v.reviewer in submitted_reviewers
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
        return (
            self.released_at is not None
            and not self.uploaded
            and self.upload_attempts < settings.UPLOAD_MAX_ATTEMPTS
        )

    def upload_failed(self):
        return (
            not self.uploaded and self.upload_attempts >= settings.UPLOAD_MAX_ATTEMPTS
        )


@dataclass(frozen=True)
class FileGroup:
    """
    Represents a group of one or more files within a release request
    """

    name: str
    files: dict[UrlPath, RequestFile]
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
                UrlPath(value["relpath"]): RequestFile.from_dict(value)
                for value in attrs.get("files", ())
            },
            comments=[Comment.from_dict(c) for c in attrs.get("comments", [])],
        )


@dataclass(frozen=True)
class Comment:
    """A user comment on a group"""

    id: str
    comment: str
    author: str
    created_at: datetime
    visibility: Visibility
    review_turn: int

    @classmethod
    def from_dict(cls, attrs):
        # `id` is implemented as an `int` in the current DAL, and as a `str`
        # in the BLL, so we need to add a conversion here (instead of just passing
        # it straight through with the other `attrs`)
        return cls(
            **{k: v for k, v in attrs.items() if k not in ["id"]},
            id=str(attrs["id"]),
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
    author: str
    created_at: datetime
    status: RequestStatus = RequestStatus.PENDING
    filegroups: dict[str, FileGroup] = field(default_factory=dict)
    submitted_reviews: dict[str, str] = field(default_factory=dict)
    turn_reviewers: set[str] = field(default_factory=set)
    review_turn: int = 0

    @classmethod
    def from_dict(cls, attrs) -> Self:
        return cls(
            **{k: v for k, v in attrs.items() if k != "filegroups"},
            filegroups=cls._filegroups_from_dict(attrs.get("filegroups", {})),
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

    def get_url(self, relpath=""):
        return reverse(
            "request_view",
            kwargs={
                "request_id": self.id,
                "path": relpath,
            },
        )

    def get_contents_url(
        self, relpath: UrlPath, download: bool = False, plaintext: bool = False
    ):
        url = reverse(
            "request_contents",
            kwargs={"request_id": self.id, "path": relpath},
        )
        if download:
            url += "?download"
        else:
            # what renderer would render this file?
            renderer = self.get_renderer(relpath, plaintext=plaintext)
            plaintext_param = "&plaintext=true" if plaintext else ""
            url += f"?cache_id={renderer.cache_id}{plaintext_param}"

        return url

    def get_renderer(
        self, relpath: UrlPath, plaintext: bool = False
    ) -> renderers.Renderer:
        request_file = self.get_request_file_from_urlpath(relpath)
        renderer_class = renderers.get_renderer(relpath, plaintext=plaintext)
        return renderer_class.from_file(
            self.abspath(relpath),
            relpath=request_file.relpath,
            cache_id=request_file.file_id,
        )

    def get_file_metadata(self, relpath: UrlPath) -> FileMetadata | None:
        rfile = self.get_request_file_from_urlpath(relpath)
        return FileMetadata(
            rfile.size,
            rfile.timestamp,
            _content_hash=rfile.file_id,
        )

    def get_workspace_file_status(self, relpath: UrlPath) -> WorkspaceFileStatus | None:
        return None

    def get_request_file_status(
        self, relpath: UrlPath, user: User
    ) -> RequestFileStatus | None:
        rfile = self.get_request_file_from_urlpath(relpath)
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

    def get_request_file_from_urlpath(self, relpath: UrlPath | str) -> RequestFile:
        """Get the request file from the url, which includes the group."""
        relpath = UrlPath(relpath)
        group = relpath.parts[0]
        file_relpath = UrlPath(*relpath.parts[1:])

        if not (filegroup := self.filegroups.get(group)):
            raise exceptions.FileNotFound(f"bad group {group} in url {relpath}")

        if not (request_file := filegroup.files.get(file_relpath)):
            raise exceptions.FileNotFound(relpath)

        return request_file

    def get_request_file_from_output_path(self, relpath: UrlPath | str):
        """Get the request file from the output path, which does not include the group"""
        relpath = UrlPath(relpath)
        if relpath in self.all_files_by_name:
            return self.all_files_by_name[relpath]

        raise exceptions.FileNotFound(relpath)

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
        is_author = user.username == self.author

        # author can only ever create public comments
        if is_author:
            return [Visibility.PUBLIC]

        # non-author non-output-checker, also only public
        if not user.output_checker:
            return [Visibility.PUBLIC]

        # all other cases - the output-checker can choose to write public or private comments
        return [Visibility.PRIVATE, Visibility.PUBLIC]

    def abspath(self, relpath):
        """Returns abspath to the file on disk.

        The first part of the relpath is the group, so we parse and validate that first.
        """
        request_file = self.get_request_file_from_urlpath(relpath)
        return self.root() / request_file.file_id

    @cached_property
    def all_files_by_name(self) -> dict[UrlPath, RequestFile]:
        """Return the relpaths for all files on the request, of any filetype"""
        return {
            request_file.relpath: request_file
            for filegroup in self.filegroups.values()
            for request_file in filegroup.files.values()
        }

    def output_files(self) -> dict[UrlPath, RequestFile]:
        """Return the relpaths for output files on the request"""
        return {
            rfile.relpath: rfile
            for rfile in self.all_files_by_name.values()
            if rfile.filetype == RequestFileType.OUTPUT
        }

    def supporting_files_count(self):
        return len(
            [
                1
                for rfile in self.all_files_by_name.values()
                if rfile.filetype == RequestFileType.SUPPORTING
            ]
        )

    def request_filetype(self, urlpath: UrlPath):
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
                relpath = request_file.relpath
                abspath = self.abspath(file_group.name / relpath)
                paths.append((relpath, abspath))
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

    def status_owner(self) -> RequestStatusOwner:
        return permissions.STATUS_OWNERS[self.status]

    def can_be_released(self) -> bool:
        return (
            self.status in [RequestStatus.REVIEWED, RequestStatus.APPROVED]
            and self.all_files_approved()
        )

    def can_be_rereleased(self) -> bool:
        """
        An approved request can be re-released if all of its file are
        either uploaded already, or have have failed to upload after the
        maximum number of attempts
        """
        return self.status == RequestStatus.APPROVED and all(
            rf.uploaded or rf.upload_failed() for rf in self.output_files().values()
        )

    def upload_in_progress(self) -> bool:
        """
        A request is uploading if it has been approved and not all of its
        output files have been uploaded
        """
        return self.status == RequestStatus.APPROVED and any(
            rf.upload_in_progress() for rf in self.output_files().values()
        )

    def is_final(self):
        return self.status_owner() == RequestStatusOwner.SYSTEM

    def is_under_review(self):
        return self.status_owner() == RequestStatusOwner.REVIEWER

    def is_editing(self):
        return self.status_owner() == RequestStatusOwner.AUTHOR

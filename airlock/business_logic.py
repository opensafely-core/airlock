from __future__ import annotations

import hashlib
import json
import logging
import secrets
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path
from typing import Any, Protocol, Self, Sequence, cast

from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject
from django.utils.module_loading import import_string

import old_api
from airlock import exceptions, permissions, renderers
from airlock.enums import (
    AuditEventType,
    NotificationEventType,
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
from airlock.notifications import send_notification_event
from airlock.types import FileMetadata, UrlPath
from airlock.users import User
from airlock.utils import is_valid_file_type


ROOT_PATH = UrlPath()  # empty path

logger = logging.getLogger(__name__)


@dataclass
class RequestFileStatus:
    """The current visible decision and inidividual vote for a specific user."""

    user: User
    decision: RequestFileDecision
    vote: RequestFileVote | None


READONLY_EVENTS = {
    AuditEventType.WORKSPACE_FILE_VIEW,
    AuditEventType.REQUEST_FILE_VIEW,
    AuditEventType.REQUEST_FILE_UNDECIDED,
}


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
    AuditEventType.REQUEST_FILE_ADD: "Added file",
    AuditEventType.REQUEST_FILE_UPDATE: "Updated file",
    AuditEventType.REQUEST_FILE_WITHDRAW: "Withdrew file from group",
    AuditEventType.REQUEST_FILE_APPROVE: "Approved file",
    AuditEventType.REQUEST_FILE_REQUEST_CHANGES: "Changes requested to file",
    AuditEventType.REQUEST_FILE_RESET_REVIEW: "Reset review of file",
    AuditEventType.REQUEST_FILE_UNDECIDED: "File with changes requested moved to undecided",
    AuditEventType.REQUEST_FILE_RELEASE: "File released",
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


class VisibleItem(Protocol):
    @property
    def author(self) -> str:
        raise NotImplementedError()

    @property
    def review_turn(self) -> int:
        raise NotImplementedError()

    @property
    def visibility(self) -> Visibility:
        raise NotImplementedError()


def filter_visible_items(
    items: Sequence[VisibleItem],
    current_turn: int,
    current_phase: ReviewTurnPhase,
    user_can_review: bool,
    user: User,
):
    """Filter a list of items to only include items this user is allowed to view.

    This depends on the current turn, phase, and whether the user is the author
    of said item.
    """
    for item in items:
        # you can always see things you've authored. Doing this first
        # simplifies later logic, and avoids potential bugs with users adding
        # items but then they can not see the item they just added
        if item.author == user.username:
            yield item
            continue

        match item.visibility:
            case Visibility.PUBLIC:
                # can always see public items from previous turns and completed turns
                if (
                    item.review_turn < current_turn
                    or current_phase == ReviewTurnPhase.COMPLETE
                ):
                    yield item
                # can see public items for other users if CONSOLIDATING and can review
                elif current_phase == ReviewTurnPhase.CONSOLIDATING and user_can_review:
                    yield item
            case Visibility.PRIVATE:
                # have to be able to review this request to see *any* private items
                if user_can_review:
                    # can always see private items from previous turns
                    if (
                        item.review_turn < current_turn
                        or current_phase == ReviewTurnPhase.COMPLETE
                    ):
                        yield item
                    # can only see private items from current turn if we are not INDEPENDENT
                    elif current_phase != ReviewTurnPhase.INDEPENDENT:
                        yield item
            case _:  # pragma: nocover
                assert False


class AirlockContainer(Protocol):
    """Structural typing class for a instance of a Workspace or ReleaseRequest

    Provides a uniform interface for accessing information about the paths and files
    contained within this instance.
    """

    def get_id(self) -> str:
        """Get the human name for this container."""

    def get_url(self, relpath: UrlPath = ROOT_PATH) -> str:
        """Get the url for the container object with path"""

    def get_contents_url(
        self, relpath: UrlPath, download: bool = False, plaintext: bool = False
    ) -> str:
        """Get the url for the contents of the container object with path"""

    def request_filetype(self, relpath: UrlPath) -> RequestFileType | None:
        """What kind of file is this, e.g. output, supporting, etc."""

    def get_renderer(
        self, relpath: UrlPath, plaintext: bool = False
    ) -> renderers.Renderer:
        """Create and return the correct renderer for this path."""

    def get_file_metadata(self, relpath: UrlPath) -> FileMetadata | None:
        """Get the file metadata"""

    def get_workspace_file_status(self, relpath: UrlPath) -> WorkspaceFileStatus | None:
        """Get workspace state of file."""

    def get_request_file_status(
        self, relpath: UrlPath, user: User
    ) -> RequestFileStatus | None:
        """Get request status of file."""


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

    def file_has_been_released(self, relpath: UrlPath) -> bool:
        return self.get_workspace_file_status(relpath) == WorkspaceFileStatus.RELEASED

    def file_can_be_updated(self, relpath: UrlPath) -> bool:
        return self.get_workspace_file_status(relpath) in [
            WorkspaceFileStatus.CONTENT_UPDATED,
            WorkspaceFileStatus.WITHDRAWN,
        ]

    def file_can_be_added(self, relpath: UrlPath) -> bool:
        return self.get_workspace_file_status(relpath) in [
            WorkspaceFileStatus.UNRELEASED,
            WorkspaceFileStatus.CONTENT_UPDATED,
            WorkspaceFileStatus.WITHDRAWN,
        ]


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
    def from_workspace(cls, workspace: Workspace, commit: str):
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
        return BusinessLogicLayer.STATUS_OWNERS[self.status]

    def can_be_released(self) -> bool:
        return (
            self.status in [RequestStatus.REVIEWED, RequestStatus.APPROVED]
            and self.all_files_approved()
        )

    def is_final(self):
        return (
            BusinessLogicLayer.STATUS_OWNERS[self.status] == RequestStatusOwner.SYSTEM
        )

    def is_under_review(self):
        return (
            BusinessLogicLayer.STATUS_OWNERS[self.status] == RequestStatusOwner.REVIEWER
        )

    def is_editing(self):
        return (
            BusinessLogicLayer.STATUS_OWNERS[self.status] == RequestStatusOwner.AUTHOR
        )


def store_file(release_request: ReleaseRequest, abspath: Path) -> str:
    # Make a "staging" copy of the file under a temporary name so we know it can't be
    # modified underneath us
    tmp_name = f"{datetime.now():%Y%m%d-%H%M%S}_{secrets.token_hex(8)}.tmp"
    tmp_path = release_request.root() / tmp_name
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(abspath, tmp_path)
    # Rename the staging file to the hash of its contents
    with tmp_path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    tmp_path.rename(release_request.root() / digest)
    return digest


class DataAccessLayerProtocol(Protocol):
    """
    Structural type class for the Data Access Layer

    Implementations aren't obliged to subclass this as long as they implement the
    specified methods, though it may be clearer to do so.
    """

    def get_release_request(self, request_id: str):
        raise NotImplementedError()

    def create_release_request(
        self,
        workspace: str,
        author: str,
        status: RequestStatus,
        audit: AuditEvent,
        id: str | None = None,  # noqa: A002
    ):
        raise NotImplementedError()

    def get_active_requests_for_workspace_by_user(self, workspace: str, username: str):
        raise NotImplementedError()

    def get_requests_for_workspace(self, workspace: str):
        raise NotImplementedError()

    def get_released_files_for_workspace(self, workspace: str):
        raise NotImplementedError()

    def get_requests_authored_by_user(self, username: str):
        raise NotImplementedError()

    def get_requests_by_status(self, *states: RequestStatus):
        raise NotImplementedError()

    def set_status(self, request_id: str, status: RequestStatus, audit: AuditEvent):
        raise NotImplementedError()

    def record_review(self, request_id: str, reviewer: str):
        raise NotImplementedError()

    def start_new_turn(self, request_id: str):
        raise NotImplementedError()

    def add_file_to_request(
        self,
        request_id: str,
        relpath: UrlPath,
        file_id: str,
        group_name: str,
        filetype: RequestFileType,
        timestamp: int,
        size: int,
        commit: str,
        repo: str,
        job_id: str,
        row_count: int | None,
        col_count: int | None,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def delete_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def release_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def withdraw_file_from_request(
        self,
        request_id: str,
        relpath: UrlPath,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def approve_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def request_changes_to_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def reset_review_file(
        self, request_id: str, relpath: UrlPath, username: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def mark_file_undecided(
        self, request_id: str, relpath: UrlPath, reviewer: str, audit: AuditEvent
    ):
        raise NotImplementedError()

    def audit_event(self, audit: AuditEvent):
        raise NotImplementedError()

    def get_audit_log(
        self,
        user: str | None = None,
        workspace: str | None = None,
        request: str | None = None,
        group: str | None = None,
        exclude: set[AuditEventType] | None = None,
        size: int | None = None,
    ) -> list[AuditEvent]:
        raise NotImplementedError()

    def group_edit(
        self,
        request_id: str,
        group: str,
        context: str,
        controls: str,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def group_comment_create(
        self,
        request_id: str,
        group: str,
        comment: str,
        visibility: Visibility,
        review_turn: int,
        username: str,
        audit: AuditEvent,
    ):
        raise NotImplementedError()

    def group_comment_delete(
        self,
        request_id: str,
        group: str,
        comment_id: str,
        username: str,
        audit: AuditEvent,
    ):
        raise NotImplementedError()


class BusinessLogicLayer:
    """
    The mechanism via which the rest of the codebase should read and write application
    state. Interacts with a Data Access Layer purely by exchanging simple values
    (dictionaries, strings etc).
    """

    def __init__(self, data_access_layer: DataAccessLayerProtocol):
        self._dal = data_access_layer

    def get_workspace(self, name: str, user: User) -> Workspace:
        """Get a workspace object."""

        permissions.check_user_can_view_workspace(user, name)

        # this is a bit awkward. If the user is an output checker, they may not
        # have the workspace metadata in their User instance, so we provide an
        # empty metadata instance.
        # Currently, the only place this metadata is used is in the workspace
        # index, to group by project, so its mostly fine that its not here.
        #
        # Metadata also contains information about whether the workspace is
        # archived, and whether the project is ongoing (note that a project
        # could be completed/closed and a workspace not archived, so we need
        # to check for both of these states);
        # The metadata is extracted as an attribute on the workspace. It is
        # used in the workspace index, to show/group
        # workspaces by project and project ongoing status, and within that,
        # by archived status. It is also used to prevent release
        # requests being created for archived workspaces or for not-ongoing projects.
        #
        # It will be None for output checkers who don't have explicit access to
        # the workspace; this is OK as they also won't be able to create requests
        # for the workspace, and they only have access to browse the files.
        metadata = user.workspaces.get(name, {})

        return Workspace.from_directory(
            name,
            metadata=metadata,
            current_request=self.get_current_request(name, user),
            released_files=self.get_released_files_for_workspace(name),
        )

    def get_workspaces_for_user(self, user: User) -> list[Workspace]:
        """Get all the local workspace directories that a user has permission for."""

        workspaces = []
        for workspace_name in user.workspaces:
            try:
                workspace = self.get_workspace(workspace_name, user)
            except exceptions.WorkspaceNotFound:
                continue

            workspaces.append(workspace)

        return workspaces

    def _create_release_request(
        self,
        workspace: str,
        author: User,
        status: RequestStatus = RequestStatus.PENDING,
        id: str | None = None,  # noqa: A002
    ) -> ReleaseRequest:
        """Factory function to create a release_request.

        Is private because it is meant to be used directly by our test factories
        to set up state - it is not part of the public API.
        """
        # id is used to set specific ids in tests. We should probably not allow this.
        audit = AuditEvent(
            type=self.STATUS_AUDIT_EVENT[status],
            user=author.username,
            workspace=workspace,
            # DAL will set request id once its created
        )
        return ReleaseRequest.from_dict(
            self._dal.create_release_request(
                workspace=workspace,
                author=author.username,
                status=status,
                audit=audit,
                id=id,
            )
        )

    def get_release_request(self, request_id: str, user: User) -> ReleaseRequest:
        """Get a ReleaseRequest object for an id."""

        release_request = ReleaseRequest.from_dict(
            self._dal.get_release_request(request_id)
        )

        permissions.check_user_can_view_workspace(user, release_request.workspace)
        return release_request

    def get_current_request(self, workspace: str, user: User) -> ReleaseRequest | None:
        """Get the current request for a workspace/user."""
        permissions.check_user_can_view_workspace(user, workspace)

        active_requests = self._dal.get_active_requests_for_workspace_by_user(
            workspace=workspace,
            username=user.username,
        )

        n = len(active_requests)
        if n == 0:
            return None
        elif n == 1:
            return ReleaseRequest.from_dict(active_requests[0])
        else:
            raise Exception(
                f"Multiple active release requests for user {user.username} in "
                f"workspace {workspace}"
            )

    def get_or_create_current_request(
        self, workspace: str, user: User
    ) -> ReleaseRequest:
        """
        Get the current request for a workspace/user, or create a new one if there is
        none.
        """
        # get_current_request will raise exception if user has no permission
        # and is not an output-cheker
        request = self.get_current_request(workspace, user)

        # requests for output-checkers, and for archived workspaces and inactive
        # projects are still viewable, check if user has permission to create one
        permissions.check_user_can_action_request_for_workspace(user, workspace)

        if request is not None:
            return request
        return self._create_release_request(workspace, user)

    def get_requests_for_workspace(
        self, workspace: str, user: User
    ) -> list[ReleaseRequest]:
        """Get all release requests in workspaces a user has access to."""
        permissions.check_user_can_view_workspace(user, workspace)

        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_requests_for_workspace(workspace=workspace)
        ]

    def get_released_files_for_workspace(self, workspace: str):
        return self._dal.get_released_files_for_workspace(workspace=workspace)

    def get_requests_authored_by_user(self, user: User) -> list[ReleaseRequest]:
        """Get all current requests authored by user."""
        return [
            ReleaseRequest.from_dict(attrs)
            for attrs in self._dal.get_requests_authored_by_user(username=user.username)
        ]

    def _get_reviewable_requests_by_status(self, user: User, *statuses: RequestStatus):
        permissions.check_user_can_review(user)
        for attrs in self._dal.get_requests_by_status(*statuses):
            release_request = ReleaseRequest.from_dict(attrs)
            if permissions.user_can_review_request(user, release_request):
                yield release_request

    def get_outstanding_requests_for_review(self, user: User):
        """Get all request that need review."""
        return list(
            self._get_reviewable_requests_by_status(
                user,
                RequestStatus.SUBMITTED,
                RequestStatus.PARTIALLY_REVIEWED,
                RequestStatus.REVIEWED,
            )
        )

    def get_returned_requests(self, user: User):
        """Get all requests that have been returned."""
        return list(
            self._get_reviewable_requests_by_status(user, RequestStatus.RETURNED)
        )

    def get_approved_requests(self, user: User):
        """Get all requests that have been approved but not yet released."""
        return list(
            self._get_reviewable_requests_by_status(user, RequestStatus.APPROVED)
        )

    VALID_STATE_TRANSITIONS = {
        RequestStatus.PENDING: [
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
        ],
        RequestStatus.SUBMITTED: [
            RequestStatus.PARTIALLY_REVIEWED,
        ],
        RequestStatus.PARTIALLY_REVIEWED: [
            RequestStatus.REVIEWED,
        ],
        RequestStatus.REVIEWED: [
            RequestStatus.APPROVED,
            RequestStatus.REJECTED,
            RequestStatus.RETURNED,
        ],
        RequestStatus.RETURNED: [
            RequestStatus.SUBMITTED,
            RequestStatus.WITHDRAWN,
        ],
        RequestStatus.APPROVED: [
            RequestStatus.RELEASED,
        ],
    }

    # The following lists should a) include every status and b) be disjoint
    # This is validated in tests
    #
    STATUS_OWNERS = {
        # states where only the author can edit this request
        RequestStatus.PENDING: RequestStatusOwner.AUTHOR,
        RequestStatus.RETURNED: RequestStatusOwner.AUTHOR,
        # states where only an output-checker can edit this request
        RequestStatus.SUBMITTED: RequestStatusOwner.REVIEWER,
        RequestStatus.PARTIALLY_REVIEWED: RequestStatusOwner.REVIEWER,
        RequestStatus.REVIEWED: RequestStatusOwner.REVIEWER,
        # states where no user can edit
        RequestStatus.WITHDRAWN: RequestStatusOwner.SYSTEM,
        RequestStatus.APPROVED: RequestStatusOwner.SYSTEM,
        RequestStatus.REJECTED: RequestStatusOwner.SYSTEM,
        RequestStatus.RELEASED: RequestStatusOwner.SYSTEM,
    }

    STATUS_AUDIT_EVENT = {
        RequestStatus.PENDING: AuditEventType.REQUEST_CREATE,
        RequestStatus.SUBMITTED: AuditEventType.REQUEST_SUBMIT,
        RequestStatus.PARTIALLY_REVIEWED: AuditEventType.REQUEST_REVIEW,
        RequestStatus.REVIEWED: AuditEventType.REQUEST_REVIEW,
        RequestStatus.APPROVED: AuditEventType.REQUEST_APPROVE,
        RequestStatus.REJECTED: AuditEventType.REQUEST_REJECT,
        RequestStatus.RETURNED: AuditEventType.REQUEST_RETURN,
        RequestStatus.RELEASED: AuditEventType.REQUEST_RELEASE,
        RequestStatus.WITHDRAWN: AuditEventType.REQUEST_WITHDRAW,
    }

    STATUS_EVENT_NOTIFICATION = {
        RequestStatus.SUBMITTED: NotificationEventType.REQUEST_SUBMITTED,
        RequestStatus.PARTIALLY_REVIEWED: NotificationEventType.REQUEST_PARTIALLY_REVIEWED,
        RequestStatus.REVIEWED: NotificationEventType.REQUEST_REVIEWED,
        RequestStatus.APPROVED: NotificationEventType.REQUEST_APPROVED,
        RequestStatus.REJECTED: NotificationEventType.REQUEST_REJECTED,
        RequestStatus.RETURNED: NotificationEventType.REQUEST_RETURNED,
        RequestStatus.RELEASED: NotificationEventType.REQUEST_RELEASED,
        RequestStatus.WITHDRAWN: NotificationEventType.REQUEST_WITHDRAWN,
    }

    def check_status(
        self, release_request: ReleaseRequest, to_status: RequestStatus, user: User
    ):
        """Check that a given status transtion is valid for this request and this user.

        This can be used to look-before-you-leap before mutating state when
        there's not transaction protection.
        """
        # validate state logic
        valid_transitions = self.VALID_STATE_TRANSITIONS.get(release_request.status, [])

        if to_status not in valid_transitions:
            raise exceptions.InvalidStateTransition(
                f"cannot change status from {release_request.status.name} to {to_status.name}"
            )

        if to_status == RequestStatus.SUBMITTED:
            for filegroup in release_request.filegroups:
                if (
                    release_request.filegroups[filegroup].context == ""
                    or release_request.filegroups[filegroup].controls == ""
                ):
                    raise exceptions.IncompleteContextOrControls(
                        f"Incomplete context and/or controls for filegroup '{filegroup}'"
                    )

        # check permissions
        owner = release_request.status_owner()
        # author transitions
        if (
            owner == RequestStatusOwner.AUTHOR
            and user.username != release_request.author
        ):
            raise exceptions.RequestPermissionDenied(
                f"only the request author {release_request.author} can set status from {release_request.status} to {to_status.name}"
            )
        # reviewer transitions
        elif owner == RequestStatusOwner.REVIEWER or (
            # APPROVED and REJECTED cannot be edited by any user, but can be
            # moved to valid state transitions by a reviewer
            owner == RequestStatusOwner.SYSTEM
            and release_request.status
            in [RequestStatus.APPROVED, RequestStatus.REJECTED]
        ):
            if not user.output_checker:
                raise exceptions.RequestPermissionDenied(
                    f"only an output checker can set status to {to_status.name}"
                )

            if user.username == release_request.author:
                raise exceptions.RequestPermissionDenied(
                    f"Can not set your own request to {to_status.name}"
                )

            if (
                to_status == RequestStatus.APPROVED
                and not release_request.all_files_approved()
            ):
                raise exceptions.RequestPermissionDenied(
                    f"Cannot set status to {to_status.name}; request has unapproved files."
                )

            if (
                to_status == RequestStatus.APPROVED
                and not release_request.output_files()
            ):
                raise exceptions.RequestPermissionDenied(
                    f"Cannot set status to {to_status.name}; request contains no output files."
                )

    def set_status(
        self, release_request: ReleaseRequest, to_status: RequestStatus, user: User
    ):
        """Set the status of the request.

        This will validate the transition, and then mutate the request object.

        As calling set_status will mutate the passed ReleaseRequest, in cases
        where we may want to call to external services (e.g. job-server) to
        mutate external state, and these calls might fail, we provide
        a look-before-you-leap API. That is, when changing status and related
        state, an implementer should call `check_status(...)` first to validate
        the desired state transition and permissions are valid, then mutate
        their own state, and then call `set_status(...)` if successful to
        mutate the passed ReleaseRequest object.
        """

        # validate first
        self.check_status(release_request, to_status, user)
        audit = AuditEvent.from_request(
            release_request,
            type=self.STATUS_AUDIT_EVENT[to_status],
            user=user,
        )
        self._dal.set_status(release_request.id, to_status, audit)
        if (release_request.status, to_status) == (
            RequestStatus.RETURNED,
            RequestStatus.SUBMITTED,
        ):
            notification_event = NotificationEventType.REQUEST_RESUBMITTED
        else:
            notification_event = self.STATUS_EVENT_NOTIFICATION[to_status]

        release_request.status = to_status
        self.send_notification(release_request, notification_event, user)

    def validate_file_types(self, file_paths):
        """
        Validate file types before releasing.

        This is a final safety check before files are released. It
        should never be hit in production, as file types are checked
        before display in the workspace view and on adding to a
        request.
        """
        for relpath, _ in file_paths:
            if not is_valid_file_type(Path(relpath)):
                raise exceptions.RequestPermissionDenied(
                    f"Invalid file type ({relpath}) found in request"
                )

    def add_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str = "default",
        filetype: RequestFileType = RequestFileType.OUTPUT,
    ) -> ReleaseRequest:
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        permissions.check_user_can_add_file_to_request(
            user, release_request, workspace, relpath
        )

        src = workspace.abspath(relpath)
        file_id = store_file(release_request, src)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_ADD,
            user=user,
            path=relpath,
            group=group_name,
            filetype=filetype.name,
        )

        manifest = workspace.get_manifest_for_file(relpath)
        assert (
            manifest["content_hash"] == file_id
        ), "File hash does not match manifest.json"

        filegroup_data = self._dal.add_file_to_request(
            request_id=release_request.id,
            group_name=group_name,
            relpath=relpath,
            file_id=file_id,
            filetype=filetype,
            timestamp=manifest["timestamp"],
            commit=manifest["commit"],
            repo=manifest["repo"],
            size=manifest["size"],
            job_id=manifest["job_id"],
            row_count=manifest["row_count"],
            col_count=manifest["col_count"],
            audit=audit,
        )
        release_request.set_filegroups_from_dict(filegroup_data)

        return release_request

    def update_file_in_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
    ) -> ReleaseRequest:
        request_file = release_request.get_request_file_from_output_path(relpath)
        return self.replace_file_in_request(
            release_request, relpath, user, request_file.group, request_file.filetype
        )

    def add_withdrawn_file_to_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str = "default",
        filetype: RequestFileType = RequestFileType.OUTPUT,
    ) -> ReleaseRequest:
        return self.replace_file_in_request(
            release_request, relpath, user, group_name, filetype
        )

    def replace_file_in_request(
        self,
        release_request: ReleaseRequest,
        relpath: UrlPath,
        user: User,
        group_name: str,
        filetype: RequestFileType,
    ) -> ReleaseRequest:
        relpath = UrlPath(relpath)
        workspace = self.get_workspace(release_request.workspace, user)
        permissions.check_user_can_update_file_on_request(
            user, release_request, workspace, relpath
        )

        src = workspace.abspath(relpath)
        file_id = store_file(release_request, src)

        manifest = workspace.get_manifest_for_file(relpath)
        assert (
            manifest["content_hash"] == file_id
        ), "File hash does not match manifest.json"

        request_file = release_request.get_request_file_from_output_path(relpath)
        old_group = request_file.group
        old_filetype = request_file.filetype

        for reviewer_username in request_file.reviews:
            audit = AuditEvent.from_request(
                request=release_request,
                type=AuditEventType.REQUEST_FILE_RESET_REVIEW,
                user=user,
                path=relpath,
                group=old_group,
                filetype=old_filetype.name,
                reviewer=reviewer_username,
            )
            self._dal.reset_review_file(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
                username=reviewer_username,
            )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_WITHDRAW,
            user=user,
            path=relpath,
            group=old_group,
            filetype=old_filetype.name,
        )
        filegroup_data = self._dal.delete_file_from_request(
            request_id=release_request.id,
            relpath=relpath,
            audit=audit,
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_UPDATE,
            user=user,
            path=relpath,
            group=group_name,
            filetype=filetype.name,
        )
        filegroup_data = self._dal.add_file_to_request(
            request_id=release_request.id,
            group_name=group_name,
            relpath=relpath,
            file_id=file_id,
            filetype=filetype,
            timestamp=manifest["timestamp"],
            commit=manifest["commit"],
            repo=manifest["repo"],
            size=manifest["size"],
            job_id=manifest["job_id"],
            row_count=manifest["row_count"],
            col_count=manifest["col_count"],
            audit=audit,
        )
        release_request.set_filegroups_from_dict(filegroup_data)

        return release_request

    def withdraw_file_from_request(
        self,
        release_request: ReleaseRequest,
        group_path: UrlPath,
        user: User,
    ):
        relpath = UrlPath(*group_path.parts[1:])
        permissions.check_user_can_withdraw_file_from_request(
            user, release_request, relpath
        )

        group_name = group_path.parts[0]
        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_WITHDRAW,
            user=user,
            path=relpath,
            group=group_name,
        )

        if release_request.status == RequestStatus.PENDING:
            # the user has not yet submitted the request, so just remove the file
            filegroup_data = self._dal.delete_file_from_request(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
            )
        elif release_request.status == RequestStatus.RETURNED:
            # the request has been returned, set the file type to WITHDRAWN
            filegroup_data = self._dal.withdraw_file_from_request(
                request_id=release_request.id,
                relpath=relpath,
                audit=audit,
            )
        else:
            assert False, f"Invalid state {release_request.status.name}, cannot withdraw file {relpath} from request {release_request.id}"

        release_request.set_filegroups_from_dict(filegroup_data)
        return release_request

    def release_files(self, release_request: ReleaseRequest, user: User, upload=True):
        """Release all files from a release_request to job-server.

        This currently uses the old api, and is shared amongst provider
        implementations, but that will likely change in future.

        Passing upload=False will just perform the DAL actions, but not
        actually attempt to upload the files. This defaults to True, and is
        primarily used for testing.
        """

        # we check this is valid status transition *before* releasing the files
        self.check_status(release_request, RequestStatus.RELEASED, user)

        file_paths = release_request.get_output_file_paths()
        self.validate_file_types(file_paths)

        filelist = old_api.create_filelist(file_paths, release_request)

        if upload:
            jobserver_release_id = old_api.create_release(
                release_request.workspace,
                release_request.id,
                filelist.json(),
                user.username,
            )
        else:
            jobserver_release_id = None

        for relpath, abspath in file_paths:
            audit = AuditEvent.from_request(
                request=release_request,
                type=AuditEventType.REQUEST_FILE_RELEASE,
                user=user,
                path=relpath,
            )
            self._dal.release_file(release_request.id, relpath, user.username, audit)

            if upload:
                old_api.upload_file(
                    jobserver_release_id, relpath, abspath, user.username
                )

        self.set_status(release_request, RequestStatus.RELEASED, user)

    def submit_request(self, request: ReleaseRequest, user: User):
        """
        Change status to SUBMITTED. If the request is currently in
        RETURNED status, mark any changes-requested reviews as undecided.
        """
        self.check_status(request, RequestStatus.SUBMITTED, user)

        # reset any previous review data
        if request.status == RequestStatus.RETURNED:
            # any unapproved files that have not been updated are set to UNDECIDED
            for rfile in request.output_files().values():
                for review in rfile.changes_requested_reviews():
                    self.mark_file_undecided(request, review, rfile.relpath, user)

        self.set_status(request, RequestStatus.SUBMITTED, user)
        self._dal.start_new_turn(request.id)

    def approve_file(
        self,
        release_request: ReleaseRequest,
        request_file: RequestFile,
        user: User,
    ):
        """ "Approve a file"""
        permissions.check_user_can_review_file(
            user, release_request, request_file.relpath
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_APPROVE,
            user=user,
            path=request_file.relpath,
            group=request_file.group,
        )

        self._dal.approve_file(
            release_request.id, request_file.relpath, user.username, audit
        )

    def request_changes_to_file(
        self,
        release_request: ReleaseRequest,
        request_file: RequestFile,
        user: User,
    ):
        """Request changes to a file"""
        permissions.check_user_can_review_file(
            user, release_request, request_file.relpath
        )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_REQUEST_CHANGES,
            user=user,
            path=request_file.relpath,
            group=request_file.group,
        )

        self._dal.request_changes_to_file(
            release_request.id, request_file.relpath, user.username, audit
        )

    def reset_review_file(
        self, release_request: ReleaseRequest, relpath: UrlPath, user: User
    ):
        """Reset a file to have no review from this user"""

        permissions.check_user_can_reset_file_review(user, release_request, relpath)

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_RESET_REVIEW,
            user=user,
            path=relpath,
        )

        self._dal.reset_review_file(release_request.id, relpath, user.username, audit)

    def review_request(self, release_request: ReleaseRequest, user: User):
        """
        Submit a review

        Marking the request as either PARTIALLY_REVIEWED or REVIEWED, depending on whether this is the first or second review.
        """
        permissions.check_user_can_submit_review(user, release_request)

        self._dal.record_review(release_request.id, user.username)

        release_request = self.get_release_request(release_request.id, user)
        n_reviews = release_request.submitted_reviews_count()

        # this method is called twice, by different users. It advances the
        # state differently depending on whether its the 1st or 2nd review to
        # be submitted.
        try:
            if n_reviews == 1:
                self.set_status(release_request, RequestStatus.PARTIALLY_REVIEWED, user)
            elif n_reviews == 2:
                self.set_status(release_request, RequestStatus.REVIEWED, user)
        except exceptions.InvalidStateTransition:
            # There is a potential race condition where two reviewers hit the Submit Review
            # button at the same time, and both attempt to transition from SUBMITTED to
            # PARTIALLY_REVIEWED, or from PARTIALLY_REVIEWED to REVIEWED
            # Assuming that the request status is now either PARTIALLY_REVIEWED or REVIEWED,
            # we can verify the status by refreshing the request, getting the number of reviews
            # again, and advance it if necessary
            if release_request.status not in [
                RequestStatus.PARTIALLY_REVIEWED,
                RequestStatus.REVIEWED,
            ]:
                raise
            release_request = self.get_release_request(release_request.id, user)
            if (
                release_request.submitted_reviews_count() > 1
                and release_request.status == RequestStatus.PARTIALLY_REVIEWED
            ):
                self.set_status(release_request, RequestStatus.REVIEWED, user)

    def return_request(self, release_request: ReleaseRequest, user: User):
        self.set_status(release_request, RequestStatus.RETURNED, user)
        self._dal.start_new_turn(release_request.id)

    def mark_file_undecided(
        self,
        release_request: ReleaseRequest,
        review: FileReview,
        relpath: UrlPath,
        user: User,
    ):
        """Change an existing changes-requested file in a returned request to undecided before re-submitting"""
        if release_request.status != RequestStatus.RETURNED:
            raise exceptions.RequestReviewDenied(
                f"cannot change file review to {RequestFileVote.UNDECIDED.name} from request in state {release_request.status.name}"
            )

        if review.status != RequestFileVote.CHANGES_REQUESTED:
            raise exceptions.RequestReviewDenied(
                f"cannot change file review from {review.status.name} to {RequestFileVote.UNDECIDED.name} from request in state {release_request.status.name}"
            )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_FILE_UNDECIDED,
            user=user,
            reviewer=review.reviewer,
            path=relpath,
        )

        self._dal.mark_file_undecided(
            release_request.id, relpath, review.reviewer, audit
        )

    def group_edit(
        self,
        release_request: ReleaseRequest,
        group: str,
        context: str,
        controls: str,
        user: User,
    ):
        if release_request.author != user.username:
            raise exceptions.RequestPermissionDenied(
                "Only request author can edit the request"
            )

        if release_request.is_final():
            raise exceptions.RequestPermissionDenied(
                "This request is no longer editable"
            )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_EDIT,
            user=user,
            group=group,
            context=context,
            controls=controls,
        )

        self._dal.group_edit(release_request.id, group, context, controls, audit)

    def group_comment_create(
        self,
        release_request: ReleaseRequest,
        group: str,
        comment: str,
        visibility: Visibility,
        user: User,
    ):
        if not user.output_checker and release_request.workspace not in user.workspaces:
            raise exceptions.RequestPermissionDenied(
                f"User {user.username} does not have permission to comment"
            )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_COMMENT,
            user=user,
            group=group,
            comment=comment,
            review_turn=str(release_request.review_turn),
            visibility=visibility.name,
        )

        self._dal.group_comment_create(
            release_request.id,
            group,
            comment,
            visibility,
            release_request.review_turn,
            user.username,
            audit,
        )

    def group_comment_delete(
        self, release_request: ReleaseRequest, group: str, comment_id: str, user: User
    ):
        filegroup = release_request.filegroups.get(group)
        if not filegroup:
            raise exceptions.FileNotFound(f"Filegroup {group} not found")

        comment = next(
            (c for c in filegroup.comments if c.id == comment_id),
            None,
        )
        if not comment:
            raise exceptions.FileNotFound(f"Comment {comment_id} not found")

        if not user.username == comment.author:
            raise exceptions.RequestPermissionDenied(
                f"User {user.username} is not the author of this comment, so cannot delete"
            )

        audit = AuditEvent.from_request(
            request=release_request,
            type=AuditEventType.REQUEST_COMMENT_DELETE,
            user=user,
            group=group,
            comment=comment.comment,
        )

        self._dal.group_comment_delete(
            release_request.id, group, comment_id, user.username, audit
        )

    def get_request_audit_log(
        self,
        user: User,
        request: ReleaseRequest,
        group: str | None = None,
        exclude_readonly: bool = False,
        size: int | None = None,
    ) -> list[AuditEvent]:
        """Fetches the audit log for this request, filtering for what the user can see."""

        audits = self._dal.get_audit_log(
            request=request.id,
            group=group,
            exclude=READONLY_EVENTS if exclude_readonly else set(),
            size=size,
        )

        return list(
            filter_visible_items(
                audits,
                request.review_turn,
                request.get_turn_phase(),
                permissions.user_can_review_request(user, request),
                user,
            )
        )

    def audit_workspace_file_access(
        self, workspace: Workspace, path: UrlPath, user: User
    ):
        audit = AuditEvent(
            type=AuditEventType.WORKSPACE_FILE_VIEW,
            user=user.username,
            workspace=workspace.name,
            path=path,
        )
        self._dal.audit_event(audit)

    def audit_request_file_access(
        self, request: ReleaseRequest, path: UrlPath, user: User
    ):
        audit = AuditEvent.from_request(
            request,
            AuditEventType.REQUEST_FILE_VIEW,
            user=user,
            path=path,
            group=path.parts[0],
        )
        self._dal.audit_event(audit)

    def audit_request_file_download(
        self, request: ReleaseRequest, path: UrlPath, user: User
    ):
        audit = AuditEvent.from_request(
            request,
            AuditEventType.REQUEST_FILE_DOWNLOAD,
            user=user,
            path=path,
            group=path.parts[0],
        )
        self._dal.audit_event(audit)

    def validate_update_dicts(self, updates):
        if updates is None:
            return
        allowed_keys = {"update", "group", "user"}
        for update_dict in updates:
            assert (
                "update" in update_dict
            ), "Notification updates must include an `update` key"
            extra_keys = set(update_dict.keys()) - allowed_keys
            assert (
                not extra_keys
            ), f"Unexpected keys in notification update ({extra_keys})"

    def send_notification(
        self,
        request: ReleaseRequest,
        event_type: NotificationEventType,
        user: User,
        updates: list[dict[str, str]] | None = None,
    ):
        """
        Send a notification about an event.
        Events can send a optional list of dicts to include in the
        notification. These must include at least one `update` key
        with a description of the update, and optional `user` and
        `group` keys.
        """
        event_data = {
            "event_type": event_type.value,
            "workspace": request.workspace,
            "request": request.id,
            "request_author": request.author,
            "user": user.username,
            "updates": updates,
        }
        self.validate_update_dicts(updates)
        if settings.AIRLOCK_OUTPUT_CHECKING_ORG:
            event_data.update(
                {
                    "org": settings.AIRLOCK_OUTPUT_CHECKING_ORG,
                    "repo": settings.AIRLOCK_OUTPUT_CHECKING_REPO,
                }
            )

        data = send_notification_event(json.dumps(event_data), user.username)
        logger.info(
            "Notification sent: %s %s - %s",
            request.id,
            event_type.value,
            data["status"],
        )
        if data["status"] == "error":
            logger.error(data["message"])


def _get_configured_bll():
    DataAccessLayer = import_string(settings.AIRLOCK_DATA_ACCESS_LAYER)
    return BusinessLogicLayer(DataAccessLayer())


# We follow the Django pattern of using a lazy object which configures itself on first
# access so as to avoid reading `settings` during import. The `cast` here is a runtime
# no-op, but indicates to the type-checker that this should be treated as an instance of
# BusinessLogicLayer not SimpleLazyObject.
bll = cast(BusinessLogicLayer, SimpleLazyObject(_get_configured_bll))

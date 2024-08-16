from enum import Enum


class WorkspaceFileStatus(Enum):
    """Possible states of a workspace file."""

    # Workspace path states
    UNRELEASED = "UNRELEASED"
    UNDER_REVIEW = "UNDER_REVIEW"
    RELEASED = "RELEASED"
    CONTENT_UPDATED = "UPDATED"
    WITHDRAWN = "WITHDRAWN"

    def formatted(self):
        return self.value.title().replace("_", " ")


class RequestStatus(Enum):
    """Status for release Requests"""

    # author set statuses
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    WITHDRAWN = "WITHDRAWN"
    # output checker set statuses
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PARTIALLY_REVIEWED = "PARTIALLY_REVIEWED"
    REVIEWED = "REVIEWED"
    RETURNED = "RETURNED"
    RELEASED = "RELEASED"

    def description(self):
        if self == RequestStatus.PARTIALLY_REVIEWED:
            return "ONE REVIEW SUBMITTED"
        if self == RequestStatus.REVIEWED:
            return "ALL REVIEWS SUBMITTED"
        return self.name


class RequestFileVote(Enum):
    """An individual output checker's vote on a specific file."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    UNDECIDED = "UNDECIDED"  # set on CHANGES_REQUESTED files by Airlock when a request is re-submitted

    def description(self):
        return self.name.replace("_", " ").title()


class RequestStatusOwner(Enum):
    """Who can write to a request in this state."""

    AUTHOR = "AUTHOR"
    REVIEWER = "REVIEWER"
    SYSTEM = "SYSTEM"


class RequestFileType(Enum):
    OUTPUT = "output"
    SUPPORTING = "supporting"
    WITHDRAWN = "withdrawn"
    CODE = "code"


class RequestFileDecision(Enum):
    """The current state of all user reviews on this file."""

    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    APPROVED = "APPROVED"
    CONFLICTED = "CONFLICTED"
    INCOMPLETE = "INCOMPLETE"

    def description(self):  # pragma: nocover
        return self.name.replace("_", " ").title()


class Visibility(Enum):
    """The visibility of comments."""

    # only visible to output-checkers
    PRIVATE = "PRIVATE"
    # visible to all
    PUBLIC = "PUBLIC"

    @classmethod
    def choices(cls):
        return {
            Visibility.PRIVATE: "Only visible to output-checkers",
            Visibility.PUBLIC: "Visible to all users",
        }

    # These will be for tooltips once those are working inside of pills
    def description(self):  # pragma: no cover
        return self.choices()[self]

    def blinded_description(self):  # pragma: no cover
        return "Only visible to you until two reviews have been completed"


class ReviewTurnPhase(Enum):
    """What phase is the request in."""

    # author's phase
    AUTHOR = "AUTHOR"
    # can only see your own votes/comments
    INDEPENDENT = "INDEPENDENT"
    # output-checkers can see all votes/comments
    CONSOLIDATING = "CONSOLIDATING"
    # can see everything
    COMPLETE = "COMPLETE"


class AuditEventType(Enum):
    """Audit log events.

    Note that the string values are stored in the local_db database via
    AuditEvent.type.  Any changes to values will require a database migration.
    See eg #562.
    """

    # file access
    WORKSPACE_FILE_VIEW = "WORKSPACE_FILE_VIEW"
    REQUEST_FILE_VIEW = "REQUEST_FILE_VIEW"
    REQUEST_FILE_DOWNLOAD = "REQUEST_FILE_DOWNLOAD"

    # request status
    REQUEST_CREATE = "REQUEST_CREATE"
    REQUEST_SUBMIT = "REQUEST_SUBMIT"
    REQUEST_WITHDRAW = "REQUEST_WITHDRAW"
    REQUEST_REVIEW = "REQUEST_REVIEW"
    REQUEST_APPROVE = "REQUEST_APPROVE"
    REQUEST_REJECT = "REQUEST_REJECT"
    REQUEST_RETURN = "REQUEST_RETURN"
    REQUEST_RELEASE = "REQUEST_RELEASE"

    # request edits
    REQUEST_EDIT = "REQUEST_EDIT"
    REQUEST_COMMENT = "REQUEST_COMMENT"
    REQUEST_COMMENT_DELETE = "REQUEST_COMMENT_DELETE"
    REQUEST_COMMENT_VISIBILITY_PUBLIC = "REQUEST_COMMENT_VISIBILITY_PUBLIC"

    # request file status
    REQUEST_FILE_ADD = "REQUEST_FILE_ADD"
    REQUEST_FILE_UPDATE = "REQUEST_FILE_UPDATE"
    REQUEST_FILE_WITHDRAW = "REQUEST_FILE_WITHDRAW"
    REQUEST_FILE_APPROVE = "REQUEST_FILE_APPROVE"
    REQUEST_FILE_REQUEST_CHANGES = "REQUEST_FILE_REQUEST_CHANGES"
    REQUEST_FILE_RESET_REVIEW = "REQUEST_FILE_RESET_REVIEW"
    REQUEST_FILE_UNDECIDED = "REQUEST_FILE_UNDECIDED"
    REQUEST_FILE_RELEASE = "REQUEST_FILE_RELEASE"


class NotificationEventType(Enum):
    REQUEST_SUBMITTED = "request_submitted"
    REQUEST_WITHDRAWN = "request_withdrawn"
    REQUEST_PARTIALLY_REVIEWED = "request_partially_reviewed"
    REQUEST_REVIEWED = "request_reviewed"
    REQUEST_APPROVED = "request_approved"
    REQUEST_RELEASED = "request_released"
    REQUEST_REJECTED = "request_rejected"
    REQUEST_RETURNED = "request_returned"
    REQUEST_RESUBMITTED = "request_resubmitted"


class PathType(Enum):
    """Types of PathItems in a tree."""

    FILE = "file"
    DIR = "directory"
    WORKSPACE = "workspace"
    REQUEST = "request"
    FILEGROUP = "filegroup"
    REPO = "repo"

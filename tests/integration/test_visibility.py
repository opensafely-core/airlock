from dataclasses import dataclass

from airlock.business_logic import BusinessLogicLayer
from airlock.enums import (
    AuditEventType,
    RequestFileVote,
    RequestStatus,
    ReviewTurnPhase,
    Visibility,
)
from airlock.models import (
    AuditEvent,
    Comment,
    ReleaseRequest,
)
from airlock.users import User
from tests import factories


@dataclass
class VisibleItemsHelper:
    """Helper class to make assertions about visiblity of comments and audit logs.

    It will fetch comments and audits that are visible for a specific request and
    user, and store them to make assertions about.
    """

    comments: list[tuple[Comment, str]]
    audits: list[AuditEvent]

    @classmethod
    def for_group(
        cls, request: ReleaseRequest, user: User, group, bll: BusinessLogicLayer
    ):
        return cls(
            comments=request.get_visible_comments_for_group(group, user),
            audits=bll.get_request_audit_log(user, request, group),
        )

    def is_comment_visible(self, text: str, author: User) -> bool:
        for c, _ in self.comments:
            if c.comment == text and c.author == author.username:
                return True

        return False

    def is_audit_visible(self, type_: AuditEventType, author: User) -> bool:
        for audit in self.audits:
            if audit.type == type_ and audit.user == author.username:
                return True

        return False

    def comment(self, text: str, author: User) -> bool:
        """Is this comment in the list of visible items we have?"""
        if not self.is_comment_visible(text=text, author=author):
            return False

        return self.is_audit_visible(
            type_=AuditEventType.REQUEST_COMMENT, author=author
        )

    def vote(self, vote: RequestFileVote, author: User) -> bool:
        """Is this vote in the list of visible items we have?"""
        match vote:
            case RequestFileVote.APPROVED:
                event_type = AuditEventType.REQUEST_FILE_APPROVE
            case RequestFileVote.CHANGES_REQUESTED:
                event_type = AuditEventType.REQUEST_FILE_REQUEST_CHANGES
            case _:  # pragma: nocover
                assert False

        return self.is_audit_visible(type_=event_type, author=author)


def test_request_comment_and_audit_visibility(bll):
    # This test is long and complex.
    #
    # It tests both get_visible_comments_for_group() and
    # get_request_audit_log(), and uses the custom VisibleItems helper above.
    #
    # It walks through a couple of rounds and turns of back and forth review,
    # validating that the comments that are visibile to various users at
    # different points in the process are correct.
    #
    author = factories.create_user("author1", workspaces=["workspace"])
    checkers = factories.get_default_output_checkers()

    release_request = factories.create_request_at_status(
        "workspace",
        status=RequestStatus.SUBMITTED,
        author=author,
        files=[factories.request_file(group="group", path="file.txt", approved=True)],
    )

    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 0 private",
        Visibility.PRIVATE,
        checkers[0],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 1 private",
        Visibility.PRIVATE,
        checkers[1],
    )

    release_request = factories.refresh_release_request(release_request)

    assert release_request.review_turn == 1
    assert release_request.get_turn_phase() == ReviewTurnPhase.INDEPENDENT

    def get_visible_items(user):
        return VisibleItemsHelper.for_group(release_request, user, "group", bll)

    # in ReviewTurnPhase.INDEPENDENT, checkers can only see own comments, author can see nothing
    visible = get_visible_items(checkers[0])
    assert visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert not visible.vote(RequestFileVote.APPROVED, checkers[1])

    visible = get_visible_items(checkers[1])
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert not visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])

    visible = get_visible_items(author)
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert not visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert not visible.vote(RequestFileVote.APPROVED, checkers[1])

    factories.submit_independent_review(release_request)
    bll.group_comment_create(
        release_request,
        "group",
        "turn 1 checker 0 public",
        Visibility.PUBLIC,
        checkers[0],
    )
    release_request = factories.refresh_release_request(release_request)

    assert release_request.review_turn == 1
    assert release_request.get_turn_phase() == ReviewTurnPhase.CONSOLIDATING

    # in ReviewTurnPhase.CONSOLIDATING, checkers should see all private comments
    # and pending public comments, but author should not see any yet
    for checker in checkers:
        visible = get_visible_items(checker)
        assert visible.comment("turn 1 checker 0 private", checkers[0])
        assert visible.vote(RequestFileVote.APPROVED, checkers[0])
        assert visible.comment("turn 1 checker 1 private", checkers[1])
        assert visible.vote(RequestFileVote.APPROVED, checkers[1])
        assert visible.comment("turn 1 checker 0 public", checkers[0])

    # author still cannot see anything
    visible = get_visible_items(author)
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert not visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert not visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert not visible.comment("turn 1 checker 0 public", checkers[0])

    bll.return_request(release_request, checkers[0])
    release_request = factories.refresh_release_request(release_request)
    bll.group_comment_create(
        release_request,
        "group",
        "turn 2 author public",
        Visibility.PUBLIC,
        author,
    )
    release_request = factories.refresh_release_request(release_request)

    assert release_request.review_turn == 2
    assert release_request.get_turn_phase() == ReviewTurnPhase.AUTHOR

    # in ReviewTurnPhase.AUTHOR, checkers should see turn 1 comments, but not authors turn 2 comments.
    # Author should turn 1 and 2 public comments
    for checker in checkers:
        visible = get_visible_items(checker)
        assert visible.comment("turn 1 checker 0 private", checkers[0])
        assert visible.vote(RequestFileVote.APPROVED, checkers[0])
        assert visible.comment("turn 1 checker 1 private", checkers[1])
        assert visible.vote(RequestFileVote.APPROVED, checkers[1])
        assert visible.comment("turn 1 checker 0 public", checkers[0])
        assert not visible.comment("turn 2 author public", author)

    # author can see al turn 1 public comments and votes, their turn 2 public comments, but no private comments.
    visible = get_visible_items(author)
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert visible.comment("turn 1 checker 0 public", checkers[0])
    assert visible.comment("turn 2 author public", author)

    bll.submit_request(release_request, author)
    release_request = factories.refresh_release_request(release_request)
    assert release_request.review_turn == 3

    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 0 private",
        Visibility.PRIVATE,
        checkers[0],
    )
    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 1 private",
        Visibility.PRIVATE,
        checkers[1],
    )
    # checker0 requests changes to the file now. Not realistic, but we want to check that
    # the audit log for later round votes is hidden
    bll.request_changes_to_file(
        release_request,
        release_request.get_request_file_from_output_path("file.txt"),
        checkers[0],
    )
    release_request = factories.refresh_release_request(release_request)

    # in ReviewTurnPhase.INDEPENDENT for a 2nd round
    # Checkers should see previous round's private comments, but not this rounds
    # Author should see previous round's public comments, but not any this round
    visible = get_visible_items(checkers[0])
    assert visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert visible.comment("turn 1 checker 0 public", checkers[0])
    assert visible.comment("turn 2 author public", author)
    assert visible.comment("turn 3 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
    assert not visible.comment("turn 3 checker 1 private", checkers[1])

    visible = get_visible_items(checkers[1])
    assert visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert visible.comment("turn 1 checker 0 public", checkers[0])
    assert visible.comment("turn 2 author public", author)
    assert not visible.comment("turn 3 checker 0 private", checkers[0])
    assert not visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
    assert visible.comment("turn 3 checker 1 private", checkers[1])

    visible = get_visible_items(author)
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert visible.comment("turn 1 checker 0 public", checkers[0])
    assert visible.comment("turn 2 author public", author)
    assert not visible.comment("turn 3 checker 0 private", checkers[0])
    assert not visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
    assert not visible.comment("turn 3 checker 1 private", checkers[1])

    factories.submit_independent_review(release_request)
    release_request = factories.refresh_release_request(release_request)
    bll.group_comment_create(
        release_request,
        "group",
        "turn 3 checker 0 public",
        Visibility.PUBLIC,
        checkers[0],
    )
    release_request = factories.refresh_release_request(release_request)

    # in ReviewTurnPhase.CONSOLIDATING for a 2nd round
    # Checkers should see previous and current round's private comments,
    # Author should see previous round's public comments, but not any private comments
    for checker in checkers:
        visible = get_visible_items(checker)
        assert visible.comment("turn 1 checker 0 private", checkers[0])
        assert visible.vote(RequestFileVote.APPROVED, checkers[0])
        assert visible.comment("turn 1 checker 1 private", checkers[1])
        assert visible.vote(RequestFileVote.APPROVED, checkers[1])
        assert visible.comment("turn 1 checker 0 public", checkers[0])
        assert visible.comment("turn 2 author public", author)
        assert visible.comment("turn 3 checker 0 private", checkers[0])
        assert visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
        assert visible.comment("turn 3 checker 1 private", checkers[1])
        assert visible.comment("turn 3 checker 0 public", checkers[0])

    # author sees no private comments, and no turn 3 things.
    visible = get_visible_items(author)
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert visible.comment("turn 1 checker 0 public", checkers[0])
    assert visible.comment("turn 2 author public", author)
    assert not visible.comment("turn 3 checker 0 private", checkers[0])
    assert not visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
    assert not visible.comment("turn 3 checker 1 private", checkers[1])
    assert not visible.comment("turn 3 checker 0 public", checkers[0])

    # reject the request
    bll.set_status(release_request, RequestStatus.REJECTED, checkers[0])
    release_request = factories.refresh_release_request(release_request)
    # no increment, as there was no return to author
    assert release_request.review_turn == 3
    assert release_request.get_turn_phase() == ReviewTurnPhase.COMPLETE

    # COMPLETE has special handling - test it works
    # checkers can see all things
    for checker in checkers:
        visible = get_visible_items(checker)
        assert visible.comment("turn 1 checker 0 private", checkers[0])
        assert visible.vote(RequestFileVote.APPROVED, checkers[0])
        assert visible.comment("turn 1 checker 1 private", checkers[1])
        assert visible.vote(RequestFileVote.APPROVED, checkers[1])
        assert visible.comment("turn 1 checker 0 public", checkers[0])
        assert visible.comment("turn 2 author public", author)
        assert visible.comment("turn 3 checker 0 private", checkers[0])
        assert visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
        assert visible.comment("turn 3 checker 1 private", checkers[1])
        assert visible.comment("turn 3 checker 0 public", checkers[0])

    # Author should see all public comments, regardless of round, but not any private comments
    visible = get_visible_items(author)
    assert not visible.comment("turn 1 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.APPROVED, checkers[0])
    assert not visible.comment("turn 1 checker 1 private", checkers[1])
    assert visible.vote(RequestFileVote.APPROVED, checkers[1])
    assert visible.comment("turn 1 checker 0 public", checkers[0])
    assert visible.comment("turn 2 author public", author)
    assert not visible.comment("turn 3 checker 0 private", checkers[0])
    assert visible.vote(RequestFileVote.CHANGES_REQUESTED, checkers[0])
    assert not visible.comment("turn 3 checker 1 private", checkers[1])
    assert visible.comment("turn 3 checker 0 public", checkers[0])

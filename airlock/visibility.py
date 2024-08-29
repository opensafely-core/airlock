from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from airlock.enums import (
    RequestFileDecision,
    RequestFileVote,
    ReviewTurnPhase,
    Visibility,
)
from airlock.users import User


@dataclass
class RequestFileStatus:
    """The current visible decision and individual vote for a specific user."""

    user: User
    decision: RequestFileDecision
    vote: RequestFileVote | None


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

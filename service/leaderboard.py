"""Leaderboard query: a Challenge's scored Submissions, ranked.

Rank is computed over every ``completed``, live Submission of the Challenge,
by unrounded Headline Metric in the Challenge's Direction, then the earlier
Submission, then id, so every row has its own Rank (see GLOSSARY.md). Rows are
chosen only after Rank is computed, so a page never changes anyone's Rank.

``read_leaderboard`` is the only entry point; the HTTP endpoint is a thin
wrapper. A filter on the rows shown (Submission Type, search text) belongs on
the outer query in ``read_leaderboard``, never inside ``_ranked``, so that it
hides rows without renumbering them.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from models import Challenge, Submission, User
from models.challenge import Direction
from models.submission import SubmissionStatus, SubmissionType
from service import scorer_catalogue

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class ChallengeNotFound(Exception):
    """No live Challenge has that id."""


@dataclass(frozen=True)
class LeaderboardRow:
    """One ranked Submission."""

    submission_id: uuid.UUID
    rank: int
    type: SubmissionType
    name: str
    submitter: str
    created_at: datetime.datetime
    headline_metric: float
    preferred_model: Optional[str]

    @property
    def has_download(self) -> bool:
        """Prompts and Skills can be downloaded; a Model is found at its own host."""
        return self.type != SubmissionType.MODEL


@dataclass(frozen=True)
class LeaderboardScorer:
    """What a Leaderboard is ranked by and how its numbers are shown: the
    Challenge's Scorer name, label and Direction, with the registry's display
    format and Public Summary declaration for that name."""

    name: str
    label: str
    direction: str
    format: dict[str, Any]
    public_summary: List[dict[str, Any]]


@dataclass(frozen=True)
class Leaderboard:
    """One page of a Challenge's Leaderboard."""

    scorer: LeaderboardScorer
    page: int
    limit: int
    rows: List[LeaderboardRow]


def read_leaderboard(
    db: Session, challenge_id: uuid.UUID, *, page: int = 1, limit: int = DEFAULT_PAGE_SIZE
) -> Leaderboard:
    """Rank a Challenge's scored Submissions and return one page of them.

    Args:
        db: Database session.
        challenge_id: The Challenge whose Leaderboard to read.
        page: Which page, from 1.
        limit: Rows per page, from 1; cut to ``MAX_PAGE_SIZE``.

    Returns:
        The Scorer block, the page and page size used, and that page's rows
        in Rank order.

    Raises:
        ChallengeNotFound: If no live Challenge has that id.
    """
    challenge = db.get(Challenge, challenge_id)
    if challenge is None or challenge.deleted_at is not None:
        raise ChallengeNotFound(challenge_id)
    limit = min(limit, MAX_PAGE_SIZE)
    # Rank first over the whole Leaderboard; only then choose the rows shown.
    ranked = _ranked(challenge).subquery()
    shown = select(ranked).order_by(ranked.c.rank).offset((page - 1) * limit).limit(limit)
    return Leaderboard(
        scorer=_scorer(challenge),
        page=page,
        limit=limit,
        rows=[LeaderboardRow(**row) for row in db.execute(shown).mappings()],
    )


def _scorer(challenge: Challenge) -> LeaderboardScorer:
    # Read live from the registry: a Scorer name never changes meaning (ADR 0001).
    described = scorer_catalogue.describe_scorer(challenge.scorer_name)
    return LeaderboardScorer(
        name=challenge.scorer_name,
        label=challenge.scorer_label,
        direction=challenge.direction,
        format=described["display_format"],
        public_summary=described["public_summary"],
    )


def _ranked(challenge: Challenge) -> Select:
    """Every row of the Challenge's Leaderboard with its Rank, unordered."""
    best_first = (
        Submission.headline_metric.desc()
        if challenge.direction == Direction.HIGHER_IS_BETTER
        else Submission.headline_metric.asc()
    )
    rank = func.row_number().over(order_by=(best_first, Submission.created_at, Submission.id))
    return (
        select(
            Submission.id.label("submission_id"),
            rank.label("rank"),
            Submission.type,
            Submission.name,
            User.username.label("submitter"),
            Submission.created_at,
            Submission.headline_metric,
            Submission.preferred_model,
        )
        .join(User, User.id == Submission.user_id)
        .where(
            Submission.challenge_id == challenge.id,
            Submission.status == SubmissionStatus.COMPLETED,
            Submission.is_live,
        )
    )

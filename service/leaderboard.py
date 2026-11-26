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
from typing import List, Optional

from sqlalchemy import Select, Subquery, func, or_, select
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
    direction: Direction
    display_format: scorer_catalogue.DisplayFormat
    public_summary: List[scorer_catalogue.SummaryEntry]


@dataclass(frozen=True)
class LeaderboardCounts:
    """Rows of the whole Leaderboard, in all and per Submission Type."""

    all: int
    model: int
    prompt: int
    skill: int


@dataclass(frozen=True)
class Leaderboard:
    """One page of a Challenge's Leaderboard."""

    scorer: LeaderboardScorer
    counts: LeaderboardCounts
    total: int
    page: int
    limit: int
    rows: List[LeaderboardRow]


def read_leaderboard(
    db: Session,
    challenge_id: uuid.UUID,
    *,
    submission_type: Optional[SubmissionType] = None,
    search: Optional[str] = None,
    page: int = 1,
    limit: int = DEFAULT_PAGE_SIZE,
) -> Leaderboard:
    """Rank a Challenge's scored Submissions and return one page of them.

    Args:
        db: Database session.
        challenge_id: The Challenge whose Leaderboard to read.
        submission_type: Show only rows of this Submission Type; None for all.
        search: Show only rows whose name, submitter username or Preferred
            Model contains this text, ignoring case; None or blank for all.
        page: Which page, from 1.
        limit: Rows per page, from 1; cut to ``MAX_PAGE_SIZE``.

    Returns:
        The Scorer block; how many rows the whole Leaderboard holds, in all
        and per Submission Type, whatever the filter, search and page; how
        many rows the filter and search show on all pages together; the
        page and page size used; and that page's rows in Rank order.

    Raises:
        ChallengeNotFound: If no live Challenge has that id.
    """
    challenge = db.get(Challenge, challenge_id)
    if challenge is None or challenge.deleted_at is not None:
        raise ChallengeNotFound(challenge_id)
    limit = min(limit, MAX_PAGE_SIZE)
    # Rank first over the whole Leaderboard; only then choose the rows shown.
    ranked = _ranked(challenge).subquery()
    shown = select(ranked)
    if submission_type is not None:
        shown = shown.where(ranked.c.type == submission_type)
    if search and search.strip():
        text = search.strip()
        shown = shown.where(
            or_(
                ranked.c.name.icontains(text, autoescape=True),
                ranked.c.submitter.icontains(text, autoescape=True),
                ranked.c.preferred_model.icontains(text, autoescape=True),
            )
        )
    total = db.execute(select(func.count()).select_from(shown.subquery())).scalar_one()
    shown = shown.order_by(ranked.c.rank).offset((page - 1) * limit).limit(limit)
    return Leaderboard(
        scorer=read_scorer(challenge),
        counts=_counts(db, ranked),
        total=total,
        page=page,
        limit=limit,
        rows=[LeaderboardRow(**row) for row in db.execute(shown).mappings()],
    )


def read_rank(db: Session, submission: Submission) -> Optional[int]:
    """A Submission's Rank on its Challenge's Leaderboard, the same Rank the
    Leaderboard shows it at.

    Args:
        db: Database session.
        submission: The Submission.

    Returns:
        Its Rank, or None when it is not on the Leaderboard (not scored, or deleted).
    """
    ranked = _ranked(submission.challenge).subquery()
    return db.execute(
        select(ranked.c.rank).where(ranked.c.submission_id == submission.id)
    ).scalar_one_or_none()


def _counts(db: Session, ranked: Subquery) -> LeaderboardCounts:
    """Rows of the whole Leaderboard, in all and per Submission Type."""
    per_type = dict(db.execute(select(ranked.c.type, func.count()).group_by(ranked.c.type)).all())
    counts = {kind.value: per_type.get(kind, 0) for kind in SubmissionType}
    return LeaderboardCounts(all=sum(counts.values()), **counts)


def read_scorer(challenge: Challenge) -> LeaderboardScorer:
    """The Scorer block of the Challenge's Leaderboard, read live from the
    registry: a Scorer name never changes meaning (ADR 0001).

    Args:
        challenge: The Challenge whose Scorer to describe.

    Returns:
        What its Submissions are ranked by and how their numbers are shown.
    """
    return LeaderboardScorer(
        name=challenge.scorer_name,
        label=challenge.scorer_label,
        direction=Direction(challenge.direction),
        display_format=scorer_catalogue.display_format(challenge.scorer_name),
        public_summary=scorer_catalogue.public_summary(challenge.scorer_name),
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

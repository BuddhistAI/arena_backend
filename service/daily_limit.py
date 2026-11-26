"""Daily Submission limit: at most ``DAILY_SUBMISSION_LIMIT`` stored
Submissions per participant per Challenge per UTC day.

There is no hidden test split, so the limit is what stops a participant from
recovering the Ground Truth by submitting small variations. Every stored
Submission counts, whatever its status; uploads rejected at validation are
never stored, so they never count. Admins are exempt.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models import Challenge, Submission, User
from models.user import ADMIN_ROLE

DAILY_SUBMISSION_LIMIT = 10


class DailyLimitReached(Exception):
    """The participant has used today's Submissions on this Challenge.

    ``reset_at`` is when they may submit again (the next 00:00 UTC, naive UTC).
    """

    def __init__(self, reset_at: datetime.datetime) -> None:
        self.reset_at = reset_at
        super().__init__(
            f"You have reached the limit of {DAILY_SUBMISSION_LIMIT} Submissions to this Challenge "
            f"per day. It resets at {reset_at:%Y-%m-%d %H:%M} UTC."
        )


def check_daily_limit(db: Session, challenge: Challenge, user: User, now: datetime.datetime) -> None:
    """Refuse the user's next Submission to the Challenge if today's are used up.

    Args:
        db: Database session.
        challenge: The Challenge being submitted to.
        user: The signed-in submitter.
        now: The current time in naive UTC, from the clock port.

    Concurrent Submissions by one user to one Challenge are serialised: the
    check holds a lock until ``db``'s transaction ends, so the caller must
    store the Submission in that same transaction.

    Raises:
        DailyLimitReached: The user already stored the limit today.
    """
    if user.role == ADMIN_ROLE:
        return
    _lock_user_on_challenge(db, challenge, user)
    day_start = datetime.datetime.combine(now.date(), datetime.time())
    if _stored_since(db, challenge, user, day_start) >= DAILY_SUBMISSION_LIMIT:
        raise DailyLimitReached(reset_at=day_start + datetime.timedelta(days=1))


def _lock_user_on_challenge(db: Session, challenge: Challenge, user: User) -> None:
    """Wait for, then hold until the transaction ends, the user's Postgres
    advisory lock on the Challenge."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtext(user.id), func.hashtext(str(challenge.id)))))


def _stored_since(db: Session, challenge: Challenge, user: User, since: datetime.datetime) -> int:
    return (
        db.query(func.count(Submission.id))
        .filter(
            Submission.user_id == user.id,
            Submission.challenge_id == challenge.id,
            Submission.created_at >= since,
        )
        .scalar()
    )

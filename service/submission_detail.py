"""Submission detail: everything public about one Submission, for its page.

``read_submission_detail`` is the entry point for the page, and
``read_visible_submission`` applies the same visibility for other reads (the
status a submitter polls); the HTTP endpoints are thin wrappers. A scored (``completed``) Submission is public. One that is not
scored yet, or failed, is shown only to its submitter and admins, so they have
one place to see why scoring failed. A deleted Submission is ``SubmissionGone``
for everyone. Rank comes from the Leaderboard's own ranking, so the page and
the Leaderboard never disagree. Per-item scores are never part of it: only
the Scorer's Public Summary is.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from models import Submission, User
from models.challenge import SyncStatus
from models.submission import SubmissionStatus, SubmissionType
from models.user import ADMIN_ROLE
from ports.object_storage import ObjectNotFound, ObjectStorage
from ports.repo_host import challenge_repo_folder_url
from service.leaderboard import LeaderboardScorer, read_rank, read_scorer
from service.submission_download import download_path


class SubmissionNotFound(Exception):
    """No Submission has that id, or the viewer may not see it."""


class SubmissionGone(Exception):
    """The Submission was deleted."""


@dataclass(frozen=True)
class SubmissionDetail:
    """One Submission as its detail page shows it."""

    id: uuid.UUID
    challenge_id: uuid.UUID
    rank: Optional[int]
    type: SubmissionType
    name: str
    submitter: str
    created_at: datetime.datetime
    description: Optional[str]
    status: SubmissionStatus
    status_message: Optional[str]
    scorer: LeaderboardScorer
    headline_metric: Optional[float]
    public_summary: Optional[List[Dict[str, Any]]]
    model_link: Optional[str]
    preferred_model: Optional[str]
    license: Optional[str]
    sync_status: SyncStatus
    repo_path: Optional[str]
    repo_url: Optional[str]
    prompt_text: Optional[str]
    download_url: Optional[str]


def read_submission_detail(
    db: Session, storage: ObjectStorage, submission_id: uuid.UUID, *, viewer: Optional[User]
) -> SubmissionDetail:
    """Read a Submission's detail as ``viewer`` may see it.

    Args:
        db: Database session.
        storage: Object storage, which holds a Prompt's text.
        submission_id: The Submission to read.
        viewer: The signed-in user, or None for an anonymous visitor.

    Returns:
        The Submission's detail.

    Raises:
        SubmissionNotFound: If no Submission has that id, or it is not scored
            and the viewer is neither its submitter nor an admin.
        SubmissionGone: If the Submission was deleted.
    """
    submission = read_visible_submission(db, submission_id, viewer=viewer)
    submitter = db.get(User, submission.user_id)
    return SubmissionDetail(
        id=submission.id,
        challenge_id=submission.challenge_id,
        rank=read_rank(db, submission),
        type=submission.type,
        name=submission.name,
        submitter=submitter.username,
        created_at=submission.created_at,
        description=submission.description,
        status=submission.status,
        status_message=submission.status_message,
        scorer=read_scorer(submission.challenge),
        headline_metric=submission.headline_metric,
        public_summary=submission.public_summary,
        model_link=submission.model_link,
        preferred_model=submission.preferred_model,
        license=submission.license,
        sync_status=SyncStatus(submission.sync_status),
        repo_path=submission.repo_path,
        repo_url=_repo_url(submission),
        prompt_text=_prompt_text(submission, storage),
        download_url=download_path(submission.id, submission.type),
    )


def read_visible_submission(db: Session, submission_id: uuid.UUID, *, viewer: Optional[User]) -> Submission:
    """The Submission, if ``viewer`` may see it: a scored one is public; one
    not scored yet, or failed, only to its submitter and admins.

    Args:
        db: Database session.
        submission_id: The Submission to read.
        viewer: The signed-in user, or None for an anonymous visitor.

    Returns:
        The Submission.

    Raises:
        SubmissionNotFound: If no Submission has that id, or it is not scored
            and the viewer is neither its submitter nor an admin.
        SubmissionGone: If the Submission was deleted.
    """
    submission = db.get(Submission, submission_id)
    if submission is None:
        raise SubmissionNotFound(submission_id)
    if not submission.is_live:
        raise SubmissionGone(submission_id)
    if submission.status != SubmissionStatus.COMPLETED and not _may_see_unscored(viewer, submission):
        raise SubmissionNotFound(submission_id)
    return submission


def _may_see_unscored(viewer: Optional[User], submission: Submission) -> bool:
    """Only its submitter and admins see a Submission that is not scored."""
    return viewer is not None and (viewer.id == submission.user_id or viewer.role == ADMIN_ROLE)


def _repo_url(submission: Submission) -> Optional[str]:
    """The Submission's folder on GitHub, once synced: a pending or failed
    sync shows no link rather than one that may not open."""
    if submission.sync_status != SyncStatus.SYNCED:
        return None
    return challenge_repo_folder_url(submission.challenge.repo_name, submission.repo_path)


def _prompt_text(submission: Submission, storage: ObjectStorage) -> Optional[str]:
    """A Prompt's text as stored; None for a Model or Skill, or when the
    Prompt is missing from storage."""
    if submission.type != SubmissionType.PROMPT:
        return None
    try:
        return storage.get(submission.artifact_url).decode("utf-8")
    except ObjectNotFound:
        return None

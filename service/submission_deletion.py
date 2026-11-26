"""Deleting a Submission.

The row is kept with ``deleted_at`` set, so the daily limit still counts it;
everything else treats it as gone. Its Artifact and Predictions File are
removed from object storage once the deletion is committed, so a failed commit
never leaves a live Submission without its files.

Only a scored Submission can have a folder in the Challenge Repo, so only its
deletion queues a sync of the Challenge, which rebuilds the repo without it.
Deleting any other Submission writes nothing to the repo.
"""

from datetime import datetime
from typing import List

from sqlalchemy.orm import Session

from models.challenge import Challenge
from models.submission import Submission, SubmissionStatus
from ports.object_storage import ObjectStorage


def delete_submission(db: Session, storage: ObjectStorage, submission: Submission, at: datetime) -> None:
    """Mark ``submission`` deleted ``at`` and remove its stored files.

    Args:
        db: The session ``submission`` belongs to; committed here.
        storage: Where the Submission's Artifact and Predictions File are.
        submission: A Submission that is not deleted yet.
        at: The time of the deletion (naive UTC).
    """
    submission.deleted_at = at
    if submission.status == SubmissionStatus.COMPLETED:
        db.get(Challenge, submission.challenge_id).queue_sync()
    db.commit()
    for key in _stored_keys(submission):
        storage.delete(key)


def _stored_keys(submission: Submission) -> List[str]:
    """The storage keys of the Submission's Predictions File and Artifact."""
    return [key for key in (submission.predictions_url, submission.artifact_url) if key is not None]

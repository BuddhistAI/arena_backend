"""Submission download: a short-lived storage URL for a scored Prompt or Skill.

The file always comes from our storage, never from GitHub, so a download
works before the Submission's sync finishes and after it fails. It is named
after the Submission's folder in the Challenge Repo (``<slug>-<id>``, as the
Submission publisher names it), with ``.md`` for a Prompt and ``.zip`` for a
Skill's uploaded zip. A Model has no download: it is found at its own host.
"""

from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import Optional

from sqlalchemy.orm import Session

from models import Submission
from models.submission import SubmissionStatus, SubmissionType
from ports.object_storage import ObjectStorage
from service.submission_publisher import submission_folder

DOWNLOAD_EXPIRY_SECONDS = 5 * 60

# The extension each downloadable Submission Type's file is saved with.
DOWNLOAD_EXTENSIONS = {
    SubmissionType.PROMPT: ".md",
    SubmissionType.SKILL: ".zip",
}


class DownloadNotFound(Exception):
    """No scored, live Prompt or Skill has that id."""


def download_path(submission_id: uuid.UUID, submission_type: SubmissionType) -> Optional[str]:
    """The API path a Submission downloads from, relative to the API root;
    None for a Model."""
    if submission_type not in DOWNLOAD_EXTENSIONS:
        return None
    return f"/submissions/{submission_id}/download"


def presigned_download_url(db: Session, storage: ObjectStorage, submission_id: uuid.UUID) -> str:
    """A short-lived URL that downloads the Submission's Artifact from storage.

    Args:
        db: Database session.
        storage: Object storage holding the Artifact.
        submission_id: The Submission to download.

    Returns:
        The presigned URL, whose response is named ``<slug>-<id>.md`` or
        ``<slug>-<id>.zip``.

    Raises:
        DownloadNotFound: If no Submission has that id, or it is a Model,
            deleted or not scored.
    """
    submission = db.get(Submission, submission_id)
    if (
        submission is None
        or submission.type not in DOWNLOAD_EXTENSIONS
        or not submission.is_live
        or submission.status != SubmissionStatus.COMPLETED
    ):
        raise DownloadNotFound(submission_id)
    return storage.presigned_download_url(
        submission.artifact_url,
        expires_in_seconds=DOWNLOAD_EXPIRY_SECONDS,
        file_name=_file_name(submission),
    )


def _file_name(submission: Submission) -> str:
    """``<slug>-<id>`` of the Submission's repo folder, plus its extension.
    A synced Submission keeps the folder it was published in; one not synced
    yet takes the folder the publisher would give it."""
    folder = submission.repo_path or submission_folder(submission)
    return PurePosixPath(folder).name + DOWNLOAD_EXTENSIONS[submission.type]

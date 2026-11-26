"""Submission publisher: the files a scored Submission adds to its Challenge Repo.

``submission_files`` is a function of the Submission row, its submitter's
public username, the Challenge's Scorer name and the Submission's Artifact in
storage, so a sync and a Resync produce the same folder. Every Submission
gets one folder, ``<type folder>/<slug>-<id>``, holding ``submission.json``
and its Artifact's files (``ARTIFACT_FILES``, one entry per Submission Type
that can be published). The Predictions File is never among them.
"""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

import pyewts

from models.submission import Submission, SubmissionType
from ports.object_storage import ObjectStorage

SCHEMA_VERSION = 1
SUBMISSION_JSON = "submission.json"
SHORT_ID_LENGTH = 8
SLUG_MAX_LENGTH = 60

TYPE_FOLDERS = {
    SubmissionType.MODEL: "models",
    SubmissionType.PROMPT: "prompts",
    SubmissionType.SKILL: "skills",
}

# The Artifact's files, by name inside the Submission's folder.
ArtifactFiles = Callable[[Submission, ObjectStorage], Dict[str, bytes]]


def _no_artifact(submission: Submission, storage: ObjectStorage) -> Dict[str, bytes]:
    """A Model Submission's link is in its metadata; it has no files."""
    return {}


ARTIFACT_FILES: Dict[SubmissionType, ArtifactFiles] = {
    SubmissionType.MODEL: _no_artifact,
}

_wylie = pyewts.pyewts()


@dataclass(frozen=True)
class PublishedSubmission:
    """A Submission's folder in the Challenge Repo and its files, keyed by
    their path in the repo."""

    folder: str
    files: Dict[str, bytes]


def is_publishable(submission_type: str) -> bool:
    """Whether Submissions of this type have a publisher entry yet.

    Args:
        submission_type: A ``SubmissionType`` value.

    Returns:
        True when ``submission_files`` can publish it.
    """
    return SubmissionType(submission_type) in ARTIFACT_FILES


def submission_files(
    submission: Submission, *, submitter: str, scorer_name: str, storage: ObjectStorage
) -> PublishedSubmission:
    """The folder and files that publish ``submission``.

    Args:
        submission: A scored Submission of a publishable type.
        submitter: The submitter's public username.
        scorer_name: The Scorer of the Submission's Challenge.
        storage: Where the Artifact is stored.

    Returns:
        The folder, with ``submission.json`` and the Artifact's files in it.
    """
    folder = submission_folder(submission)
    artifact = ARTIFACT_FILES[SubmissionType(submission.type)](submission, storage)
    metadata = submission_json(submission, submitter=submitter, scorer_name=scorer_name)
    files = {f"{folder}/{name}": content for name, content in artifact.items()}
    files[f"{folder}/{SUBMISSION_JSON}"] = _json_bytes(metadata)
    return PublishedSubmission(folder=folder, files=files)


def submission_folder(submission: Submission) -> str:
    """``<type folder>/<slug>-<id>``, or ``<type folder>/<id>`` when the name
    gives no slug; ``<id>`` is the first 8 characters of the Submission id.

    Args:
        submission: The Submission to name the folder of.

    Returns:
        The folder's path in the Challenge Repo.
    """
    short_id = str(submission.id)[:SHORT_ID_LENGTH]
    slug = folder_slug(submission.name)
    name = f"{slug}-{short_id}" if slug else short_id
    return f"{TYPE_FOLDERS[SubmissionType(submission.type)]}/{name}"


def folder_slug(name: str) -> str:
    """An ASCII slug of ``name``: Tibetan transliterated to Wylie, then
    lowercase letters and digits with other runs collapsed to one hyphen.

    Args:
        name: The Submission's name.

    Returns:
        The slug, at most ``SLUG_MAX_LENGTH`` characters; empty when nothing
        in the name can be slugged.
    """
    ascii_name = unicodedata.normalize("NFKD", _wylie.toWylie(name)).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:SLUG_MAX_LENGTH].strip("-")


def submission_json(submission: Submission, *, submitter: str, scorer_name: str) -> Dict[str, Any]:
    """The Submission's public metadata. Fields that do not apply to its type
    are null; the submitter is named by public username only.

    Args:
        submission: The scored Submission.
        submitter: The submitter's public username.
        scorer_name: The Scorer of the Submission's Challenge.

    Returns:
        The content of ``submission.json``.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "submission_id": str(submission.id),
        "challenge_id": str(submission.challenge_id),
        "type": submission.type,
        "name": submission.name,
        "description": submission.description,
        "submitter": submitter,
        "scorer_name": scorer_name,
        "headline_metric": submission.headline_metric,
        "preferred_model": submission.preferred_model,
        "model_link": submission.model_link,
        "license": submission.license,
        "created_at": _utc_timestamp(submission.created_at),
    }


def _utc_timestamp(moment: Optional[datetime]) -> Optional[str]:
    """ISO 8601 with a ``Z``; the arena stores naive UTC."""
    return None if moment is None else moment.replace(tzinfo=None).isoformat() + "Z"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

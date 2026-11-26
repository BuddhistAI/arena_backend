"""Submission publisher: the files a scored Submission adds to its Challenge Repo.

``submission_files`` is a function of the Submission row, its submitter's
public username, the Challenge's Scorer name and the Submission's Artifact in
storage, so a sync and a Resync produce the same folder. Every Submission
gets one folder, ``<type folder>/<slug>-<id>`` (the full id if an earlier
Submission already has that folder), holding ``submission.json``
and its Artifact's files (``ARTIFACT_FILES``, one entry per Submission Type
that can be published). The Predictions File is never among them.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import AbstractSet, Any, Callable, Dict, Iterable, Optional

import pyewts

from models.submission import Submission, SubmissionType
from ports.object_storage import ObjectStorage
from service.challenge_publisher import json_bytes
from service.skill_zip import METADATA_FILE, skill_files

SCHEMA_VERSION = 1
SUBMISSION_JSON = METADATA_FILE
PROMPT_FILE = "prompt.md"
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


def _prompt_file(submission: Submission, storage: ObjectStorage) -> Dict[str, bytes]:
    """A Prompt, typed or uploaded, is stored and published as ``prompt.md``."""
    return {PROMPT_FILE: storage.get(submission.artifact_url)}


def _skill_files(submission: Submission, storage: ObjectStorage) -> Dict[str, bytes]:
    """A Skill is published as its zip's files, never as the zip."""
    return skill_files(storage.get(submission.artifact_url))


ARTIFACT_FILES: Dict[SubmissionType, ArtifactFiles] = {
    SubmissionType.MODEL: _no_artifact,
    SubmissionType.PROMPT: _prompt_file,
    SubmissionType.SKILL: _skill_files,
}

_wylie = pyewts.pyewts()


@dataclass(frozen=True)
class PublishedSubmission:
    """A Submission's folder in the Challenge Repo and its files, keyed by
    their path in the repo, and the public username it is published under."""

    folder: str
    files: Dict[str, bytes]
    submitter: str


def submission_files(
    submission: Submission,
    *,
    submitter: str,
    scorer_name: str,
    storage: ObjectStorage,
    taken_folders: AbstractSet[str] = frozenset(),
) -> PublishedSubmission:
    """The folder and files that publish ``submission``.

    Args:
        submission: A scored Submission.
        submitter: The submitter's public username.
        scorer_name: The Scorer of the Submission's Challenge.
        storage: Where the Artifact is stored.
        taken_folders: Folders of the Challenge's earlier Submissions.

    Returns:
        The folder, with ``submission.json`` and the Artifact's files in it.
    """
    folder = submission_folder(submission, taken_folders)
    artifact = ARTIFACT_FILES[submission.type](submission, storage)
    metadata = submission_json(submission, submitter=submitter, scorer_name=scorer_name)
    files = {f"{folder}/{name}": content for name, content in artifact.items()}
    files[f"{folder}/{SUBMISSION_JSON}"] = json_bytes(metadata)
    return PublishedSubmission(folder=folder, files=files, submitter=submitter)


def submission_folder(submission: Submission, taken_folders: AbstractSet[str] = frozenset()) -> str:
    """``<type folder>/<slug>-<id>``, or ``<type folder>/<id>`` when the name
    gives no slug; ``<id>`` is the first 8 characters of the Submission id,
    or the full id when that folder is already taken.

    Args:
        submission: The Submission to name the folder of.
        taken_folders: Folders of the Challenge's earlier Submissions.

    Returns:
        The folder's path in the Challenge Repo.
    """
    short = _folder_with_id(submission, str(submission.id)[:SHORT_ID_LENGTH])
    return _folder_with_id(submission, str(submission.id)) if short in taken_folders else short


def folder_among(submission: Submission, published_in_order: Iterable[Submission]) -> str:
    """The folder a sync gives ``submission``: each Submission takes its folder
    in publishing order, so ``taken_folders`` is every earlier one's.

    Args:
        submission: The Submission to name the folder of.
        published_in_order: The Challenge's scored, live Submissions, oldest
            first, as the Sync Worker publishes them; ``submission`` among them.

    Returns:
        The folder's path in the Challenge Repo.
    """
    taken: set[str] = set()
    for earlier in published_in_order:
        if earlier.id == submission.id:
            break
        taken.add(submission_folder(earlier, taken))
    return submission_folder(submission, taken)


def _folder_with_id(submission: Submission, shown_id: str) -> str:
    slug = folder_slug(submission.name)
    name = f"{slug}-{shown_id}" if slug else shown_id
    return f"{TYPE_FOLDERS[submission.type]}/{name}"


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
    are null; the submitter is named by public username only; its score is
    the Headline Metric and the Scorer's Public Summary, never per-item scores.

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
        "type": submission.type.value,
        "name": submission.name,
        "description": submission.description,
        "submitter": submitter,
        "scorer_name": scorer_name,
        "headline_metric": submission.headline_metric,
        "public_summary": submission.public_summary,
        "preferred_model": submission.preferred_model,
        "model_link": submission.model_link,
        "license": submission.license,
        "created_at": _utc_timestamp(submission.created_at),
    }


def _utc_timestamp(moment: Optional[datetime]) -> Optional[str]:
    """ISO 8601 with a ``Z``; the arena stores naive UTC."""
    return None if moment is None else moment.replace(tzinfo=None).isoformat() + "Z"

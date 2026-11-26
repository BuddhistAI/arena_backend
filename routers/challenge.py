from fastapi import APIRouter, status, HTTPException, Depends, Path, Form, File, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from dataclasses import dataclass
from typing import Annotated, Dict, Iterable, List, Optional
from sqlalchemy.orm import Session
import models
from models import Challenge
from models.user import User
from database import get_db
from schemas.challenge import ChallengeAdminRead, ChallengeRead, ChallengeWithCategoryRead
from uuid import UUID
from sqlalchemy.orm import joinedload
from auth import require_admin
import io
import json
import logging
import re
import unicodedata
import uuid
from models.challenge import utc_now
from ports.object_storage import ObjectStorage, get_object_storage, private_key, public_key
from service import scorer_catalogue
from service.challenge_data import (
    GROUND_TRUTH_FIELD,
    INPUTS_FIELD,
    MEDIA_ARCHIVE_FIELD,
    ChallengeDataError,
    InvalidChallengeData,
    media_archive_size_limit,
    validate_challenge_data,
)

# Constants
CHALLENGE_NOT_FOUND_MESSAGE = "Challenge not found"
# GitHub allows 100 characters in a repo name; leave room for "challenge-".
REPO_SLUG_MAX_LENGTH = 89

router = APIRouter(prefix="/challenges", tags=["challenges"])

db_dependency = Annotated[Session, Depends(get_db)]


def _live_challenges(db: Session):
    """Challenges that are not deleted: a deleted Challenge is a tombstone the
    arena no longer shows (see ``Challenge.deleted_at``)."""
    return db.query(Challenge).filter(Challenge.deleted_at.is_(None))


def _live_challenge_or_404(db: Session, challenge_id: UUID) -> Challenge:
    challenge = _live_challenges(db).filter(Challenge.id == challenge_id).one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
    return challenge


def _challenge_or_404(db: Session, challenge_id: UUID) -> Challenge:
    """Any Challenge, deleted ones included: for admin reads and Resync."""
    challenge = db.get(Challenge, challenge_id)
    if challenge is None:
        raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
    return challenge


# for listing all challenges

@router.get("/list", response_model=List[ChallengeWithCategoryRead])
async def list_challenges_with_category(db: db_dependency):
    try:
        # Eager load the category relationship
        challenges = _live_challenges(db).options(joinedload(Challenge.category)).all()
        return challenges
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# for admins: every Challenge, deleted ones included, with its sync state

@router.get(
    "/admin/list",
    response_model=List[ChallengeAdminRead],
    summary="List every challenge, deleted ones included (admin)",
    responses={403: {"description": "The signed-in user is not an admin."}},
)
def admin_list_challenges(db: db_dependency, current_user: User = Depends(require_admin)):
    """Every Challenge with its storage keys, sync status and last sync error,
    including deleted ones (whose repo archiving may have failed)."""
    return (
        db.query(Challenge)
        .options(joinedload(Challenge.category))
        .order_by(Challenge.created_at)
        .all()
    )


@router.get(
    "/admin/{challenge_id}",
    response_model=ChallengeAdminRead,
    summary="Read a challenge, even a deleted one (admin)",
    responses={403: {"description": "The signed-in user is not an admin."}},
)
def admin_get_challenge(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge"),
):
    return _challenge_or_404(db, challenge_id)


# for getting a specific challenge

@router.get("/{challenge_id}", response_model=ChallengeRead)
async def get_challenge(db: db_dependency, challenge_id: UUID = Path(..., description="This is the ID of the challenge")):
    return _live_challenge_or_404(db, challenge_id)

# for creating a challenge

def repo_name_for(title: str, challenge_id: uuid.UUID) -> str:
    """``challenge-<slug-of-title>``: ASCII letters and digits, lowercased,
    other runs of characters collapsed to one hyphen. A title with no ASCII
    letter or digit (e.g. all Tibetan) gives ``challenge-<short id>``, the
    first 8 hex digits of the Challenge id."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")
    slug = slug[:REPO_SLUG_MAX_LENGTH].strip("-")
    return f"challenge-{slug or challenge_id.hex[:8]}"


def _invalid(errors: List[ChallengeDataError]) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "The Challenge data is invalid.",
            "errors": [error.to_dict() for error in errors],
        },
    )


@router.post(
    "/create",
    response_model=ChallengeAdminRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new challenge",
    responses={
        403: {"description": "The signed-in user is not an admin."},
        409: {"description": "The title, or the repo name derived from it, is taken."},
        422: {"description": "Invalid data: ``errors`` lists each problem."},
    },
)
def create_new_challenge(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    storage: ObjectStorage = Depends(get_object_storage),
    max_media_archive_bytes: int = Depends(media_archive_size_limit),
    title: str = Form(..., description="Challenge title; also names the Challenge Repo"),
    category_id: UUID = Form(..., description="Category ID"),
    image_uri: Optional[str] = Form(None, description="Challenge image URL"),
    description: Optional[str] = Form(None, description="Challenge description"),
    challenge_status: Optional[str] = Form("active", alias="status", description="Challenge status"),
    scorer_name: str = Form(..., description="Name of a Scorer from GET /scorers"),
    scorer_params: str = Form("{}", description="The Scorer's parameters as a JSON object"),
    inputs_file: UploadFile = File(..., description="Inputs JSONL: {id, input, media?} rows"),
    ground_truth_file: UploadFile = File(..., description="Ground Truth JSONL: {id, reference} rows"),
    media_archive_file: Optional[UploadFile] = File(None, description="Optional Media Archive (zip)"),
):
    """
    Create a Challenge (admin only). The files are validated against the
    Scorer; the Inputs and Media Archive are stored publicly and the Ground
    Truth privately. The Challenge starts with sync status ``pending``; the
    Sync Worker publishes its Challenge Repo later.
    """
    title = title.strip()
    errors: List[ChallengeDataError] = []
    if db.get(models.Category, category_id) is None:
        errors.append(ChallengeDataError("category_id", f"no category with id {category_id}"))

    media_archive = media_archive_file.file if media_archive_file is not None else None
    try:
        validated = validate_challenge_data(
            inputs=inputs_file.file.read(),
            ground_truth=ground_truth_file.file.read(),
            scorer_name=scorer_name,
            scorer_params=scorer_params,
            media_archive=media_archive,
            max_media_archive_bytes=max_media_archive_bytes,
        )
    except InvalidChallengeData as exc:
        errors.extend(exc.errors)
    if errors:
        return _invalid(errors)

    challenge_id = uuid.uuid4()
    repo_name = repo_name_for(title, challenge_id)
    if db.query(Challenge).filter(Challenge.title == title).first():
        raise HTTPException(status_code=409, detail=f"A Challenge titled {title!r} already exists.")
    clash = db.query(Challenge).filter(Challenge.repo_name == repo_name).first()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"The title gives the repo name {repo_name!r}, already used by the Challenge {clash.title!r}.",
        )

    keys = _store_uploads(storage, challenge_id, {
        INPUTS_FIELD: inputs_file.file,
        GROUND_TRUTH_FIELD: ground_truth_file.file,
        MEDIA_ARCHIVE_FIELD: media_archive,
    })
    challenge = Challenge(
        id=challenge_id,
        title=title,
        category_id=category_id,
        image_uri=image_uri or None,
        description=description,
        status=challenge_status or "active",
        created_by=current_user.id,
        scorer_name=validated.scorer["name"],
        scorer_label=validated.scorer["label"],
        direction=validated.scorer["direction"],
        scorer_params=validated.params,
        package_version=scorer_catalogue.PACKAGE_VERSION,
        repo_name=repo_name,
        **keys,
    )
    challenge.queue_sync()
    db.add(challenge)
    _commit_or_discard(db, storage, keys.values(), title)
    db.refresh(challenge)
    return challenge


@dataclass(frozen=True)
class _StoredFile:
    """An uploaded file: its form field, the Challenge column holding its
    storage key, its object name and whether it is public."""

    field: str
    column: str
    filename: str
    content_type: str
    public: bool


STORED_FILES = (
    _StoredFile(INPUTS_FIELD, "inputs_key", "inputs.jsonl", "application/jsonl", public=True),
    _StoredFile(GROUND_TRUTH_FIELD, "ground_truth_key", "ground_truth.jsonl", "application/jsonl", public=False),
    _StoredFile(MEDIA_ARCHIVE_FIELD, "media_archive_key", "media.zip", "application/zip", public=True),
)


def _new_key(challenge_id: UUID, stored: _StoredFile) -> str:
    """``challenges/<id>/<upload id>/<file name>`` under the public or private
    prefix. Every upload gets a new key, so a replacement never overwrites an
    object the database still points at."""
    make_key = public_key if stored.public else private_key
    return make_key("challenges", str(challenge_id), uuid.uuid4().hex, stored.filename)


def _store_uploads(storage: ObjectStorage, challenge_id: UUID, uploads: dict) -> Dict[str, str]:
    """Put each upload (form field -> file object, None for none) under a new
    key and return Challenge column -> key. If one put fails, the objects
    already stored are deleted."""
    keys: Dict[str, str] = {}
    try:
        for stored in STORED_FILES:
            if uploads.get(stored.field) is not None:
                key = _new_key(challenge_id, stored)
                _put(storage, key, uploads[stored.field], stored.content_type)
                keys[stored.column] = key
    except Exception:
        _delete_quietly(storage, keys.values())
        raise
    return keys


def _commit_or_discard(db: Session, storage: ObjectStorage, new_keys: Iterable[str], title: Optional[str]) -> None:
    """Commit; if that fails, delete the objects just stored for it, which no
    row points at."""
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        _delete_quietly(storage, new_keys)
        raise HTTPException(status_code=409, detail=f"A Challenge titled {title!r} already exists.")
    except Exception:
        db.rollback()
        _delete_quietly(storage, new_keys)
        raise


def _delete_quietly(storage: ObjectStorage, keys: Iterable[str]) -> None:
    for key in list(keys):
        try:
            storage.delete(key)
        except Exception:
            logging.exception("Could not delete the unused object %s", key)

# for editing a challenge

# Form fields whose change re-validates the data and is refused after the Lock.
DATA_FIELDS = ("scorer_name", "scorer_params", "inputs_file", "ground_truth_file", "media_archive_file")


@router.patch(
    "/update/{challenge_id}",
    response_model=ChallengeAdminRead,
    status_code=status.HTTP_200_OK,
    summary="Edit a challenge",
    responses={
        403: {"description": "The signed-in user is not an admin."},
        409: {"description": "The Challenge is Locked and a file was sent or the Scorer or parameters changed, or the title is taken."},
        422: {"description": "Invalid data: ``errors`` lists each problem."},
    },
)
def update_challenge(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    storage: ObjectStorage = Depends(get_object_storage),
    max_media_archive_bytes: int = Depends(media_archive_size_limit),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to update"),
    title: Optional[str] = Form(None, description="Challenge title (does not rename the repo)"),
    category_id: Optional[str] = Form(None, description="Category ID"),
    image_uri: Optional[str] = Form(None, description="Challenge image URL"),
    description: Optional[str] = Form(None, description="Challenge description"),
    challenge_status: Optional[str] = Form(None, alias="status", description="Challenge status"),
    scorer_name: Optional[str] = Form(None, description="Name of a Scorer from GET /scorers"),
    scorer_params: Optional[str] = Form(
        None, description="The Scorer's parameters as a JSON object. Omitted with a new Scorer: its defaults"
    ),
    inputs_file: Optional[UploadFile] = File(None, description="Replacement Inputs JSONL"),
    ground_truth_file: Optional[UploadFile] = File(None, description="Replacement Ground Truth JSONL"),
    media_archive_file: Optional[UploadFile] = File(None, description="Replacement or new Media Archive (zip)"),
):
    """
    Edit a Challenge (admin only). Every field is optional; a missing or
    blank field is left unchanged. Scorer, parameter and file changes are
    validated with the same rules as create, against the stored data for
    whatever is not replaced, and are refused with 409 once the Challenge is
    Locked. A Scorer or parameters equal to the stored ones are not a change;
    an uploaded file always is. Title, description and image stay editable. Any accepted change
    sets sync status to ``pending`` so the Sync Worker re-publishes the repo.
    """
    challenge = _live_challenge_or_404(db, challenge_id)

    sent = {
        name: value
        for name, value in {
            "title": (title or "").strip() or None,
            "category_id": category_id or None,
            "image_uri": image_uri or None,
            "description": description or None,
            "status": challenge_status or None,
            "scorer_name": scorer_name or None,
            "scorer_params": scorer_params or None,
            "inputs_file": inputs_file,
            "ground_truth_file": ground_truth_file,
            "media_archive_file": media_archive_file,
        }.items()
        if value is not None
    }
    _drop_unchanged_scorer(challenge, sent)
    if not sent:
        return challenge

    data_edits = [name for name in DATA_FIELDS if name in sent]
    if data_edits and challenge.locked_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The Challenge is Locked (its first Submission arrived at {challenge.locked_at.isoformat()}), "
                f"so {', '.join(data_edits)} can no longer change. Title, description and image can still be edited."
            ),
        )

    errors: List[ChallengeDataError] = []
    if "category_id" in sent:
        sent["category_id"] = _existing_category_id(db, sent["category_id"], errors)
    validated = None
    if data_edits:
        try:
            validated = _validate_edit(challenge, sent, storage, max_media_archive_bytes)
        except InvalidChallengeData as exc:
            errors.extend(exc.errors)
    if errors:
        return _invalid(errors)

    if "title" in sent and db.query(Challenge).filter(
        Challenge.title == sent["title"], Challenge.id != challenge.id
    ).first():
        raise HTTPException(status_code=409, detail=f"A Challenge titled {sent['title']!r} already exists.")

    # Replacements go under new keys and the row points at them only once
    # committed, so a failed edit never leaves new files with an old Scorer.
    # The replaced objects are deleted after the commit.
    keys = _store_uploads(storage, challenge.id, {
        stored.field: sent[stored.field].file for stored in STORED_FILES if stored.field in sent
    })
    replaced = [getattr(challenge, column) for column in keys if getattr(challenge, column)]
    for column, key in keys.items():
        setattr(challenge, column, key)

    for name in ("title", "category_id", "image_uri", "description", "status"):
        if name in sent:
            setattr(challenge, name, sent[name])
    if validated is not None:
        challenge.scorer_name = validated.scorer["name"]
        challenge.scorer_label = validated.scorer["label"]
        challenge.direction = validated.scorer["direction"]
        challenge.scorer_params = validated.params
        challenge.package_version = scorer_catalogue.PACKAGE_VERSION
    # New content, so a fresh round of sync attempts.
    challenge.queue_sync()
    _commit_or_discard(db, storage, keys.values(), sent.get("title"))
    _delete_quietly(storage, replaced)
    db.refresh(challenge)
    return challenge


def _drop_unchanged_scorer(challenge: Challenge, sent: dict) -> None:
    """A Scorer or parameters equal to the stored ones are not a change: a form
    may resend them with a new title, even after the Lock. Parameters are
    compared with their defaults filled in. Uploaded files always count."""
    if sent.get("scorer_name") == challenge.scorer_name:
        del sent["scorer_name"]
    if "scorer_name" not in sent and "scorer_params" in sent and _same_params(challenge, sent["scorer_params"]):
        del sent["scorer_params"]


def _same_params(challenge: Challenge, raw: str) -> bool:
    try:
        params = json.loads(raw)
        if not isinstance(params, dict):
            return False
        return scorer_catalogue.validate_params(challenge.scorer_name, params) == challenge.scorer_params
    except (ValueError, TypeError, scorer_catalogue.ScorerError):
        return False


def _existing_category_id(db: Session, value: str, errors: List[ChallengeDataError]) -> Optional[UUID]:
    try:
        category_id = UUID(value)
    except ValueError:
        errors.append(ChallengeDataError("category_id", f"{value!r} is not a UUID"))
        return None
    if db.get(models.Category, category_id) is None:
        errors.append(ChallengeDataError("category_id", f"no category with id {category_id}"))
    return category_id


def _validate_edit(challenge: Challenge, sent: dict, storage: ObjectStorage, max_media_archive_bytes: int):
    """Validate the Challenge as it would be after the edit: the sent files and
    Scorer settings, and the stored ones for whatever was not sent."""
    new_scorer_name = sent.get("scorer_name", challenge.scorer_name)
    if "scorer_params" in sent:
        params = sent["scorer_params"]
    elif new_scorer_name == challenge.scorer_name:
        params = challenge.scorer_params
    else:
        params = None  # another Scorer: its defaults, not the old Scorer's parameters

    def file_or_stored(field: str, key: str) -> bytes:
        if field in sent:
            return sent[field].file.read()
        return storage.get(key)

    if "media_archive_file" in sent:
        media_archive = sent["media_archive_file"].file
    elif challenge.media_archive_key:
        # The port has no streaming read, so the stored archive is held in memory.
        media_archive = io.BytesIO(storage.get(challenge.media_archive_key))
    else:
        media_archive = None

    return validate_challenge_data(
        inputs=file_or_stored("inputs_file", challenge.inputs_key),
        ground_truth=file_or_stored("ground_truth_file", challenge.ground_truth_key),
        scorer_name=new_scorer_name,
        scorer_params=params,
        media_archive=media_archive,
        max_media_archive_bytes=max_media_archive_bytes,
    )


def _put(storage: ObjectStorage, key: str, body, content_type: str) -> None:
    body.seek(0)
    storage.put(key, body, content_type=content_type)


# for resyncing a challenge

@router.post(
    "/resync/{challenge_id}",
    response_model=ChallengeAdminRead,
    status_code=status.HTTP_200_OK,
    summary="Resync a challenge's repo",
    responses={403: {"description": "The signed-in user is not an admin."}},
)
def resync_challenge(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to resync"),
):
    """
    Resync a Challenge (admin only): reset its sync attempts and set its sync
    status to ``pending``. The Sync Worker then rebuilds the repo entirely from
    the arena's data, creating it if it is missing and replacing every file,
    so edits made directly on GitHub are overwritten. Typically used after a
    sync ``failed`` once its cause (``last_sync_error``) is fixed. On a
    deleted Challenge it retries archiving the repo instead.
    """
    challenge = _challenge_or_404(db, challenge_id)
    challenge.queue_sync()
    db.commit()
    db.refresh(challenge)
    return challenge


# for deleting a challenge

@router.delete(
    "/remove/{challenge_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete a challenge",
    responses={403: {"description": "The signed-in user is not an admin."}},
)
def delete_challenge(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to delete"),
):
    """
    Delete a Challenge (admin only). It disappears from the arena at once and
    the Sync Worker archives its repo, which stays on GitHub read-only. The row
    is kept as a tombstone, so the repo name is never reused.
    """
    challenge = _live_challenge_or_404(db, challenge_id)
    deleted_at = utc_now()
    challenge.deleted_at = deleted_at
    challenge.queue_sync()
    db.commit()
    logging.info("Challenge %s (%s) deleted; its repo will be archived", challenge.id, challenge.repo_name)
    return {
        "message": "Challenge deleted; its repo will be archived",
        "deleted_challenge": {
            "id": str(challenge.id),
            "title": challenge.title,
            "repo_name": challenge.repo_name,
            "created_at": challenge.created_at.isoformat(),
            "deleted_at": deleted_at.isoformat(),
        },
        "status": "success",
    }

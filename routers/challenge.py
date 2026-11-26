from fastapi import APIRouter, status, HTTPException, Depends, Path, Form, File, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from typing import Annotated, List, Optional
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
import logging
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from models.challenge import SYNC_PENDING
from ports.object_storage import ObjectStorage, get_object_storage, private_key, public_key
from service import scorer_catalogue
from service.challenge_data import (
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


def _queue_sync(challenge: Challenge) -> None:
    """Leave the Challenge to the Sync Worker with a fresh round of attempts."""
    challenge.sync_status = SYNC_PENDING
    challenge.sync_attempts = 0
    challenge.last_sync_error = None


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
    response_model=ChallengeRead,
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

    inputs_key = public_key("challenges", str(challenge_id), "inputs.jsonl")
    ground_truth_key = private_key("challenges", str(challenge_id), "ground_truth.jsonl")
    media_archive_key = (
        public_key("challenges", str(challenge_id), "media.zip") if media_archive is not None else None
    )
    uploads = [
        (inputs_key, inputs_file.file, "application/jsonl"),
        (ground_truth_key, ground_truth_file.file, "application/jsonl"),
    ]
    if media_archive is not None:
        uploads.append((media_archive_key, media_archive, "application/zip"))
    stored: List[str] = []
    try:
        for key, body, content_type in uploads:
            body.seek(0)
            storage.put(key, body, content_type=content_type)
            stored.append(key)

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
            inputs_key=inputs_key,
            media_archive_key=media_archive_key,
            ground_truth_key=ground_truth_key,
            repo_name=repo_name,
            sync_status=SYNC_PENDING,
            sync_attempts=0,
        )
        db.add(challenge)
        db.commit()
    except IntegrityError:
        db.rollback()
        _delete_quietly(storage, stored)
        raise HTTPException(status_code=409, detail=f"A Challenge titled {title!r} already exists.")
    except Exception:
        db.rollback()
        _delete_quietly(storage, stored)
        raise
    db.refresh(challenge)
    return challenge


def _delete_quietly(storage: ObjectStorage, keys: List[str]) -> None:
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            logging.exception("Could not delete %s after a failed create", key)

# for editing a challenge

# Form fields whose change re-validates the data and is refused after the Lock.
DATA_FIELDS = ("scorer_name", "scorer_params", "inputs_file", "ground_truth_file", "media_archive_file")


@router.patch(
    "/update/{challenge_id}",
    response_model=ChallengeRead,
    status_code=status.HTTP_200_OK,
    summary="Edit a challenge",
    responses={
        403: {"description": "The signed-in user is not an admin."},
        409: {"description": "The Challenge is Locked and a data or Scorer field was sent, or the title is taken."},
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
    Locked. Title, description and image stay editable. Any accepted change
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

    # Files are written over the existing keys so public URLs stay stable.
    if "inputs_file" in sent:
        _put(storage, challenge.inputs_key, inputs_file.file, "application/jsonl")
    if "ground_truth_file" in sent:
        _put(storage, challenge.ground_truth_key, ground_truth_file.file, "application/jsonl")
    if "media_archive_file" in sent:
        challenge.media_archive_key = challenge.media_archive_key or public_key(
            "challenges", str(challenge.id), "media.zip"
        )
        _put(storage, challenge.media_archive_key, media_archive_file.file, "application/zip")

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
    _queue_sync(challenge)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"A Challenge titled {sent.get('title')!r} already exists.")
    db.refresh(challenge)
    return challenge


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
    response_model=ChallengeRead,
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
    _queue_sync(challenge)
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
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    challenge.deleted_at = deleted_at
    _queue_sync(challenge)
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

from fastapi import APIRouter, status, HTTPException, Depends, Path, Form, File, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from typing import Annotated, List, Optional
from sqlalchemy.orm import Session
import models
from models import Challenge
from models.user import User
from database import get_db
from schemas.challenge import ChallengeRead, ChallengeWithCategoryRead
from uuid import UUID
from sqlalchemy.orm import joinedload
from auth import get_current_active_user, require_admin
import logging
import re
import unicodedata
import uuid
from datetime import datetime
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


# for listing all challenges

@router.get("/list", response_model=List[ChallengeWithCategoryRead])
async def list_challenges_with_category(db: db_dependency):
    try:
        # Eager load the category relationship
        challenges = db.query(Challenge).options(joinedload(Challenge.category)).all()
        return challenges
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# for getting a specific challenge

@router.get("/{challenge_id}", response_model=ChallengeRead)
async def get_challenge(db: db_dependency, challenge_id: UUID = Path(..., description="This is the ID of the challenge")):
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
    return challenge

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

# for updating a challenge

@router.patch("/update/{challenge_id}", response_model=ChallengeRead, status_code=status.HTTP_200_OK)
async def update_challenge(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to update"),
    title: Optional[str] = Form(None, description="Challenge title"),
    category_id: Optional[str] = Form(None, description="Category ID"),
    image_uri: Optional[str] = Form(None, description="Challenge image URL"),
    description: Optional[str] = Form(None, description="Challenge description"),
    status: Optional[str] = Form(None, description="Challenge status"),
):
    """
    Update an existing challenge's presentation fields. Fields are optional
    and only updated if provided. Data and Scorer edits come with ticket 05.
    """
    try:
        logging.info(f"Updating challenge: {challenge_id}")
        
        # Find existing challenge
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
        
        # Convert empty strings to None for all optional fields to preserve existing data
        if title == "":
            title = None
        if category_id == "":
            category_id = None
        elif category_id is not None:
            try:
                category_id = UUID(category_id)  # Convert string to UUID
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format for category_id")
        if image_uri == "":
            image_uri = None
        if description == "":
            description = None
        if status == "":
            status = None
        
        # Update basic fields if provided
        if title is not None:
            challenge.title = title
        if category_id is not None:
            challenge.category_id = category_id
        if image_uri is not None:
            challenge.image_uri = image_uri
        if description is not None:
            challenge.description = description
        if status is not None:
            challenge.status = status
        
        # Commit changes
        db.commit()
        db.refresh(challenge)
        
        logging.info(f"Challenge updated successfully: {challenge_id}")
        return challenge
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Unexpected error updating challenge {challenge_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# for deleting a challenge

@router.delete("/remove/{challenge_id}", status_code=status.HTTP_200_OK)
async def delete_challenge(
    db: db_dependency, 
    current_user: User = Depends(get_current_active_user),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to delete")
):
    """
    Delete a challenge by ID and return confirmation.
    
    Authorization rules:
    - Admin users can delete any challenge
    - Non-admin users can only delete challenges they created
    
    Returns a confirmation message with details about the deleted challenge.
    """
    try:
        logging.info(f"Attempting to delete challenge: {challenge_id}")
        
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            logging.warning(f"Challenge not found for deletion: {challenge_id}")
            raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
        
        # Check authorization: admin can delete any challenge, non-admin can only delete their own
        if current_user.role != 'admin' and challenge.created_by != current_user.id:
            logging.warning(f"User {current_user.id} attempted to delete challenge {challenge_id} owned by {challenge.created_by}")
            raise HTTPException(status_code=403, detail="You can only delete challenges you created")
        
        # Check if there are submissions referencing this challenge
        related_submissions = db.query(models.Submission).filter(models.Submission.challenge_id == challenge_id).all()
        if related_submissions:
            submission_count = len(related_submissions)
            logging.warning(f"Cannot delete challenge {challenge_id}: {submission_count} submissions reference it")
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete challenge. There are {submission_count} submission(s) that reference this challenge. Please delete all related submissions first."
            )
        
        challenge_title = challenge.title
        challenge_created_at = challenge.created_at
        
        db.delete(challenge)
        db.commit()
        
        logging.info(f"Challenge deleted successfully: {challenge_id} - {challenge_title}")
        
        return {
            "message": "Challenge deleted successfully",
            "deleted_challenge": {
                "id": str(challenge_id),
                "title": challenge_title,
                "created_at": challenge_created_at.isoformat(),
                "deleted_at": datetime.now().isoformat()
            },
            "status": "success"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Unexpected error deleting challenge {challenge_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

from fastapi import APIRouter, status, HTTPException, Depends, Path, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse, RedirectResponse
from typing import Annotated, List, Optional, Union
from sqlalchemy.orm import Session
import models
from models.user import ADMIN_ROLE, User
from database import get_db
from schemas.submission import (
    DailyLimitResponse,
    SubmissionCreatedResponse,
    SubmissionDetailRead,
    SubmissionRead,
    SubmissionStatusResponse,
)
from ports.clock import Clock, get_clock
from ports.object_storage import ObjectStorage, get_object_storage
from service.daily_limit import DailyLimitReached
from service.submission_detail import SubmissionDetail, SubmissionGone, SubmissionNotFound, read_submission_detail
from service.submission_download import DownloadNotFound, presigned_download_url
from service.submission_deletion import delete_submission as delete_submission_and_files
from service.submission_intake import SubmissionForm, SubmissionIntake, SubmissionRejected
from submission_worker import SubmissionQueue, get_queue_stats, get_submission_queue
from submission_cache import get_cache_stats
from auth import get_current_active_user, get_optional_user, require_admin
from models.submission import SubmissionStatus
import datetime
import math
import uuid

# Constants for repeated strings
SUBMISSION_ID_PATH_DESCRIPTION = "This is the ID of the submission"
SUBMISSION_NOT_FOUND_MESSAGE = "Submission not found"
SUBMISSION_DELETED_MESSAGE = "This Submission was deleted"

router = APIRouter(prefix="/submissions", tags=["submissions"])

db_dependency = Annotated[Session, Depends(get_db)]


def _live_submission_or_404(db: Session, submission_id: uuid.UUID) -> models.Submission:
    submission = models.Submission.live(db).filter(models.Submission.id == submission_id).one_or_none()
    if submission is None:
        raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)
    return submission

# ******** basic endpoints ********
@router.get("", response_model=List[SubmissionRead])
def list_all_submissions(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    challenge_id: Optional[uuid.UUID] = Query(None, description="Only this Challenge's Submissions"),
) -> List[models.Submission]:
    """List every user's Submissions (admin only), optionally of one Challenge."""
    submissions = models.Submission.live(db)
    if challenge_id is not None:
        submissions = submissions.filter(models.Submission.challenge_id == challenge_id)
    return submissions.all()

@router.get("/my", response_model=List[SubmissionRead])
async def list_my_submissions(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user)
) -> List[models.Submission]:
    """List submissions created by the current authenticated user."""
    return models.Submission.live(db).filter(models.Submission.user_id == current_user.id).all()

@router.get("/preferred-models", response_model=List[str])
def list_preferred_models(db: db_dependency) -> List[str]:
    """List the Preferred Model ids already used, for the form's autocomplete.

    Returns:
        Each distinct Preferred Model id on any Challenge's Submissions that
        were not deleted, sorted.
    """
    rows = (
        db.query(models.Submission.preferred_model)
        .filter(models.Submission.preferred_model.isnot(None), models.Submission.is_live)
        .distinct()
        .order_by(models.Submission.preferred_model)
        .all()
    )
    return [preferred_model for (preferred_model,) in rows]

@router.get(
    "/{submission_id}",
    response_model=SubmissionDetailRead,
    responses={
        404: {"description": "No Submission has that id, or it is not scored and not yours."},
        410: {"description": "The Submission was deleted."},
    },
)
def get_submission(
    db: db_dependency,
    viewer: Optional[User] = Depends(get_optional_user),
    storage: ObjectStorage = Depends(get_object_storage),
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION),
) -> SubmissionDetail:
    """
    Everything public about a Submission, for its detail page. A scored
    Submission is public; one pending, processing or failed is shown only
    to its submitter and admins, with its status and message. Per-item
    scores are never returned, only the Scorer's Public Summary.
    """
    try:
        return read_submission_detail(db, storage, submission_id, viewer=viewer)
    except SubmissionNotFound:
        raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)
    except SubmissionGone:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=SUBMISSION_DELETED_MESSAGE)

@router.get(
    "/{submission_id}/download",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    response_class=RedirectResponse,
    responses={404: {"description": "No scored, live Prompt or Skill has that id."}},
)
def download_submission(
    db: db_dependency,
    storage: ObjectStorage = Depends(get_object_storage),
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION),
) -> RedirectResponse:
    """
    Download a scored Prompt as ``<slug>-<id>.md`` or a Skill as the zip its
    author uploaded, ``<slug>-<id>.zip``; no sign-in. Redirects to a
    short-lived storage URL, so the download works whatever the Submission's
    GitHub sync status. A Model has no download.
    """
    try:
        return RedirectResponse(presigned_download_url(db, storage, submission_id))
    except DownloadNotFound:
        raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)

@router.get("/{submission_id}/status", response_model=SubmissionStatusResponse)
async def get_submission_status(
    db: db_dependency, 
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION)
) -> models.Submission:
    """
    Get where a Submission is in scoring, to poll after creating it.

    Status values:
    - pending: stored, waiting for a worker
    - processing: being scored
    - completed: scored; ``headline_metric`` and ``public_summary`` are set
    - failed: ``status_message`` says why
    """
    return _live_submission_or_404(db, submission_id)



@router.post(
    "/{submission_id}/resync",
    response_model=SubmissionRead,
    summary="Resync a submission's repo folder (admin)",
    responses={
        403: {"description": "The signed-in user is not an admin."},
        409: {"description": "The Submission is not scored, so it is never published."},
    },
)
def resync_submission(
    db: db_dependency,
    current_user: User = Depends(require_admin),
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION),
) -> models.Submission:
    """
    Resync a Submission (admin only): set its sync status to ``pending`` and
    queue a sync of its Challenge, which rebuilds the Challenge Repo, this
    Submission's folder included, from the database and object storage.
    Typically used once a sync ``failed``.
    """
    submission = _live_submission_or_404(db, submission_id)
    if submission.status != SubmissionStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Only scored Submissions are published to the Challenge Repo")
    submission.queue_sync()
    db.get(models.Challenge, submission.challenge_id).queue_sync()
    db.commit()
    db.refresh(submission)
    return submission


@router.delete(
    "/{submission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a submission (submitter or admin)",
    responses={
        403: {"description": "The signed-in user neither made the Submission nor is an admin."},
        404: {"description": "No Submission has that id, or it was already deleted."},
    },
)
def delete_submission(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    storage: ObjectStorage = Depends(get_object_storage),
    clock: Clock = Depends(get_clock),
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION),
) -> None:
    """
    Delete a Submission (its submitter or an admin). Submissions cannot be
    edited, so this is how one is corrected: delete it and submit again.
    It still counts toward that day's limit, and its Challenge stays Locked.
    """
    submission = _live_submission_or_404(db, submission_id)
    if current_user.role != ADMIN_ROLE and submission.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own submissions")
    delete_submission_and_files(db, storage, submission, at=clock())

# ******** basic endpoints ********


# ******** create a submission ********
def submission_form(
    submission_type: str = Form(..., alias="type", description="Submission Type: model, prompt or skill"),
    name: str = Form("", description="Name of the Submission; for a Model, the model name (reused or created)"),
    description: str = Form("", description="Description of the Submission; for a Prompt or Skill, how to run it"),
    model_link: Optional[str] = Form(None, description="Model only: optional https link to the model"),
    preferred_model: Optional[str] = Form(None, description="Prompt or Skill: the exact model id it was run with"),
    license_: Optional[str] = Form(
        None, alias="license", description="Prompt or Skill: CC-BY-4.0, CC0-1.0, MIT or Apache-2.0"
    ),
    prompt_text: Optional[str] = Form(None, description="Prompt only: the prompt, typed (or send prompt_file)"),
    prompt_file: Optional[UploadFile] = File(None, description="Prompt only: the prompt as a text file"),
    skill_file: Optional[UploadFile] = File(
        None, description="Skill only: the skill as a zip (any layout; safety-checked, never extracted here)"
    ),
    predictions_file: UploadFile = File(..., description="Predictions File: JSONL {id, prediction} rows, one per Input id"),
) -> SubmissionForm:
    """The multipart form of a Submission, as the intake takes it (a FastAPI dependency).

    Returns:
        The form fields, with the uploaded files read into bytes.
    """
    return SubmissionForm(
        type=submission_type,
        name=name,
        description=description,
        model_link=model_link,
        preferred_model=preferred_model,
        license=license_,
        prompt_text=prompt_text,
        prompt_file=_read(prompt_file),
        skill_file=_read(skill_file),
        predictions=predictions_file.file.read(),
    )


def _read(upload: Optional[UploadFile]) -> Optional[bytes]:
    return upload.file.read() if upload is not None else None


def submission_intake(
    db: db_dependency,
    storage: ObjectStorage = Depends(get_object_storage),
    submission_queue: SubmissionQueue = Depends(get_submission_queue),
    clock: Clock = Depends(get_clock),
) -> SubmissionIntake:
    """The Submission intake, wired to the request's outside world (a FastAPI dependency).

    Returns:
        An intake on the request's session, object storage, queue and clock.
    """
    return SubmissionIntake(db=db, storage=storage, submission_queue=submission_queue, clock=clock)


def _live_challenge_or_404(db: Session, challenge_id: uuid.UUID) -> models.Challenge:
    challenge = (
        db.query(models.Challenge)
        .filter(models.Challenge.id == challenge_id, models.Challenge.deleted_at.is_(None))
        .one_or_none()
    )
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return challenge


@router.post(
    "/create-submission",
    response_model=SubmissionCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit to a challenge",
    responses={
        404: {"description": "No live Challenge has that id."},
        422: {"description": "Invalid Submission: ``errors`` lists each problem; nothing was stored."},
        429: {
            "model": DailyLimitResponse,
            "description": "Daily limit reached on this Challenge; ``reset_at`` is the next 00:00 UTC. Nothing was stored.",
        },
    },
)
def create_submission(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    intake: SubmissionIntake = Depends(submission_intake),
    clock: Clock = Depends(get_clock),
    challenge_id: uuid.UUID = Form(..., description="ID of the Challenge submitted to"),
    form: SubmissionForm = Depends(submission_form),
) -> Union[SubmissionCreatedResponse, JSONResponse]:
    """
    Submit to a Challenge (signed-in users). Every check runs before anything
    is stored; on any problem the answer is 422 with every error. A valid
    Submission is stored ``pending``, Locks the Challenge if it is the first,
    and is scored in the background with the Challenge's Scorer: poll
    ``GET /submissions/{id}/status``.
    """
    challenge = _live_challenge_or_404(db, challenge_id)
    try:
        submission = intake.submit(challenge, current_user, form)
    except DailyLimitReached as exc:
        return _daily_limit_response(exc, clock())
    except SubmissionRejected as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "The Submission is invalid.", "errors": [e.to_dict() for e in exc.errors]},
        )
    return SubmissionCreatedResponse(
        id=submission.id, status=submission.status.value, message="Submission accepted; scoring has been queued."
    )


def _daily_limit_response(exc: DailyLimitReached, now: datetime.datetime) -> JSONResponse:
    """429 with the reset time as ISO 8601 UTC, and Retry-After in seconds."""
    retry_after = max(0, math.ceil((exc.reset_at - now).total_seconds()))
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content=DailyLimitResponse(detail=str(exc), reset_at=f"{exc.reset_at:%Y-%m-%dT%H:%M:%S}Z").model_dump(),
        headers={"Retry-After": str(retry_after)},
    )

# ******** Monitoring endpoints ********
@router.get("/cache/stats")
async def get_submission_cache_stats():
    """
    Get submission cache statistics for monitoring.
    Shows cache performance and current active submissions.
    """
    return get_cache_stats()

@router.get("/queue/stats")
async def get_submission_queue_stats():
    """
    Get submission queue statistics for monitoring.
    Shows queue size, worker status, and processing statistics.
    """
    return get_queue_stats()

@router.get("/system/stats")
async def get_submission_system_stats():
    """
    Get combined system statistics for submission processing.
    Includes both cache and queue metrics.
    """
    return {
        "cache": get_cache_stats(),
        "queue": get_queue_stats(),
        "system": "queue-based-workers"
    }
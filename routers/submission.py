from fastapi import APIRouter, status, HTTPException, Depends, Path, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import Annotated, List, Optional
from sqlalchemy.orm import Session
import models
from models.user import User
from database import get_db
from schemas.submission import SubmissionRead, SubmissionCreatedResponse, SubmissionStatusResponse
from ports.clock import Clock, get_clock
from ports.object_storage import ObjectStorage, get_object_storage
from service.submission_intake import SubmissionForm, SubmissionIntake, SubmissionRejected
from submission_worker import SubmissionQueue, get_queue_stats, get_submission_queue
from submission_cache import get_cache_stats
from auth import get_current_active_user
import uuid

# Constants for repeated strings
SUBMISSION_ID_PATH_DESCRIPTION = "This is the ID of the submission"
SUBMISSION_NOT_FOUND_MESSAGE = "Submission not found"

router = APIRouter(prefix="/submissions", tags=["submissions"])

db_dependency = Annotated[Session, Depends(get_db)]

# ******** basic endpoints ********
@router.get("", response_model=List[SubmissionRead])
async def list_all_submissions(db: db_dependency):
    """List all submissions (admin only - for now open to all)."""
    try:
        return db.query(models.Submission).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/my", response_model=List[SubmissionRead])
async def list_my_submissions(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user)
):
    """List submissions created by the current authenticated user."""
    try:
        return db.query(models.Submission).filter(models.Submission.user_id == current_user.id).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{submission_id}", response_model=SubmissionRead)
async def get_submission(db: db_dependency, submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION)):
    submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)
    return submission

@router.get("/{submission_id}/status", response_model=SubmissionStatusResponse)
async def get_submission_status(
    db: db_dependency, 
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION)
):
    """
    Get where a Submission is in scoring, to poll after creating it.

    Status values:
    - pending: stored, waiting for a worker
    - processing: being scored
    - completed: scored; ``headline_metric`` and ``score_details`` are set
    - failed: ``status_message`` says why
    """
    submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)
    return submission



@router.delete("/{submission_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_submission(
    db: db_dependency, 
    current_user: User = Depends(get_current_active_user),
    submission_id: uuid.UUID = Path(..., description=SUBMISSION_ID_PATH_DESCRIPTION)
):
    """
    Delete a submission from the leaderboard.
    
    Authorization rules:
    - Admin users can delete any submission
    - Non-admin users can only delete their own submissions
    """
    try:
        submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
        if not submission:
            raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)
        
        # Check authorization: admin can delete any submission, non-admin can only delete their own
        if current_user.role != 'admin' and submission.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You can only delete your own submissions")
        
        # Database CASCADE will automatically delete related results
        db.delete(submission)
        db.commit()
        
        return {"message": "Submission and related records deleted successfully"}
    
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        db.rollback()
        # Return detailed error information
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete submission {submission_id}: {str(e)}"
        )

# ******** basic endpoints ********


# ******** create a submission ********
@router.post(
    "/create-submission",
    response_model=SubmissionCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit to a challenge",
    responses={
        404: {"description": "No live Challenge has that id."},
        422: {"description": "Invalid Submission: ``errors`` lists each problem; nothing was stored."},
    },
)
def create_submission(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    storage: ObjectStorage = Depends(get_object_storage),
    submission_queue: SubmissionQueue = Depends(get_submission_queue),
    clock: Clock = Depends(get_clock),
    challenge_id: uuid.UUID = Form(..., description="ID of the Challenge submitted to"),
    submission_type: str = Form(..., alias="type", description="Submission Type: model (prompt and skill coming)"),
    name: str = Form("", description="Name of the Submission; for a Model, the model name (reused or created)"),
    description: str = Form("", description="Description of the Submission"),
    model_link: Optional[str] = Form(None, description="Model only: optional https link to the model"),
    predictions_file: UploadFile = File(..., description="Predictions File: JSONL {id, prediction} rows, one per Input id"),
):
    """
    Submit to a Challenge (signed-in users). Every check runs before anything
    is stored; on any problem the answer is 422 with every error. A valid
    Submission is stored ``pending``, Locks the Challenge if it is the first,
    and is scored in the background with the Challenge's Scorer: poll
    ``GET /submissions/{id}/status``.
    """
    challenge = (
        db.query(models.Challenge)
        .filter(models.Challenge.id == challenge_id, models.Challenge.deleted_at.is_(None))
        .one_or_none()
    )
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    form = SubmissionForm(
        type=submission_type,
        name=name,
        description=description,
        model_link=model_link,
        predictions=predictions_file.file.read(),
    )
    intake = SubmissionIntake(db=db, storage=storage, submission_queue=submission_queue, clock=clock)
    try:
        submission = intake.submit(challenge, current_user, form)
    except SubmissionRejected as exc:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": "The Submission is invalid.", "errors": [e.to_dict() for e in exc.errors]},
        )
    return SubmissionCreatedResponse(
        id=submission.id, status=submission.status.value, message="Submission accepted; scoring has been queued."
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
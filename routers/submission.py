from fastapi import APIRouter, status, HTTPException, Depends, Path, Body, UploadFile, File, Form
from typing import Annotated, List
from sqlalchemy.orm import Session
import models
from models.user import User
from models.submission import SubmissionStatus
from database import get_db
from schemas.submission import SubmissionCreate, SubmissionRead, SubmissionCreatedResponse, SubmissionStatusResponse
from CRUD.model import create_or_get_model
from submission_worker import get_queue_stats
from submission_cache import (
    get_submission_progress, 
    set_submission_progress, 
    get_cache_stats,
    CacheStatus
)
import logging
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
    Get the current processing status of a submission.
    
    Use this endpoint to track the progress of your submission after creation.
    This endpoint checks the fast in-memory cache first for instant responses,
    then falls back to the database if needed.
    
    Status values:
    - pending: Submission created, waiting to start processing
    - processing: File upload and evaluation in progress  
    - completed: All processing completed successfully
    - failed: Processing failed, check status_message for details
    """
    submission_id_str = str(submission_id)
    
    # First, try to get status from fast cache
    cached_progress = get_submission_progress(submission_id_str)
    if cached_progress:
        # Cache hit - return instantly without database query
        logging.info(f"Fast cache hit for submission {submission_id_str}")
        
        # Map cache status to schema status
        cache_to_schema_status = {
            CacheStatus.PENDING: SubmissionStatus.PENDING,
            CacheStatus.PROCESSING: SubmissionStatus.PROCESSING,
            CacheStatus.UPLOADING: SubmissionStatus.PROCESSING,  # Map uploading to processing
            CacheStatus.VALIDATING: SubmissionStatus.PROCESSING,  # Map validating to processing
            CacheStatus.EVALUATING: SubmissionStatus.PROCESSING,  # Map evaluating to processing
            CacheStatus.COMPLETED: SubmissionStatus.COMPLETED,
            CacheStatus.FAILED: SubmissionStatus.FAILED
        }
        
        schema_status = cache_to_schema_status.get(cached_progress.status, SubmissionStatus.PROCESSING)
        
        return SubmissionStatusResponse(
            id=submission_id,
            status=schema_status,
            status_message=cached_progress.message,
            progress_percentage=cached_progress.progress_percentage,
            current_step=cached_progress.step,
            error_details=cached_progress.error_details,
            created_at=None,  # We don't store timestamps in cache for speed
            updated_at=None
        )
    
    # Cache miss - fall back to database
    logging.info(f"Cache miss for submission {submission_id_str}, querying database")
    submission = db.query(models.Submission).filter(models.Submission.id == submission_id).first()
    if not submission:
        raise HTTPException(status_code=404, detail=SUBMISSION_NOT_FOUND_MESSAGE)
    
    # For queue-based system, we rely on cache and database sync
    # No need to check individual task status since workers manage their own state
    
    return SubmissionStatusResponse(
        id=submission.id,
        status=submission.status,
        status_message=submission.status_message,
        progress_percentage=None,  # Database doesn't store progress percentage
        current_step=None,  # Database doesn't store current step
        error_details=None,  # Database doesn't store detailed errors
        created_at=submission.created_at,
        updated_at=submission.updated_at
    )



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


# ******** upload json file ********
@router.post("/create-submission", response_model=SubmissionCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_submission(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    file: UploadFile = File(..., description="JSON file containing inference results with 'filename' and 'prediction' columns"),
    model_name: str = Form(..., description="Name of the model used for inference (you can add new model name and it will create in our model table only if the submission file passes the validation.)"),
    challenge_id: uuid.UUID = Form(..., description="ID of the challenge for evaluation (basically holds the ground truth file)"),
    description: str = Form(..., description="Description of the submission (will come from front end)")
):
    """
    Legacy EvalAI-style submission scored with CER/WER against a ground-truth
    URL. Challenges no longer have that URL: their Ground Truth is a private
    storage key scored through a Scorer. Submitting returns 501 until RFC 2
    (Challenge Submissions) rebuilds this endpoint.
    """
    raise HTTPException(
        status_code=501,
        detail="Submitting to a Challenge is not supported until RFC 2 (Challenge Submissions).",
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
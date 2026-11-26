import uuid
import datetime
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional
from enum import Enum

from models.challenge import SyncStatus
from models.submission import SubmissionType

SUBMISSION_ID_DESCRIPTION = "ID of the submission"
HEADLINE_METRIC_DESCRIPTION = "The Scorer's Headline Metric, unrounded; set once completed"
REPO_PATH_DESCRIPTION = "The Submission's folder in the Challenge Repo, once synced"
SYNC_STATUS_DESCRIPTION = "Whether the folder is in the Challenge Repo: pending, synced or failed"

class SubmissionStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class SubmissionRead(BaseModel):
    """Public view of a Submission. The storage keys of the Predictions File
    and the Artifact are internals and not part of it: Predictions are never
    published, and the Artifact is published through the Challenge Repo."""

    id: uuid.UUID = Field(..., description=SUBMISSION_ID_DESCRIPTION)
    user_id: str = Field(..., description="ID of the user (Auth0 user ID)")
    challenge_id: uuid.UUID = Field(..., description="ID of the challenge")
    type: SubmissionType = Field(..., description="Submission Type: model, prompt or skill")
    name: str = Field(..., description="Name of the Submission; for a Model, the model name")
    description: Optional[str] = Field(None, description="Description of the submission")
    model_id: Optional[uuid.UUID] = Field(None, description="The model, for Model Submissions")
    model_link: Optional[str] = Field(None, description="Optional https link to the model")
    preferred_model: Optional[str] = Field(
        None, description="Prompt and Skill: the exact model id the Artifact was run with"
    )
    license: Optional[str] = Field(None, description="Prompt and Skill: the Artifact's license")
    status: SubmissionStatus = Field(..., description="pending, processing, completed (scored) or failed")
    status_message: Optional[str] = Field(None, description="The error, once failed")
    headline_metric: Optional[float] = Field(None, description=HEADLINE_METRIC_DESCRIPTION)
    score_details: Optional[Dict[str, Any]] = Field(None, description="Everything else the Scorer returned")
    repo_path: Optional[str] = Field(None, description=REPO_PATH_DESCRIPTION)
    sync_status: SyncStatus = Field(..., description=SYNC_STATUS_DESCRIPTION)
    created_at: datetime.datetime = Field(..., description="Creation timestamp of the submission")
    updated_at: datetime.datetime = Field(..., description="Last update timestamp of the submission")

    class Config:
        from_attributes = True

class SubmissionCreatedResponse(BaseModel):
    """Response schema for immediate submission creation"""
    id: uuid.UUID = Field(..., description=SUBMISSION_ID_DESCRIPTION)
    status: SubmissionStatus = Field(..., description="Current status of the submission")
    message: str = Field(..., description="Status message")

    class Config:
        from_attributes = True

class DailyLimitResponse(BaseModel):
    """Answer when the daily Submission limit on a Challenge is reached."""
    detail: str = Field(..., description="Message saying the limit is reached and when it resets")
    reset_at: str = Field(..., description="When the limit resets: the next 00:00 UTC, ISO 8601 with Z")

class SubmissionStatusResponse(BaseModel):
    """Where a Submission is in scoring, and its result once scored."""
    id: uuid.UUID = Field(..., description=SUBMISSION_ID_DESCRIPTION)
    status: SubmissionStatus = Field(..., description="Current status of the submission")
    status_message: Optional[str] = Field(None, description="The error, once failed")
    headline_metric: Optional[float] = Field(None, description=HEADLINE_METRIC_DESCRIPTION)
    score_details: Optional[Dict[str, Any]] = Field(None, description="Everything else the Scorer returned")
    repo_path: Optional[str] = Field(None, description=REPO_PATH_DESCRIPTION)
    sync_status: SyncStatus = Field(..., description=SYNC_STATUS_DESCRIPTION)
    created_at: Optional[datetime.datetime] = Field(None, description="Creation timestamp")
    updated_at: Optional[datetime.datetime] = Field(None, description="Last update timestamp")

    class Config:
        from_attributes = True

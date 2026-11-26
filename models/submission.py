from sqlalchemy import Column, String, DateTime, Float, ForeignKey, Enum, JSON, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from database import Base
from models.challenge import SyncStatus, _one_of
import datetime
import uuid
import enum

class SubmissionStatus(enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SubmissionType(str, enum.Enum):
    """What a Submission's Artifact is (see GLOSSARY.md)."""

    MODEL = "model"
    PROMPT = "prompt"
    SKILL = "skill"


class Submission(Base):
    """One entry on a Challenge: a Predictions File scored by the Challenge's
    Scorer, with the Artifact that produced it (see GLOSSARY.md)."""
    __tablename__ = "submission"
    __table_args__ = (
        CheckConstraint(_one_of("type", SubmissionType), name="ck_submission_type"),
        CheckConstraint(_one_of("sync_status", SyncStatus), name="ck_submission_sync_status"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, unique=True, nullable=False)
    user_id = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    challenge_id = Column(UUID(as_uuid=True), ForeignKey("challenge.id"), nullable=False, index=True)
    type = Column(String, nullable=False)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    # Set only for Model Submissions: the model name, shared across Challenges.
    model_id = Column(UUID(as_uuid=True), ForeignKey("model.id"), nullable=True, index=True)
    model_link = Column(String, nullable=True)
    # Prompt and Skill only. A free-text model id, deliberately not a reference
    # to a model: it may name a model the arena has never seen.
    preferred_model = Column(String, nullable=True)
    license = Column(String, nullable=True)
    # Private object storage key of the prompt (markdown) or skill zip.
    artifact_url = Column(String, nullable=True)
    # Private object storage key; Predictions are never published.
    predictions_url = Column(String, nullable=False)
    status = Column(Enum(SubmissionStatus), nullable=False, default=SubmissionStatus.PENDING)
    status_message = Column(String, nullable=True)  # For error messages or progress info
    # Unrounded, so ranking ties are broken by the real value.
    headline_metric = Column(Float, nullable=True)
    score_details = Column(JSON, nullable=True)
    # Challenge Repo: the Submission's folder, set once it is published.
    # Its sync rides on the Challenge's (see ``sync_worker``).
    repo_path = Column(String, nullable=True)
    sync_status = Column(
        String, nullable=False, default=SyncStatus.PENDING.value, server_default=SyncStatus.PENDING.value
    )
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    def mark_synced(self, repo_path: str) -> None:
        """The Submission's folder is in the Challenge Repo at ``repo_path``."""
        self.sync_status = SyncStatus.SYNCED
        self.repo_path = repo_path

    def queue_sync(self) -> None:
        """Wait for the next sync of the Challenge to publish the folder."""
        self.sync_status = SyncStatus.PENDING

    def record_failed_sync(self) -> None:
        """Its folder could not be published: its Challenge's sync ran out of
        attempts, or its Artifact is missing from storage."""
        self.sync_status = SyncStatus.FAILED

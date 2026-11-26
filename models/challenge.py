from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Integer, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from database import Base
from models.columns import one_of
import datetime
import enum
import uuid
from sqlalchemy.orm import relationship


class SyncStatus(str, enum.Enum):
    """Whether the Challenge Repo matches the arena (see ``sync_worker``)."""

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"


class Direction(str, enum.Enum):
    """Which way a Scorer's scores rank (see GLOSSARY.md)."""

    LOWER_IS_BETTER = "lower_is_better"
    HIGHER_IS_BETTER = "higher_is_better"


class Challenge(Base):
    """
    A task with public Inputs, private Ground Truth and one locked Scorer,
    ranked on a leaderboard (see GLOSSARY.md). Not an Arena Challenge.
    """
    __tablename__ = "challenge"
    __table_args__ = (
        CheckConstraint(one_of("sync_status", SyncStatus), name="ck_challenge_sync_status"),
        CheckConstraint(one_of("direction", Direction), name="ck_challenge_direction"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, unique=True, nullable=False)
    # Unique so that repo names derived from it never collide.
    title = Column(String, nullable=False, unique=True)
    image_uri = Column(String, nullable=True)
    category_id = Column(UUID(as_uuid=True), ForeignKey("category.id"), nullable=False, index=True)
    created_by = Column(String, ForeignKey("user.id"), nullable=False, index=True)
    description = Column(String, nullable=True)
    status = Column(String, nullable=True)

    # Scorer from buddhist-eval's registry. The name always means the same
    # computation (ADR 0001); package_version is recorded for audit only.
    scorer_name = Column(String, nullable=False)
    scorer_params = Column(JSON, nullable=False, default=dict)
    direction = Column(String, nullable=False)
    scorer_label = Column(String, nullable=False)
    package_version = Column(String, nullable=False)

    # Object storage keys. Inputs and Media Archive are under the public
    # prefix; Ground Truth is under the private prefix and never published.
    inputs_key = Column(String, nullable=False)
    media_archive_key = Column(String, nullable=True)
    ground_truth_key = Column(String, nullable=False)

    # Challenge Repo: `challenge-<slug-of-title>`, fixed at creation.
    repo_name = Column(String, nullable=False, unique=True)
    sync_status = Column(
        String, nullable=False, default=SyncStatus.PENDING.value, server_default=SyncStatus.PENDING.value
    )
    sync_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_sync_error = Column(String, nullable=True)

    # Set when the first Submission arrives (RFC 2); the Lock.
    locked_at = Column(DateTime, nullable=True)

    # Set when an admin deletes the Challenge. The row stays as a tombstone:
    # the Sync Worker archives its repo, and its repo name is never reused.
    # A deleted Challenge is gone from the arena's API.
    deleted_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    # Relationship to Category
    category = relationship("Category", back_populates="challenges")

    def apply_scorer(self, scorer) -> None:
        """Store a ``service.challenge_data.ValidScorer``: the Scorer, its
        parameters and the buddhist-eval version they were validated with."""
        self.scorer_name = scorer.name
        self.scorer_label = scorer.label
        self.direction = scorer.direction
        self.scorer_params = scorer.params
        self.package_version = scorer.package_version

    def queue_sync(self) -> None:
        """Leave the Challenge to the Sync Worker with a fresh round of
        attempts: after a create, an edit, a delete or a Resync."""
        self.sync_status = SyncStatus.PENDING
        self.sync_attempts = 0
        self.last_sync_error = None

    def mark_synced(self) -> None:
        """The repo now matches the arena (or, once deleted, is archived)."""
        self.sync_status = SyncStatus.SYNCED
        self.sync_attempts = 0
        self.last_sync_error = None

    def record_failed_sync(self, error: str, at: datetime.datetime, max_attempts: int) -> None:
        """Count a failed attempt made ``at``; after ``max_attempts`` in a row
        the Challenge is ``failed`` and waits for a Resync."""
        self.sync_attempts = (self.sync_attempts or 0) + 1
        self.last_sync_error = error
        self.updated_at = at
        if self.sync_attempts >= max_attempts:
            self.sync_status = SyncStatus.FAILED

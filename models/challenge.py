from sqlalchemy import Column, String, DateTime, JSON, ForeignKey, Integer, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from database import Base
import datetime
import uuid
from sqlalchemy.orm import relationship

SYNC_PENDING = "pending"
SYNC_SYNCED = "synced"
SYNC_FAILED = "failed"
SYNC_STATUSES = (SYNC_PENDING, SYNC_SYNCED, SYNC_FAILED)

DIRECTIONS = ("lower_is_better", "higher_is_better")


class Challenge(Base):
    """
    A task with public Inputs, private Ground Truth and one locked Scorer,
    ranked on a leaderboard (see GLOSSARY.md). Not an Arena Challenge.
    """
    __tablename__ = "challenge"
    __table_args__ = (
        CheckConstraint(
            "sync_status IN ('pending', 'synced', 'failed')", name="ck_challenge_sync_status"
        ),
        CheckConstraint(
            "direction IN ('lower_is_better', 'higher_is_better')", name="ck_challenge_direction"
        ),
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
    sync_status = Column(String, nullable=False, default=SYNC_PENDING, server_default=SYNC_PENDING)
    sync_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    last_sync_error = Column(String, nullable=True)

    # Set when the first Submission arrives (RFC 2); the Lock.
    locked_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc))
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc), onupdate=lambda: datetime.datetime.now(datetime.timezone.utc))

    # Relationship to Category
    category = relationship("Category", back_populates="challenges")

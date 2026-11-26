import uuid
import datetime
from pydantic import BaseModel, computed_field
from typing import Any, Dict, Optional
from models.challenge import Direction, SyncStatus
from schemas.category import CategoryRead


class ChallengeRead(BaseModel):
    """Public view of a Challenge. Storage keys and the last sync error are
    internals and not part of it: participants get the public links from the
    repo's ``challenge.json``. Admins see them in ``ChallengeAdminRead``."""

    id: uuid.UUID
    title: str
    image_uri: Optional[str] = None
    category_id: uuid.UUID
    created_by: str  # Auth0 user ID
    description: Optional[str] = None
    status: Optional[str] = None

    scorer_name: str
    scorer_params: Dict[str, Any]
    direction: Direction
    scorer_label: str
    package_version: str

    repo_name: str
    sync_status: SyncStatus
    sync_attempts: int
    locked_at: Optional[datetime.datetime] = None

    @computed_field
    @property
    def locked(self) -> bool:
        """Locked once the first Submission arrived: the Scorer, its parameters
        and the data can no longer change."""
        return self.locked_at is not None

    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


class ChallengeWithCategoryRead(ChallengeRead):
    category: CategoryRead

    class Config:
        from_attributes = True


class ChallengeAdminRead(ChallengeWithCategoryRead):
    """Admin view of a Challenge, deleted ones included: the public view plus
    the storage keys, the last sync error and the deletion time."""

    inputs_key: str
    media_archive_key: Optional[str] = None
    ground_truth_key: str
    last_sync_error: Optional[str] = None
    deleted_at: Optional[datetime.datetime] = None

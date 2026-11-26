import uuid
import datetime
from pydantic import BaseModel
from typing import Any, Dict, Optional
from schemas.category import CategoryRead


class ChallengeRead(BaseModel):
    """Public view of a Challenge. The Ground Truth storage key is private and
    deliberately not part of it."""

    id: uuid.UUID
    title: str
    image_uri: Optional[str] = None
    category_id: uuid.UUID
    created_by: str  # Auth0 user ID
    description: Optional[str] = None
    status: Optional[str] = None

    scorer_name: str
    scorer_params: Dict[str, Any]
    direction: str
    scorer_label: str
    package_version: str

    inputs_key: str
    media_archive_key: Optional[str] = None

    repo_name: str
    sync_status: str
    sync_attempts: int
    last_sync_error: Optional[str] = None
    locked_at: Optional[datetime.datetime] = None

    created_at: datetime.datetime
    updated_at: datetime.datetime

    class Config:
        from_attributes = True


class ChallengeWithCategoryRead(ChallengeRead):
    category: CategoryRead

    class Config:
        from_attributes = True

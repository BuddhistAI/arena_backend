from typing import Any, Dict

from pydantic import BaseModel

from models.challenge import Direction


class ScorerRead(BaseModel):
    """One Scorer as the challenge form needs it."""

    name: str
    label: str
    direction: Direction
    parameter_schema: Dict[str, Any]

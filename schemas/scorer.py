from typing import Any, Dict, Literal

from pydantic import BaseModel


class ScorerRead(BaseModel):
    """One Scorer as the challenge form needs it."""

    name: str
    label: str
    direction: Literal["lower_is_better", "higher_is_better"]
    parameter_schema: Dict[str, Any]

from typing import Any, Dict, List

from pydantic import BaseModel

from models.challenge import Direction
from service.scorer_catalogue import DisplayStyle


class DisplayFormat(BaseModel):
    """How a number is shown: as a percent or a decimal, with this many decimals."""

    style: DisplayStyle
    decimals: int


class PublicSummaryEntry(BaseModel):
    """One aggregate number a Scorer lets the public see: its key in the details, label and format."""

    key: str
    label: str
    format: DisplayFormat


class PublicSummaryValue(PublicSummaryEntry):
    """One Public Summary entry of a scored Submission, with its value."""

    value: float


class ScorerRead(BaseModel):
    """One Scorer as the challenge form and the Leaderboard need it."""

    name: str
    label: str
    direction: Direction
    parameter_schema: Dict[str, Any]
    display_format: DisplayFormat
    public_summary: List[PublicSummaryEntry]

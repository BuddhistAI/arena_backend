from typing import Any, Dict, List, Literal

from pydantic import BaseModel

from models.challenge import Direction


class DisplayFormat(BaseModel):
    """How a number is shown: as a percent or a decimal, with this many decimals."""

    style: Literal["percent", "decimal"]
    decimals: int


class PublicSummaryEntry(BaseModel):
    """One aggregate number a Scorer lets the public see: its key in the details, label and format."""

    key: str
    label: str
    format: DisplayFormat


class ScorerRead(BaseModel):
    """One Scorer as the challenge form and the Leaderboard need it."""

    name: str
    label: str
    direction: Direction
    parameter_schema: Dict[str, Any]
    display_format: DisplayFormat
    public_summary: List[PublicSummaryEntry]

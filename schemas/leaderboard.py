import datetime
import uuid
from typing import List, Optional

from pydantic import BaseModel, Field

from models.challenge import Direction
from models.submission import SubmissionType
from schemas.scorer import DisplayFormat, PublicSummaryEntry


class LeaderboardRowRead(BaseModel):
    """One ranked Submission on a Leaderboard."""

    submission_id: uuid.UUID
    rank: int = Field(..., description="Rank by Headline Metric in the Scorer's Direction, the earlier Submission first on a tie; unique per row")
    type: SubmissionType = Field(..., description="Submission Type: model, prompt or skill")
    name: str = Field(..., description="Name of the Submission; for a Model, the model name")
    submitter: str = Field(..., description="The submitter's current public username")
    created_at: datetime.datetime = Field(..., description="When the Submission was made")
    headline_metric: float = Field(..., description="The Scorer's Headline Metric, unrounded")
    preferred_model: Optional[str] = Field(
        None, description="Prompt and Skill: the model it was run with; null for a Model"
    )
    has_download: bool = Field(..., description="Whether the Artifact can be downloaded (Prompt and Skill)")

    class Config:
        from_attributes = True


class LeaderboardScorerRead(BaseModel):
    """The Scorer a Leaderboard is ranked by, and how to show its numbers."""

    name: str
    label: str = Field(..., description="Heads the Headline Metric's column")
    direction: Direction
    format: DisplayFormat = Field(..., description="How to show the Headline Metric")
    public_summary: List[PublicSummaryEntry] = Field(
        ..., description="The aggregate numbers a Submission's detail shows, in order"
    )

    class Config:
        from_attributes = True


class LeaderboardCounts(BaseModel):
    """How many rows the whole Leaderboard holds, whatever the type filter,
    search and page."""

    all: int
    model: int
    prompt: int
    skill: int


class LeaderboardRead(BaseModel):
    """One page of a Challenge's Leaderboard."""

    scorer: LeaderboardScorerRead
    counts: LeaderboardCounts
    total: int = Field(
        ..., description="Rows the type filter and search show, on all pages together; the last page holds the last"
    )
    page: int = Field(..., description="This page's number, from 1")
    limit: int = Field(..., description="Rows per page, after the cap")
    rows: List[LeaderboardRowRead]

    class Config:
        from_attributes = True

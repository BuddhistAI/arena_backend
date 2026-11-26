"""Leaderboard: a Challenge's ranked, scored Submissions, for anyone to read."""

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from database import get_db
from models.submission import SubmissionType
from schemas.leaderboard import LeaderboardRead
from service.leaderboard import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, ChallengeNotFound, Leaderboard, read_leaderboard

router = APIRouter(prefix="/challenges", tags=["leaderboard"])


@router.get("/{challenge_id}/leaderboard", response_model=LeaderboardRead)
def get_leaderboard(
    db: Annotated[Session, Depends(get_db)],
    challenge_id: uuid.UUID = Path(..., description="The Challenge whose Leaderboard to read"),
    submission_type: Optional[SubmissionType] = Query(
        None, alias="type", description="Show only this Submission Type; omit for all. Ranks are unchanged"
    ),
    q: Optional[str] = Query(
        None, description="Search text: matches name, submitter or Preferred Model, ignoring case"
    ),
    page: int = Query(1, ge=1, description="Which page, from 1"),
    limit: int = Query(
        DEFAULT_PAGE_SIZE, ge=1, description=f"Rows per page; at most {MAX_PAGE_SIZE} are returned"
    ),
) -> Leaderboard:
    """The Challenge's scored Submissions, ranked by Headline Metric in the
    Scorer's Direction, the earlier Submission first on a tie."""
    try:
        return read_leaderboard(db, challenge_id, submission_type=submission_type, search=q, page=page, limit=limit)
    except ChallengeNotFound:
        raise HTTPException(status_code=404, detail="Challenge not found")

"""List Scorers: what the challenge form's Scorer dropdown is filled from."""

from typing import List

from fastapi import APIRouter, Depends, status

from auth import get_current_active_user
from schemas.scorer import ScorerRead
from service import scorer_catalogue

router = APIRouter(prefix="/scorers", tags=["scorers"])


@router.get(
    "",
    response_model=List[ScorerRead],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(get_current_active_user)],
)
def list_scorers():
    """Every Scorer with its name, label, Direction, parameter JSON schema (with defaults),
    Headline Metric display format and Public Summary declaration."""
    return scorer_catalogue.list_scorers()

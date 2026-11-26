from fastapi import APIRouter, status, HTTPException, Depends, Path, Form
from typing import Annotated, List, Optional
from sqlalchemy.orm import Session
import models
from models import Challenge
from models.user import User
from database import get_db
from schemas.challenge import ChallengeRead, ChallengeWithCategoryRead
from uuid import UUID
from sqlalchemy.orm import joinedload
from auth import get_current_active_user
import logging
from datetime import datetime

# Constants
CHALLENGE_NOT_FOUND_MESSAGE = "Challenge not found"

router = APIRouter(prefix="/challenges", tags=["challenges"])

db_dependency = Annotated[Session, Depends(get_db)]


# for listing all challenges

@router.get("/list", response_model=List[ChallengeWithCategoryRead])
async def list_challenges_with_category(db: db_dependency):
    try:
        # Eager load the category relationship
        challenges = db.query(Challenge).options(joinedload(Challenge.category)).all()
        return challenges
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# for getting a specific challenge

@router.get("/{challenge_id}", response_model=ChallengeRead)
async def get_challenge(db: db_dependency, challenge_id: UUID = Path(..., description="This is the ID of the challenge")):
    challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
    if not challenge:
        raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
    return challenge

# Creating a Challenge is being rebuilt around Scorers, Inputs and a private
# Ground Truth (ticket 04). The legacy EvalAI-shaped create is retired.

@router.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    summary="Create a new challenge",
)
async def create_new_challenge(current_user: User = Depends(get_current_active_user)):
    raise HTTPException(
        status_code=501,
        detail="Creating a Challenge is not supported until the Scorer-based create endpoint ships.",
    )

# for updating a challenge

@router.patch("/update/{challenge_id}", response_model=ChallengeRead, status_code=status.HTTP_200_OK)
async def update_challenge(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to update"),
    title: Optional[str] = Form(None, description="Challenge title"),
    category_id: Optional[str] = Form(None, description="Category ID"),
    image_uri: Optional[str] = Form(None, description="Challenge image URL"),
    description: Optional[str] = Form(None, description="Challenge description"),
    status: Optional[str] = Form(None, description="Challenge status"),
):
    """
    Update an existing challenge's presentation fields. Fields are optional
    and only updated if provided. Data and Scorer edits come with ticket 05.
    """
    try:
        logging.info(f"Updating challenge: {challenge_id}")
        
        # Find existing challenge
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
        
        # Convert empty strings to None for all optional fields to preserve existing data
        if title == "":
            title = None
        if category_id == "":
            category_id = None
        elif category_id is not None:
            try:
                category_id = UUID(category_id)  # Convert string to UUID
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid UUID format for category_id")
        if image_uri == "":
            image_uri = None
        if description == "":
            description = None
        if status == "":
            status = None
        
        # Update basic fields if provided
        if title is not None:
            challenge.title = title
        if category_id is not None:
            challenge.category_id = category_id
        if image_uri is not None:
            challenge.image_uri = image_uri
        if description is not None:
            challenge.description = description
        if status is not None:
            challenge.status = status
        
        # Commit changes
        db.commit()
        db.refresh(challenge)
        
        logging.info(f"Challenge updated successfully: {challenge_id}")
        return challenge
        
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Unexpected error updating challenge {challenge_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# for deleting a challenge

@router.delete("/remove/{challenge_id}", status_code=status.HTTP_200_OK)
async def delete_challenge(
    db: db_dependency, 
    current_user: User = Depends(get_current_active_user),
    challenge_id: UUID = Path(..., description="This is the ID of the challenge to delete")
):
    """
    Delete a challenge by ID and return confirmation.
    
    Authorization rules:
    - Admin users can delete any challenge
    - Non-admin users can only delete challenges they created
    
    Returns a confirmation message with details about the deleted challenge.
    """
    try:
        logging.info(f"Attempting to delete challenge: {challenge_id}")
        
        challenge = db.query(Challenge).filter(Challenge.id == challenge_id).first()
        if not challenge:
            logging.warning(f"Challenge not found for deletion: {challenge_id}")
            raise HTTPException(status_code=404, detail=CHALLENGE_NOT_FOUND_MESSAGE)
        
        # Check authorization: admin can delete any challenge, non-admin can only delete their own
        if current_user.role != 'admin' and challenge.created_by != current_user.id:
            logging.warning(f"User {current_user.id} attempted to delete challenge {challenge_id} owned by {challenge.created_by}")
            raise HTTPException(status_code=403, detail="You can only delete challenges you created")
        
        # Check if there are submissions referencing this challenge
        related_submissions = db.query(models.Submission).filter(models.Submission.challenge_id == challenge_id).all()
        if related_submissions:
            submission_count = len(related_submissions)
            logging.warning(f"Cannot delete challenge {challenge_id}: {submission_count} submissions reference it")
            raise HTTPException(
                status_code=400, 
                detail=f"Cannot delete challenge. There are {submission_count} submission(s) that reference this challenge. Please delete all related submissions first."
            )
        
        challenge_title = challenge.title
        challenge_created_at = challenge.created_at
        
        db.delete(challenge)
        db.commit()
        
        logging.info(f"Challenge deleted successfully: {challenge_id} - {challenge_title}")
        
        return {
            "message": "Challenge deleted successfully",
            "deleted_challenge": {
                "id": str(challenge_id),
                "title": challenge_title,
                "created_at": challenge_created_at.isoformat(),
                "deleted_at": datetime.now().isoformat()
            },
            "status": "success"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logging.error(f"Unexpected error deleting challenge {challenge_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

from fastapi import APIRouter, status, HTTPException, Depends, Path
from typing import Annotated, List
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from models.user import User
from models.challenge import Challenge
from database import get_db
from schemas.user import UserCreate, UserRead, UserUpdate, UserResponse
from fastapi import Body
from auth import get_current_active_user

router = APIRouter(prefix="/users", tags=["users"])

db_dependency = Annotated[Session, Depends(get_db)]


@router.get("/me", response_model=UserResponse)
def read_users_me(db: db_dependency, current_user: User = Depends(get_current_active_user)):
    """Get current user info. Creates user if it doesn't exist."""
    # The get_current_active_user dependency already handles user creation
    # in auth.py via get_or_create_user_from_token, so current_user will
    # always be a valid User object (either existing or newly created)
    return current_user

# for listing all users

@router.get("", response_model=List[UserRead], status_code=status.HTTP_200_OK)
async def list_all_users(db: db_dependency):
    try:
        return db.query(User).all()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{user_id}", response_model=UserRead, status_code=status.HTTP_200_OK)
async def get_user(db: db_dependency, user_id: str = Path(..., description="This is the ID of the user")):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

# for creating new user

@router.post("/create", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_new_user(
    db: db_dependency, 
    current_user: User = Depends(get_current_active_user),
    user: UserCreate = Body(
        ..., 
        description="The user details for creating a new user.",
        example={
            "username": "john.doe",
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "picture": "http://example.com/pic.jpg",
            "role": "user"
        }
    )
):
    # User ID comes from the authenticated token, not the request body
    existing_user = db.query(User).filter(User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="User with this email already exists")
    try:
        # Create user with ID from token
        user_data = user.model_dump()
        user_data['id'] = current_user.id  # Use authenticated user's ID
        user_instance = User(**user_data)
        db.add(user_instance)
        db.commit()
        db.refresh(user_instance)
        return user_instance
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail="an error occurred while creating the user: " + str(e))

# for deleting user

@router.delete("/me", status_code=status.HTTP_200_OK)
async def delete_current_user(
    db: db_dependency, 
    current_user: User = Depends(get_current_active_user)
):
    """Delete the currently authenticated user."""
    db.delete(current_user)
    db.commit()
    return {"message": "User deleted successfully"}

# for updating user

USERNAME_TAKEN = "That username is taken."


def _username_taken(db: Session, username: str, *, by_other_than: User) -> bool:
    return (
        db.query(User.id).filter(User.username == username, User.id != by_other_than.id).first()
        is not None
    )


@router.patch("/me", response_model=UserRead, status_code=status.HTTP_200_OK)
async def update_current_user(
    db: db_dependency,
    current_user: User = Depends(get_current_active_user),
    user_update: UserUpdate = Body(..., description="The fields to update for the current user")
):
    """Update the currently authenticated user. A ``username`` another user
    already has answers 409."""
    update_data = user_update.model_dump(exclude_unset=True)
    username = update_data.get("username")
    if username is not None and _username_taken(db, username, by_other_than=current_user):
        raise HTTPException(status_code=409, detail=USERNAME_TAKEN)
    for field, value in update_data.items():
        setattr(current_user, field, value)
    try:
        db.commit()
    except IntegrityError:
        # Someone else took the username between the check and the commit.
        db.rollback()
        raise HTTPException(status_code=409, detail=USERNAME_TAKEN)
    db.refresh(current_user)
    return current_user


import datetime
import re
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional
from datetime import datetime


class UserBase(BaseModel):
    username: str = Field(..., description="Username derived from email")
    first_name: str = Field(..., description="First name of the user")
    last_name: str = Field(..., description="Last name of the user")
    email: str = Field(..., description="Email of the user")
    picture: Optional[str] = Field(None, description="Profile picture url (optional)")
    role: str = Field(default='user', description="Role of the user")

class UserBaseMinimal(BaseModel):
    username: str = Field(..., description="Username derived from email")
    email: str = Field(..., description="Email of the user")

class UserCreate(UserBase):
    pass  # ID comes from authenticated token, not request body

# A public username chosen in Settings: what Submissions are published under.
PUBLIC_USERNAME = re.compile(r"[a-z0-9_-]{3,30}")
INVALID_USERNAME = "Use 3 to 30 lowercase letters (a-z), digits, '-' or '_'."


class UserUpdate(BaseModel):
    """What users may change about themselves. Not their role, which grants
    admin rights, nor their email, which comes from sign-in: any field not
    listed here is refused with 422."""

    model_config = ConfigDict(extra="forbid")

    username: Optional[str] = Field(
        None, description="Public username: 3 to 30 lowercase ASCII letters, digits, '-' or '_'"
    )
    first_name: Optional[str] = Field(None, description="First name of the user")
    last_name: Optional[str] = Field(None, description="Last name of the user")
    picture: Optional[str] = Field(None, description="Profile picture url (optional)")

    @field_validator("username")
    @classmethod
    def _public_username(cls, username: Optional[str]) -> str:
        if username is None or not PUBLIC_USERNAME.fullmatch(username):
            raise ValueError(INVALID_USERNAME)
        return username

class UserRead(UserBase):
    id: str = Field(..., description="Auth0 user ID")
    created_at: datetime = Field(..., description="Creation timestamp of the user")
    updated_at: datetime = Field(..., description="Last update timestamp of the user")

    class Config:
        from_attributes = True

class UserResponse(UserBase):
    model_config = ConfigDict(from_attributes=True)
    
    id: str  # Auth0 user ID
    created_at: datetime
    updated_at: Optional[datetime] = None
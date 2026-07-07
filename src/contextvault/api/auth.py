"""Authentication endpoints."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.core.security import verify_password
from contextvault.core.tokens import create_access_token
from contextvault.db.session import get_session
from contextvault.services import users as user_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # If true the client must route the user to set a new password before use
    # (temp-password recovery flow).
    must_change_password: bool = False


@router.post("/login")
async def login(body: LoginRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    """Exchange username + password for a JWT access token."""
    user = await user_service.get_user_by_username(session, body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        # Same response for unknown user and wrong password — don't leak which.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(subject=str(user.id), role=user.role.value)
    return TokenResponse(access_token=token, must_change_password=user.must_change_password)

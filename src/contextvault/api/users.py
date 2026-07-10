"""User-management endpoints (admin) — account recovery (card #27, design spec §2).

An admin can issue a random temporary password for a user who has lost access. The
temp password is returned to the admin **once**, in plaintext; only its hash is
stored, and the user is forced to replace it on next login
(``must_change_password``), so the admin never learns the user's real password.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contextvault.api.deps import require_admin
from contextvault.db.session import get_session
from contextvault.models import User
from contextvault.services import users as user_service

router = APIRouter(prefix="/users", tags=["users"])


class ResetPasswordResponse(BaseModel):
    """A freshly issued temporary password (shown once) and the forced-change flag."""

    temporary_password: str
    must_change_password: bool


@router.post("/{user_id}/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    user_id: uuid.UUID,
    _: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ResetPasswordResponse:
    """Issue a random temporary password for a user; forces a change on next login."""
    user = await user_service.get_user_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    temporary = await user_service.reset_password(session, user)
    await session.commit()
    return ResetPasswordResponse(temporary_password=temporary, must_change_password=True)

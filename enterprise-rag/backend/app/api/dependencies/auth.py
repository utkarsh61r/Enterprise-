"""
Enterprise Knowledge Assistant - Auth Dependencies

FastAPI dependency injection for authentication and authorization.
Used in route handlers via Depends().
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps
from typing import Callable

import structlog
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.auth import decode_access_token, ROLE_HIERARCHY
from app.domain.models.all_models import User, UserRole
from app.infrastructure.database.session import get_db

logger = structlog.get_logger(__name__)
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    """Extracted and validated current user from JWT."""
    id: str
    email: str
    full_name: str
    role: str
    organization_id: str
    avatar_url: str | None
    department: str | None
    mfa_enabled: bool
    email_verified: bool
    created_at: object
    last_login_at: object | None
    is_active: bool


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    """
    FastAPI dependency that extracts and validates the current user from JWT.

    Raises 401 if token is missing, invalid, or expired.
    Raises 403 if user is deactivated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if not credentials:
        raise credentials_exception

    try:
        payload = decode_access_token(credentials.credentials)
    except JWTError:
        raise credentials_exception

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise credentials_exception

    # Load user from DB to check current status (not just token claims)
    result = await db.execute(
        select(User).where(User.id == user_id, User.is_deleted == False)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is deactivated. Contact your administrator.",
        )

    # Validate org matches token (defense against token reuse across tenants)
    if user.organization_id != payload.get("org"):
        logger.warning("Token org mismatch", user_id=user_id, token_org=payload.get("org"))
        raise credentials_exception

    return CurrentUser(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        organization_id=user.organization_id,
        avatar_url=user.avatar_url,
        department=user.department,
        mfa_enabled=user.mfa_enabled,
        email_verified=user.email_verified,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
        is_active=user.is_active,
    )


def require_role(minimum_role: str) -> Callable:
    """
    Dependency factory that enforces minimum role level.

    Usage:
        @router.delete("/users/{id}", dependencies=[Depends(require_role(UserRole.ADMIN))])
        async def delete_user(...):
            ...
    """
    async def check_role(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user_level = ROLE_HIERARCHY.get(current_user.role, -1)
        required_level = ROLE_HIERARCHY.get(minimum_role, 999)

        if user_level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required role: {minimum_role}",
            )
        return current_user

    return check_role


def require_admin() -> Callable:
    """Shorthand: require Admin or higher."""
    return require_role(UserRole.ADMIN)


def require_super_admin() -> Callable:
    """Shorthand: require Super Admin only."""
    return require_role(UserRole.SUPER_ADMIN)


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser | None:
    """Like get_current_user but returns None instead of raising if no token."""
    if not credentials:
        return None
    try:
        return await get_current_user(credentials, db)
    except HTTPException:
        return None


def same_organization(resource_org_id: str, current_user: CurrentUser) -> None:
    """
    Verify that a resource belongs to the current user's organization.

    Call this in handlers before returning any resource to enforce multi-tenancy.
    """
    if resource_org_id != current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",  # Don't leak existence of resources in other orgs
        )

"""
Enterprise Knowledge Assistant - Authentication API Router

Endpoints:
- POST /auth/register        - Create account
- POST /auth/login           - Email/password login
- POST /auth/logout          - Revoke session
- POST /auth/refresh         - Rotate refresh token
- POST /auth/forgot-password - Initiate password reset
- POST /auth/reset-password  - Complete password reset
- GET  /auth/me              - Current user profile
- POST /auth/verify-email    - Verify email address
- GET  /auth/oauth/google    - Google OAuth redirect
- GET  /auth/oauth/github    - GitHub OAuth redirect
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user, CurrentUser
from app.core.security.auth import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    is_account_locked,
    get_lockout_until,
    validate_password_strength,
    verify_password,
)
from app.core.config.settings import get_security_settings
from app.domain.models.all_models import (
    AuditLog, Organization, RefreshToken, User, UserRole, UserSession,
)
from app.infrastructure.database.session import get_db

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., min_length=2, max_length=255)
    organization_name: str = Field(..., min_length=2, max_length=255)

    @field_validator("password")
    @classmethod
    def validate_pw(cls, v: str) -> str:
        errors = validate_password_strength(v)
        if errors:
            raise ValueError("; ".join(errors))
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: dict


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(..., min_length=8)

    @field_validator("new_password")
    @classmethod
    def validate_pw(cls, v: str) -> str:
        errors = validate_password_strength(v)
        if errors:
            raise ValueError("; ".join(errors))
        return v


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _audit(
    db: AsyncSession,
    action: str,
    status: str,
    user_id: str | None = None,
    org_id: str | None = None,
    ip: str | None = None,
    details: dict | None = None,
) -> None:
    log = AuditLog(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        user_id=user_id,
        action=action,
        status=status,
        ip_address=ip,
        details=details or {},
    )
    db.add(log)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    request_body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Register a new user and organization.

    Each registration creates a new organization (tenant).
    The registering user becomes the organization's Admin.
    """
    ip = _get_client_ip(request)

    # Check email uniqueness (globally, across all orgs for security)
    existing = await db.execute(
        select(User).where(User.email == request_body.email)
    )
    if existing.scalar_one_or_none():
        # Return generic error to avoid email enumeration
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed. Please check your details.",
        )

    # Create organization
    org = Organization(
        id=str(uuid.uuid4()),
        name=request_body.organization_name,
        slug=request_body.organization_name.lower().replace(" ", "-")[:100] + "-" + str(uuid.uuid4())[:8],
    )
    db.add(org)
    await db.flush()

    # Create user
    user = User(
        id=str(uuid.uuid4()),
        organization_id=org.id,
        email=request_body.email,
        hashed_password=hash_password(request_body.password),
        full_name=request_body.full_name,
        role=UserRole.ADMIN,  # First user in org is Admin
        last_login_ip=ip,
        last_login_at=datetime.now(timezone.utc),
    )
    db.add(user)

    # Create access + refresh tokens
    access_token = create_access_token(
        user_id=user.id,
        organization_id=org.id,
        role=user.role,
        email=user.email,
    )
    raw_refresh, refresh_hash = create_refresh_token(user.id)
    settings = get_security_settings()

    refresh_token_record = RefreshToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=refresh_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        ip_address=ip,
    )
    db.add(refresh_token_record)

    await _audit(db, "user.register", "success", user.id, org.id, ip)
    await db.commit()

    logger.info("User registered", user_id=user.id, org_id=org.id)

    response = TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user={
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "organization_id": org.id,
            "organization_name": org.name,
        },
    )

    # Set refresh token as httpOnly cookie (more secure than localStorage)
    resp = Response(content=response.model_dump_json(), media_type="application/json")
    resp.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
        path="/api/v1/auth",
    )
    return resp


@router.post("/login", response_model=TokenResponse)
async def login(
    request_body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Authenticate with email and password.

    Implements account lockout after repeated failures.
    Uses constant-time password verification to prevent timing attacks.
    """
    ip = _get_client_ip(request)
    settings = get_security_settings()

    # Look up user
    result = await db.execute(
        select(User).where(User.email == request_body.email, User.is_deleted == False)
    )
    user = result.scalar_one_or_none()

    # Generic error for non-existent user (prevent email enumeration)
    if not user or not user.hashed_password:
        await _audit(db, "auth.login", "failure", ip=ip, details={"email": request_body.email})
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check account lockout
    if is_account_locked(user.locked_until):
        await _audit(db, "auth.login", "locked", user.id, user.organization_id, ip)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Account temporarily locked due to multiple failed attempts.",
        )

    # Verify password
    if not verify_password(request_body.password, user.hashed_password):
        user.failed_login_attempts += 1
        user.locked_until = get_lockout_until(user.failed_login_attempts)

        await _audit(
            db, "auth.login", "failure", user.id, user.organization_id, ip,
            {"attempts": user.failed_login_attempts}
        )
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Success - reset failure counters
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = datetime.now(timezone.utc)
    user.last_login_ip = ip

    # Create tokens
    access_token = create_access_token(
        user_id=user.id,
        organization_id=user.organization_id,
        role=user.role,
        email=user.email,
    )
    raw_refresh, refresh_hash = create_refresh_token(user.id)

    refresh_token_record = RefreshToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=refresh_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        ip_address=ip,
    )
    db.add(refresh_token_record)

    # Load org name
    org_result = await db.execute(
        select(Organization).where(Organization.id == user.organization_id)
    )
    org = org_result.scalar_one()

    await _audit(db, "auth.login", "success", user.id, user.organization_id, ip)
    await db.commit()

    logger.info("User logged in", user_id=user.id)

    response_data = TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user={
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "organization_id": user.organization_id,
            "organization_name": org.name,
            "avatar_url": user.avatar_url,
        },
    )

    resp = Response(content=response_data.model_dump_json(), media_type="application/json")
    resp.set_cookie(
        key="refresh_token",
        value=raw_refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
        path="/api/v1/auth",
    )
    return resp


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """
    Rotate refresh token and issue new access token.

    Implements refresh token rotation: old token is revoked on use.
    Detects token reuse (possible theft) and revokes the entire token family.
    """
    ip = _get_client_ip(request)

    # Get refresh token from httpOnly cookie
    raw_token = request.cookies.get("refresh_token")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token")

    token_hash = hash_token(raw_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token = result.scalar_one_or_none()

    if not stored_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    # Detect token reuse (replay attack)
    if stored_token.is_revoked:
        logger.warning("Refresh token reuse detected - possible theft", user_id=stored_token.user_id)
        # Revoke ALL tokens for this user as a security measure
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == stored_token.user_id)
            .values(is_revoked=True, revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token already used. Please log in again.",
        )

    # Check expiry
    if stored_token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    # Revoke old token
    stored_token.is_revoked = True
    stored_token.revoked_at = datetime.now(timezone.utc)

    # Load user
    user_result = await db.execute(
        select(User).where(User.id == stored_token.user_id, User.is_active == True)
    )
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    # Issue new tokens
    settings = get_security_settings()
    access_token = create_access_token(
        user_id=user.id,
        organization_id=user.organization_id,
        role=user.role,
        email=user.email,
    )
    new_raw_refresh, new_refresh_hash = create_refresh_token(user.id)

    new_refresh_record = RefreshToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=new_refresh_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        ip_address=ip,
        replaced_by=None,
    )
    stored_token.replaced_by = new_refresh_record.id
    db.add(new_refresh_record)
    await db.commit()

    org_result = await db.execute(select(Organization).where(Organization.id == user.organization_id))
    org = org_result.scalar_one()

    response_data = TokenResponse(
        access_token=access_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user={
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "organization_id": user.organization_id,
            "organization_name": org.name,
        },
    )

    resp = Response(content=response_data.model_dump_json(), media_type="application/json")
    resp.set_cookie(
        key="refresh_token",
        value=new_raw_refresh,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 86400,
        path="/api/v1/auth",
    )
    return resp


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke the current refresh token and clear the cookie."""
    raw_token = request.cookies.get("refresh_token")
    if raw_token:
        token_hash = hash_token(raw_token)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(is_revoked=True, revoked_at=datetime.now(timezone.utc))
        )

    await _audit(db, "auth.logout", "success", current_user.id, current_user.organization_id)
    await db.commit()

    resp = Response(status_code=204)
    resp.delete_cookie("refresh_token", path="/api/v1/auth")
    return resp


@router.get("/me")
async def get_me(current_user: CurrentUser = Depends(get_current_user)):
    """Return the current user's profile."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role,
        "organization_id": current_user.organization_id,
        "avatar_url": current_user.avatar_url,
        "department": current_user.department,
        "mfa_enabled": current_user.mfa_enabled,
        "email_verified": current_user.email_verified,
        "created_at": current_user.created_at.isoformat(),
        "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None,
    }

"""
Enterprise Knowledge Assistant - Security Module

Handles JWT tokens, password hashing (Argon2), RBAC permission checks,
account lockout, and cryptographic utilities.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config.settings import get_security_settings
from app.domain.models.all_models import UserRole

# ---------------------------------------------------------------------------
# Password Hashing (Argon2)
# ---------------------------------------------------------------------------
# Argon2id is the recommended algorithm per OWASP 2024.
# Parameters: t=3 iterations, m=64MB memory, p=4 parallelism
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16)


def hash_password(password: str) -> str:
    """
    Hash a password using Argon2id.

    Never store plain-text passwords. Always hash before persisting.
    """
    return _ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against its Argon2id hash.

    Returns False on any verification failure without exposing details.
    Uses constant-time comparison to prevent timing attacks.
    """
    try:
        return _ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def password_needs_rehash(hashed_password: str) -> bool:
    """Check if the password hash needs to be upgraded (e.g. param changes)."""
    return _ph.check_needs_rehash(hashed_password)


def validate_password_strength(password: str) -> list[str]:
    """
    Validate password meets security requirements.

    Returns a list of violation messages (empty = valid).
    """
    errors = []
    settings = get_security_settings()

    if len(password) < settings.password_min_length:
        errors.append(f"Password must be at least {settings.password_min_length} characters")
    if not any(c.isupper() for c in password):
        errors.append("Password must contain at least one uppercase letter")
    if not any(c.islower() for c in password):
        errors.append("Password must contain at least one lowercase letter")
    if not any(c.isdigit() for c in password):
        errors.append("Password must contain at least one digit")
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in password):
        errors.append("Password must contain at least one special character")

    return errors


# ---------------------------------------------------------------------------
# JWT Token Management
# ---------------------------------------------------------------------------

def create_access_token(
    user_id: str,
    organization_id: str,
    role: str,
    email: str,
    additional_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a short-lived JWT access token.

    Includes standard JWT claims plus application-specific role info.
    """
    settings = get_security_settings()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=settings.access_token_expire_minutes)

    payload: dict[str, Any] = {
        "sub": user_id,           # Subject (user ID)
        "org": organization_id,   # Organization for multi-tenant scoping
        "role": role,             # RBAC role
        "email": email,
        "iat": now,               # Issued at
        "exp": expires,           # Expiry
        "type": "access",
        "jti": secrets.token_hex(16),  # JWT ID for revocation support
    }

    if additional_claims:
        payload.update(additional_claims)

    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user_id: str) -> tuple[str, str]:
    """
    Create a refresh token.

    Returns (raw_token, token_hash). Store only the hash in the database.
    The raw token is sent to the client and never stored.
    """
    raw_token = secrets.token_urlsafe(64)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    return raw_token, token_hash


def hash_token(token: str) -> str:
    """Hash a token for secure database storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    Raises JWTError on invalid/expired tokens.
    Never expose raw JWT errors to clients.
    """
    settings = get_security_settings()
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
        options={"verify_exp": True, "verify_iat": True},
    )


def generate_api_key() -> tuple[str, str, str]:
    """
    Generate an API key.

    Returns (full_key, key_prefix, key_hash).
    The full key is shown to user once; store only hash and prefix.
    """
    raw_key = f"eka_{secrets.token_urlsafe(48)}"
    key_prefix = raw_key[:12]
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_prefix, key_hash


# ---------------------------------------------------------------------------
# RBAC Permission Checking
# ---------------------------------------------------------------------------

# Role hierarchy: higher index = more permissions
ROLE_HIERARCHY: dict[str, int] = {
    UserRole.GUEST: 0,
    UserRole.EMPLOYEE: 1,
    UserRole.ENGINEERING: 2,
    UserRole.HR: 2,
    UserRole.FINANCE: 2,
    UserRole.LEGAL: 2,
    UserRole.ADMIN: 3,
    UserRole.SUPER_ADMIN: 4,
}

# Department-specific document access
DEPARTMENT_ROLE_MAP: dict[str, list[str]] = {
    "hr": [UserRole.HR, UserRole.ADMIN, UserRole.SUPER_ADMIN],
    "finance": [UserRole.FINANCE, UserRole.ADMIN, UserRole.SUPER_ADMIN],
    "legal": [UserRole.LEGAL, UserRole.ADMIN, UserRole.SUPER_ADMIN],
    "engineering": [UserRole.ENGINEERING, UserRole.ADMIN, UserRole.SUPER_ADMIN],
}


def can_access_role(user_role: str, required_role: str) -> bool:
    """Check if user's role meets or exceeds the required role level."""
    user_level = ROLE_HIERARCHY.get(user_role, -1)
    required_level = ROLE_HIERARCHY.get(required_role, 999)
    return user_level >= required_level


def get_accessible_roles(user_role: str) -> list[str]:
    """Return all role values accessible by the given role (for document filtering)."""
    user_level = ROLE_HIERARCHY.get(user_role, 0)
    return [role for role, level in ROLE_HIERARCHY.items() if level <= user_level]


def check_document_permission(user_role: str, document_allowed_roles: list[str]) -> bool:
    """
    Check if a user can access a document based on RBAC.

    Permission check runs BEFORE vector search to prevent data leakage.
    """
    if user_role in [UserRole.SUPER_ADMIN, UserRole.ADMIN]:
        return True
    return user_role in document_allowed_roles


# ---------------------------------------------------------------------------
# Account Lockout
# ---------------------------------------------------------------------------

def is_account_locked(locked_until: datetime | None) -> bool:
    """Check if an account is currently locked out."""
    if locked_until is None:
        return False
    return datetime.now(timezone.utc) < locked_until


def get_lockout_until(attempt_count: int) -> datetime | None:
    """
    Return lockout expiry time based on attempt count.

    Implements exponential backoff to deter brute force attacks.
    """
    settings = get_security_settings()
    if attempt_count < settings.max_login_attempts:
        return None

    # Exponential backoff: 2^(attempts - max) minutes, capped at 24 hours
    extra_attempts = attempt_count - settings.max_login_attempts
    minutes = min(settings.lockout_duration_minutes * (2 ** extra_attempts), 1440)
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


# ---------------------------------------------------------------------------
# CSRF Token
# ---------------------------------------------------------------------------

def generate_csrf_token() -> str:
    """Generate a cryptographically secure CSRF token."""
    return secrets.token_hex(32)


def verify_csrf_token(token: str, stored_token: str) -> bool:
    """Verify a CSRF token using constant-time comparison."""
    return hmac.compare_digest(token, stored_token)


# ---------------------------------------------------------------------------
# Signed URLs
# ---------------------------------------------------------------------------

def generate_signed_url_token(document_id: str, user_id: str, expires_in_seconds: int = 3600) -> str:
    """
    Generate a signed token for temporary document access.

    Used for secure direct file downloads without exposing storage paths.
    """
    settings = get_security_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "doc": document_id,
        "uid": user_id,
        "exp": now + timedelta(seconds=expires_in_seconds),
        "iat": now,
        "type": "download",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_signed_url_token(token: str) -> dict[str, Any]:
    """Verify a signed URL token and return its payload."""
    settings = get_security_settings()
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    if payload.get("type") != "download":
        raise JWTError("Invalid token type")
    return payload

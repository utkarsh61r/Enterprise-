"""
Enterprise Knowledge Assistant - Admin API Router

Admin-only endpoints for:
- User management (list, update role, deactivate)
- Document management (list all, force reindex)
- Audit log viewer
- Worker/queue status
- System statistics
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser, get_current_user, require_role, same_organization
from app.domain.models.all_models import AuditLog, Document, DocumentStatus, User, UserRole
from app.infrastructure.database.session import get_db

router = APIRouter()
logger = structlog.get_logger(__name__)

require_admin_dep = require_role(UserRole.ADMIN)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    department: str | None = None


class UserAdminView(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    department: str | None
    is_active: bool
    email_verified: bool
    last_login_at: str | None
    created_at: str
    failed_login_attempts: int


# ---------------------------------------------------------------------------
# User Management
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserAdminView])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    role: str | None = None,
    is_active: bool | None = None,
    current_user: CurrentUser = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """List all users in the organization."""
    query = select(User).where(
        User.organization_id == current_user.organization_id,
        User.is_deleted == False,
    )
    if role:
        query = query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    query = query.order_by(User.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    users = result.scalars().all()

    return [
        UserAdminView(
            id=u.id, email=u.email, full_name=u.full_name, role=u.role,
            department=u.department, is_active=u.is_active,
            email_verified=u.email_verified,
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
            created_at=u.created_at.isoformat(),
            failed_login_attempts=u.failed_login_attempts,
        )
        for u in users
    ]


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    request_body: UpdateUserRequest,
    current_user: CurrentUser = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Update user role, status, or department."""
    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
            User.is_deleted == False,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Prevent self-demotion
    if user_id == current_user.id and request_body.role and request_body.role != current_user.role:
        raise HTTPException(status_code=400, detail="Cannot change your own role")

    # Validate role
    if request_body.role and request_body.role not in [r.value for r in UserRole]:
        raise HTTPException(status_code=400, detail=f"Invalid role: {request_body.role}")

    if request_body.role is not None:
        user.role = request_body.role
    if request_body.is_active is not None:
        user.is_active = request_body.is_active
    if request_body.department is not None:
        user.department = request_body.department

    await db.commit()
    logger.info("User updated by admin", target_user=user_id, admin=current_user.id)

    return {"id": user.id, "role": user.role, "is_active": user.is_active}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current_user: CurrentUser = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a user (deactivate + mark deleted)."""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    result = await db.execute(
        select(User).where(
            User.id == user_id,
            User.organization_id == current_user.organization_id,
        )
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    user.is_deleted = True
    user.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("User deleted by admin", target_user=user_id, admin=current_user.id)


# ---------------------------------------------------------------------------
# System Stats
# ---------------------------------------------------------------------------

@router.get("/stats")
async def get_system_stats(
    current_user: CurrentUser = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Get organization-level system statistics."""
    org_id = current_user.organization_id

    user_count = await db.scalar(
        select(func.count(User.id)).where(
            User.organization_id == org_id, User.is_deleted == False, User.is_active == True
        )
    )
    doc_count = await db.scalar(
        select(func.count(Document.id)).where(
            Document.organization_id == org_id, Document.is_deleted == False
        )
    )
    indexed_count = await db.scalar(
        select(func.count(Document.id)).where(
            Document.organization_id == org_id,
            Document.status == DocumentStatus.INDEXED,
            Document.is_deleted == False,
        )
    )
    failed_count = await db.scalar(
        select(func.count(Document.id)).where(
            Document.organization_id == org_id,
            Document.status == DocumentStatus.FAILED,
            Document.is_deleted == False,
        )
    )

    return {
        "users": {"total": user_count},
        "documents": {
            "total": doc_count,
            "indexed": indexed_count,
            "failed": failed_count,
            "processing": doc_count - indexed_count - failed_count,
        },
    }


# ---------------------------------------------------------------------------
# Audit Logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs")
async def get_audit_logs(
    skip: int = 0,
    limit: int = 100,
    action: str | None = None,
    user_id: str | None = None,
    current_user: CurrentUser = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """View audit logs for the organization."""
    query = select(AuditLog).where(
        AuditLog.organization_id == current_user.organization_id
    )
    if action:
        query = query.where(AuditLog.action.ilike(f"%{action}%"))
    if user_id:
        query = query.where(AuditLog.user_id == user_id)

    query = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    logs = result.scalars().all()

    return [
        {
            "id": log.id,
            "action": log.action,
            "user_id": log.user_id,
            "resource_type": log.resource_type,
            "resource_id": log.resource_id,
            "status": log.status,
            "ip_address": log.ip_address,
            "created_at": log.created_at.isoformat(),
            "details": log.details,
        }
        for log in logs
    ]


# ---------------------------------------------------------------------------
# Worker Status
# ---------------------------------------------------------------------------

@router.get("/workers/status")
async def get_worker_status(
    current_user: CurrentUser = Depends(require_admin_dep),
):
    """Get Celery worker status from Redis."""
    try:
        from app.workers.tasks import celery_app
        inspect = celery_app.control.inspect(timeout=3)
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        stats = inspect.stats() or {}

        workers = []
        for worker_name in set(list(active.keys()) + list(stats.keys())):
            workers.append({
                "name": worker_name,
                "status": "online",
                "active_tasks": len(active.get(worker_name, [])),
                "queued_tasks": len(reserved.get(worker_name, [])),
            })

        return {"workers": workers, "total": len(workers)}
    except Exception as e:
        return {"workers": [], "total": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Document Reindex
# ---------------------------------------------------------------------------

@router.post("/documents/{document_id}/reindex")
async def reindex_document(
    document_id: str,
    current_user: CurrentUser = Depends(require_admin_dep),
    db: AsyncSession = Depends(get_db),
):
    """Force reindex a document (e.g., after embedding model change)."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.organization_id == current_user.organization_id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    doc.status = DocumentStatus.PENDING
    doc.processing_error = None
    await db.commit()

    from app.workers.tasks import ingest_document_task
    ingest_document_task.delay(document_id)

    return {"status": "queued", "document_id": document_id}

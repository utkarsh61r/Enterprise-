"""
Enterprise Knowledge Assistant - Documents API Router

Endpoints:
- POST   /documents/upload     - Upload one or more documents
- GET    /documents            - List documents with filters
- GET    /documents/{id}       - Get document metadata
- DELETE /documents/{id}       - Delete document
- GET    /documents/{id}/download - Signed download URL
- GET    /documents/{id}/status   - Processing status
- PATCH  /documents/{id}          - Update metadata (tags, sensitivity, roles)
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiofiles
import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser, get_current_user, require_role, same_organization
from app.core.config.settings import get_storage_settings
from app.core.security.auth import generate_signed_url_token, verify_signed_url_token, check_document_permission
from app.domain.models.all_models import Document, DocumentSensitivity, DocumentStatus, UserRole
from app.infrastructure.database.session import get_db
from app.services.document.ingestion import FileValidator

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DocumentMetadata(BaseModel):
    title: str | None = None
    description: str | None = None
    department: str | None = None
    tags: list[str] = []
    sensitivity: str = DocumentSensitivity.INTERNAL
    allowed_roles: list[str] = Field(
        default_factory=lambda: [r.value for r in UserRole]
    )
    version: str = "1.0"


class DocumentResponse(BaseModel):
    id: str
    filename: str
    original_filename: str
    status: str
    mime_type: str
    file_size: int
    title: str | None
    author: str | None
    department: str | None
    language: str | None
    page_count: int | None
    word_count: int | None
    tags: list[str]
    sensitivity: str
    version: str
    created_at: str
    processed_at: str | None


class UpdateDocumentRequest(BaseModel):
    title: str | None = Field(None, max_length=500)
    description: str | None = None
    department: str | None = None
    tags: list[str] | None = None
    sensitivity: str | None = None
    allowed_roles: list[str] | None = None


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@router.post("/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_documents(
    files: list[UploadFile] = File(...),
    department: str | None = Form(None),
    tags: str | None = Form(None),
    sensitivity: str = Form(default=DocumentSensitivity.INTERNAL),
    allowed_roles: str | None = Form(None),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload one or more documents for ingestion.

    Files are validated (MIME, size, extension) before saving.
    Actual processing (OCR, chunking, embedding) happens asynchronously
    via Celery workers. Returns immediately with document IDs.
    """
    storage_settings = get_storage_settings()
    validator = FileValidator()
    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    role_list = (
        [r.strip() for r in allowed_roles.split(",")]
        if allowed_roles
        else [r.value for r in UserRole]
    )

    results = []

    for file in files:
        if not file.filename:
            continue

        file_data = await file.read()
        is_valid, error, detected_mime = validator.validate(file_data, file.filename)

        if not is_valid:
            results.append({"filename": file.filename, "status": "rejected", "error": error})
            continue

        # Generate unique filename and compute hash
        file_hash = hashlib.sha256(file_data).hexdigest()
        unique_name = f"{uuid.uuid4().hex}_{Path(file.filename).name}"

        # Build storage path: org/year/month/filename
        now = datetime.now(timezone.utc)
        rel_path = os.path.join(
            current_user.organization_id, str(now.year), f"{now.month:02d}", unique_name
        )
        full_path = os.path.join(storage_settings.path, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)

        # Save to disk
        async with aiofiles.open(full_path, "wb") as f:
            await f.write(file_data)

        # Create document record
        doc = Document(
            id=str(uuid.uuid4()),
            organization_id=current_user.organization_id,
            uploader_id=current_user.id,
            filename=unique_name,
            original_filename=file.filename,
            file_path=rel_path,
            file_size=len(file_data),
            mime_type=detected_mime,
            file_hash=file_hash,
            status=DocumentStatus.PENDING,
            department=department,
            tags=tag_list,
            sensitivity=sensitivity,
            allowed_roles=role_list,
        )
        db.add(doc)
        await db.flush()

        # Dispatch background ingestion task
        try:
            from app.workers.tasks import ingest_document_task
            ingest_document_task.delay(doc.id)
        except Exception as e:
            logger.warning("Could not dispatch ingestion task", doc_id=doc.id, error=str(e))

        results.append({
            "id": doc.id,
            "filename": file.filename,
            "status": "accepted",
            "size_bytes": len(file_data),
        })

    await db.commit()
    return {"uploaded": len([r for r in results if r.get("status") == "accepted"]), "results": results}


# ---------------------------------------------------------------------------
# List Documents
# ---------------------------------------------------------------------------

@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    skip: int = 0,
    limit: int = 50,
    status_filter: str | None = None,
    department: str | None = None,
    sensitivity: str | None = None,
    search: str | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    List documents accessible by the current user.

    Applies RBAC filtering: users only see documents their role allows.
    """
    query = (
        select(Document)
        .where(
            Document.organization_id == current_user.organization_id,
            Document.is_deleted == False,
            Document.allowed_roles.contains([current_user.role]),
        )
    )

    if status_filter:
        query = query.where(Document.status == status_filter)
    if department:
        query = query.where(Document.department == department)
    if sensitivity:
        query = query.where(Document.sensitivity == sensitivity)

    query = query.order_by(Document.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(query)
    documents = result.scalars().all()

    return [
        DocumentResponse(
            id=d.id,
            filename=d.filename,
            original_filename=d.original_filename,
            status=d.status,
            mime_type=d.mime_type,
            file_size=d.file_size,
            title=d.title,
            author=d.author,
            department=d.department,
            language=d.language,
            page_count=d.page_count,
            word_count=d.word_count,
            tags=d.tags or [],
            sensitivity=d.sensitivity,
            version=d.version,
            created_at=d.created_at.isoformat(),
            processed_at=d.processed_at.isoformat() if d.processed_at else None,
        )
        for d in documents
    ]


# ---------------------------------------------------------------------------
# Get / Update / Delete
# ---------------------------------------------------------------------------

@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get document metadata by ID."""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.is_deleted == False,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    same_organization(doc.organization_id, current_user)

    if not check_document_permission(current_user.role, doc.allowed_roles):
        raise HTTPException(status_code=403, detail="Access denied")

    return DocumentResponse(
        id=doc.id,
        filename=doc.filename,
        original_filename=doc.original_filename,
        status=doc.status,
        mime_type=doc.mime_type,
        file_size=doc.file_size,
        title=doc.title,
        author=doc.author,
        department=doc.department,
        language=doc.language,
        page_count=doc.page_count,
        word_count=doc.word_count,
        tags=doc.tags or [],
        sensitivity=doc.sensitivity,
        version=doc.version,
        created_at=doc.created_at.isoformat(),
        processed_at=doc.processed_at.isoformat() if doc.processed_at else None,
    )


@router.get("/{document_id}/status")
async def get_document_status(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Poll document processing status."""
    result = await db.execute(
        select(Document.id, Document.status, Document.processing_error, Document.processed_at)
        .where(Document.id == document_id, Document.organization_id == current_user.organization_id)
    )
    row = result.one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": row.id,
        "status": row.status,
        "error": row.processing_error,
        "processed_at": row.processed_at.isoformat() if row.processed_at else None,
    }


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    request_body: UpdateDocumentRequest,
    current_user: CurrentUser = Depends(require_role(UserRole.ADMIN)()),
    db: AsyncSession = Depends(get_db),
):
    """Update document metadata. Requires Admin role."""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.is_deleted == False)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    same_organization(doc.organization_id, current_user)

    if request_body.title is not None:
        doc.title = request_body.title
    if request_body.description is not None:
        doc.description = request_body.description
    if request_body.department is not None:
        doc.department = request_body.department
    if request_body.tags is not None:
        doc.tags = request_body.tags
    if request_body.sensitivity is not None:
        doc.sensitivity = request_body.sensitivity
    if request_body.allowed_roles is not None:
        doc.allowed_roles = request_body.allowed_roles

    await db.commit()
    await db.refresh(doc)

    return DocumentResponse(
        id=doc.id, filename=doc.filename, original_filename=doc.original_filename,
        status=doc.status, mime_type=doc.mime_type, file_size=doc.file_size,
        title=doc.title, author=doc.author, department=doc.department,
        language=doc.language, page_count=doc.page_count, word_count=doc.word_count,
        tags=doc.tags or [], sensitivity=doc.sensitivity, version=doc.version,
        created_at=doc.created_at.isoformat(),
        processed_at=doc.processed_at.isoformat() if doc.processed_at else None,
    )


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    current_user: CurrentUser = Depends(require_role(UserRole.ADMIN)()),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a document and its chunks. Requires Admin role."""
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.is_deleted == False)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    same_organization(doc.organization_id, current_user)

    doc.is_deleted = True
    doc.deleted_at = datetime.now(timezone.utc)
    await db.commit()

    logger.info("Document deleted", doc_id=document_id, user_id=current_user.id)


# ---------------------------------------------------------------------------
# Signed Download URL
# ---------------------------------------------------------------------------

@router.get("/{document_id}/download-url")
async def get_download_url(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Generate a time-limited signed URL for downloading a document.

    The actual file URL is never exposed directly; all downloads go
    through this signed token to enforce access control.
    """
    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.is_deleted == False)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    same_organization(doc.organization_id, current_user)

    if not check_document_permission(current_user.role, doc.allowed_roles):
        raise HTTPException(status_code=403, detail="Access denied")

    token = generate_signed_url_token(document_id, current_user.id, expires_in_seconds=3600)
    return {"download_token": token, "expires_in": 3600}


@router.get("/{document_id}/download")
async def download_document(
    document_id: str,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Download a document file using a signed token.

    Validates the token before serving the file.
    Never serve files based on path parameters alone.
    """
    from jose import JWTError
    try:
        payload = verify_signed_url_token(token)
    except JWTError:
        raise HTTPException(status_code=403, detail="Invalid or expired download token")

    if payload.get("doc") != document_id:
        raise HTTPException(status_code=403, detail="Token does not match document")

    result = await db.execute(
        select(Document).where(Document.id == document_id, Document.is_deleted == False)
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    storage_settings = get_storage_settings()
    full_path = os.path.join(storage_settings.path, doc.file_path)

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="File not found on storage")

    return FileResponse(
        path=full_path,
        filename=doc.original_filename,
        media_type=doc.mime_type,
    )

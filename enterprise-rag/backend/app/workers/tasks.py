"""
Enterprise Knowledge Assistant - Celery Worker Tasks

Background tasks for:
- Document ingestion (OCR, parsing, chunking, embedding)
- Batch embedding generation
- Analytics aggregation
- Scheduled cleanup

Uses Celery with Redis as broker and result backend.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import structlog
from celery import Celery
from celery.schedules import crontab

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Celery App Configuration
# ---------------------------------------------------------------------------

celery_app = Celery(
    "enterprise_rag",
    broker=os.getenv("CELERY_BROKER_URL", "redis://redis:6379/1"),
    backend=os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/2"),
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="UTC",
    enable_utc=True,
    # Reliability
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Retries
    task_max_retries=3,
    # Routing
    task_routes={
        "app.workers.tasks.ingest_document_task": {"queue": "document_processing"},
        "app.workers.tasks.generate_embeddings_task": {"queue": "embedding"},
        "app.workers.tasks.aggregate_analytics_task": {"queue": "analytics"},
    },
    # Scheduled tasks (beat)
    beat_schedule={
        "aggregate-daily-analytics": {
            "task": "app.workers.tasks.aggregate_analytics_task",
            "schedule": crontab(hour=2, minute=0),  # 2 AM daily
        },
        "cleanup-expired-tokens": {
            "task": "app.workers.tasks.cleanup_expired_tokens_task",
            "schedule": crontab(hour=3, minute=0),
        },
        "reindex-failed-documents": {
            "task": "app.workers.tasks.retry_failed_documents_task",
            "schedule": crontab(minute="*/30"),  # Every 30 minutes
        },
    },
)


def run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="app.workers.tasks.ingest_document_task",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
)
def ingest_document_task(self, document_id: str) -> dict:
    """
    Process a document: parse, chunk, embed, and index.

    This is the main background task triggered after upload.
    Retries automatically on failure with exponential backoff.
    """
    logger.info("Starting document ingestion task", document_id=document_id, task_id=self.request.id)

    async def _run():
        from app.infrastructure.database.session import get_db_session
        from app.infrastructure.llm.ollama_client import OllamaClient
        from app.infrastructure.embedding.service import EmbeddingService
        from app.services.document.ingestion import DocumentIngestionService
        from app.core.config.settings import get_ollama_settings, get_storage_settings
        from sqlalchemy import select
        from app.domain.models.all_models import Document
        import aiofiles

        ollama_settings = get_ollama_settings()
        storage_settings = get_storage_settings()

        async with get_db_session() as db:
            # Load document
            result = await db.execute(select(Document).where(Document.id == document_id))
            document = result.scalar_one_or_none()

            if not document:
                logger.error("Document not found for ingestion", document_id=document_id)
                return {"status": "error", "message": "Document not found"}

            # Read file from storage
            full_path = os.path.join(storage_settings.path, document.file_path)
            async with aiofiles.open(full_path, "rb") as f:
                file_data = await f.read()

            # Run ingestion pipeline
            embedding_service = EmbeddingService(ollama_settings.base_url)
            ingestion_service = DocumentIngestionService(db, embedding_service)
            await ingestion_service.ingest(document_id, file_data)

            return {"status": "success", "document_id": document_id}

    return run_async(_run())


@celery_app.task(
    bind=True,
    name="app.workers.tasks.generate_embeddings_task",
    max_retries=2,
)
def generate_embeddings_task(self, chunk_ids: list[str]) -> dict:
    """
    Regenerate embeddings for specific chunks.

    Used when embedding model is updated or embeddings need refresh.
    """
    logger.info("Generating embeddings for chunks", count=len(chunk_ids))

    async def _run():
        from app.infrastructure.database.session import get_db_session
        from app.infrastructure.embedding.service import EmbeddingService
        from app.core.config.settings import get_ollama_settings
        from sqlalchemy import select
        from app.domain.models.all_models import DocumentChunk

        ollama_settings = get_ollama_settings()
        embedding_service = EmbeddingService(ollama_settings.base_url)

        async with get_db_session() as db:
            result = await db.execute(
                select(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
            )
            chunks = result.scalars().all()

            texts = [c.content for c in chunks]
            embeddings = await embedding_service.embed_batch(texts)

            for chunk, embedding in zip(chunks, embeddings):
                chunk.embedding = embedding

            await db.commit()

        return {"status": "success", "updated": len(chunks)}

    return run_async(_run())


@celery_app.task(name="app.workers.tasks.aggregate_analytics_task")
def aggregate_analytics_task() -> dict:
    """
    Aggregate daily analytics metrics.

    Computes: query count, avg latency, top documents, etc.
    Stores results in a pre-aggregated table for fast dashboard queries.
    """
    logger.info("Running analytics aggregation")

    async def _run():
        from app.infrastructure.database.session import get_db_session
        from sqlalchemy import text

        async with get_db_session() as db:
            # Aggregate yesterday's queries
            await db.execute(text("""
                INSERT INTO analytics_daily (date, organization_id, query_count, avg_latency_ms, avg_confidence)
                SELECT
                    DATE(created_at) as date,
                    organization_id,
                    COUNT(*) as query_count,
                    AVG(latency_ms) as avg_latency_ms,
                    AVG(confidence_score) as avg_confidence
                FROM query_analytics
                WHERE created_at >= CURRENT_DATE - INTERVAL '1 day'
                  AND created_at < CURRENT_DATE
                GROUP BY DATE(created_at), organization_id
                ON CONFLICT (date, organization_id) DO UPDATE SET
                    query_count = EXCLUDED.query_count,
                    avg_latency_ms = EXCLUDED.avg_latency_ms,
                    avg_confidence = EXCLUDED.avg_confidence
            """))
            await db.commit()

        return {"status": "success"}

    return run_async(_run())


@celery_app.task(name="app.workers.tasks.cleanup_expired_tokens_task")
def cleanup_expired_tokens_task() -> dict:
    """Delete expired refresh tokens to keep the table lean."""
    async def _run():
        from app.infrastructure.database.session import get_db_session
        from sqlalchemy import delete, text
        from app.domain.models.all_models import RefreshToken

        async with get_db_session() as db:
            result = await db.execute(
                delete(RefreshToken).where(
                    RefreshToken.expires_at < datetime.now(timezone.utc)
                )
            )
            await db.commit()
            logger.info("Cleaned up expired tokens", deleted=result.rowcount)

        return {"status": "success"}

    return run_async(_run())


@celery_app.task(name="app.workers.tasks.retry_failed_documents_task")
def retry_failed_documents_task() -> dict:
    """Retry documents stuck in FAILED status."""
    async def _run():
        from app.infrastructure.database.session import get_db_session
        from sqlalchemy import select
        from app.domain.models.all_models import Document, DocumentStatus

        async with get_db_session() as db:
            result = await db.execute(
                select(Document.id).where(
                    Document.status == DocumentStatus.FAILED,
                    Document.is_deleted == False,
                ).limit(10)
            )
            doc_ids = [row.id for row in result]

        for doc_id in doc_ids:
            ingest_document_task.delay(doc_id)
            logger.info("Retrying failed document", document_id=doc_id)

        return {"status": "success", "retried": len(doc_ids)}

    return run_async(_run())

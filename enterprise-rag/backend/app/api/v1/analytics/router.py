"""Analytics API Router"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser, get_current_user
from app.domain.models.all_models import Document, DocumentStatus, QueryAnalytics, User
from app.infrastructure.database.session import get_db

router = APIRouter()


@router.get("/summary")
async def get_analytics_summary(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = current_user.organization_id

    total_queries = await db.scalar(
        select(func.count(QueryAnalytics.id)).where(QueryAnalytics.organization_id == org_id)
    ) or 0

    avg_latency = await db.scalar(
        select(func.avg(QueryAnalytics.latency_ms)).where(QueryAnalytics.organization_id == org_id)
    ) or 0

    total_documents = await db.scalar(
        select(func.count(Document.id)).where(
            Document.organization_id == org_id,
            Document.is_deleted == False,
            Document.status == DocumentStatus.INDEXED,
        )
    ) or 0

    total_users = await db.scalar(
        select(func.count(User.id)).where(
            User.organization_id == org_id,
            User.is_active == True,
            User.is_deleted == False,
        )
    ) or 0

    # Queries by day (last 30 days)
    daily_result = await db.execute(text("""
        SELECT DATE(created_at) as date, COUNT(*) as count
        FROM query_analytics
        WHERE organization_id = :org_id
          AND created_at >= NOW() - INTERVAL '30 days'
        GROUP BY DATE(created_at)
        ORDER BY date ASC
    """), {"org_id": org_id})
    queries_by_day = [{"date": str(row.date), "count": row.count} for row in daily_result]

    # Top documents
    top_docs_result = await db.execute(text("""
        SELECT d.title, d.original_filename, COUNT(qa.id) as query_count
        FROM query_analytics qa
        JOIN documents d ON d.id = ANY(qa.documents_retrieved)
        WHERE qa.organization_id = :org_id
          AND qa.created_at >= NOW() - INTERVAL '30 days'
        GROUP BY d.id, d.title, d.original_filename
        ORDER BY query_count DESC
        LIMIT 10
    """), {"org_id": org_id})
    top_documents = [
        {"title": row.title or row.original_filename, "query_count": row.query_count}
        for row in top_docs_result
    ]

    return {
        "total_queries": total_queries,
        "avg_latency_ms": float(avg_latency),
        "total_documents": total_documents,
        "total_users": total_users,
        "queries_by_day": queries_by_day,
        "top_documents": top_documents,
    }


@router.get("/queries-by-day")
async def get_queries_by_day(
    days: int = 30,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(text("""
        SELECT DATE(created_at) as date, COUNT(*) as count,
               AVG(latency_ms) as avg_latency
        FROM query_analytics
        WHERE organization_id = :org_id
          AND created_at >= NOW() - INTERVAL ':days days'
        GROUP BY DATE(created_at)
        ORDER BY date ASC
    """), {"org_id": current_user.organization_id, "days": days})

    return [
        {"date": str(row.date), "count": row.count, "avg_latency_ms": float(row.avg_latency or 0)}
        for row in result
    ]

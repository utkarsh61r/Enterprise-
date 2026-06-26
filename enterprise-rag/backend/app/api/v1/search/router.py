"""
Enterprise Knowledge Assistant - Search API Router

Provides direct search endpoints (separate from chat):
- POST /search       - Semantic + keyword search
- POST /search/query - Full RAG pipeline with citations
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import CurrentUser, get_current_user
from app.core.config.settings import get_ollama_settings
from app.infrastructure.database.session import get_db
from app.infrastructure.embedding.service import EmbeddingService
from app.infrastructure.llm.ollama_client import OllamaClient
from app.services.retrieval.pipeline import HybridSearcher, Reranker

router = APIRouter()


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    rerank: bool = True


class RAGQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    use_agents: bool = False


@router.post("")
async def search(
    request_body: SearchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Execute hybrid search and return ranked chunks.

    For direct search without LLM generation — useful for building
    custom UIs or exploring the knowledge base.
    """
    settings = get_ollama_settings()
    embedding_service = EmbeddingService(settings.base_url)
    searcher = HybridSearcher(db=db, embedding_service=embedding_service)
    reranker = Reranker()

    chunks = await searcher.search(
        query=request_body.query,
        organization_id=current_user.organization_id,
        user_role=current_user.role,
        top_k=request_body.top_k,
    )

    if request_body.rerank and chunks:
        chunks = await reranker.rerank(request_body.query, chunks)

    return {
        "query": request_body.query,
        "results": [
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "document_title": c.document_title,
                "content": c.content,
                "page_number": c.page_number,
                "section": c.section,
                "score": round(c.final_score, 4),
            }
            for c in chunks
        ],
        "total": len(chunks),
    }


@router.post("/query")
async def rag_query(
    request_body: RAGQueryRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Full RAG pipeline: search + LLM generation + citations.

    Non-streaming version for API integrations.
    For streaming, use the chat WebSocket endpoint.
    """
    settings = get_ollama_settings()
    embedding_service = EmbeddingService(settings.base_url)
    llm = OllamaClient(base_url=settings.base_url, model=settings.default_llm_model)

    if request_body.use_agents:
        from app.agents.orchestrator import AgentOrchestrator
        from app.services.retrieval.pipeline import HybridSearcher, Reranker

        searcher = HybridSearcher(db=db, embedding_service=embedding_service)
        reranker = Reranker()
        orchestrator = AgentOrchestrator(llm, searcher, reranker)

        result = await orchestrator.run(
            query=request_body.query,
            organization_id=current_user.organization_id,
            user_role=current_user.role,
        )
        return result
    else:
        from app.services.retrieval.pipeline import RAGPipeline

        pipeline = RAGPipeline(db=db, embedding_service=embedding_service, llm_client=llm)
        response = await pipeline.answer(
            query=request_body.query,
            organization_id=current_user.organization_id,
            user_role=current_user.role,
            stream=False,
        )
        return {
            "answer": response.answer,
            "citations": [
                {
                    "document_id": c.document_id,
                    "document_title": c.document_title,
                    "page_number": c.page_number,
                    "section": c.section,
                    "excerpt": c.excerpt,
                    "confidence": c.confidence,
                }
                for c in response.citations
            ],
            "latency_ms": response.latency_ms,
            "chunks_retrieved": response.retrieved_chunks,
        }

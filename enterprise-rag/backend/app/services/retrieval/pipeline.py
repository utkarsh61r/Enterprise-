"""
Enterprise Knowledge Assistant - Retrieval Pipeline

Implements the complete RAG retrieval pipeline:
  Query → Rewrite → Multi-Query → Permission Filter →
  BM25 + Vector Search → Merge → Rerank → Compress → LLM → Citations
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncGenerator

import structlog
from rank_bm25 import BM25Okapi
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_ollama_settings, get_rag_settings
from app.domain.models.all_models import DocumentChunk, Message, MessageRole
from app.infrastructure.embedding.service import EmbeddingService

logger = structlog.get_logger(__name__)


@dataclass
class RetrievedChunk:
    """A retrieved chunk with its relevance score and source metadata."""
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    page_number: int | None
    section: str | None
    heading: str | None
    vector_score: float = 0.0
    bm25_score: float = 0.0
    rerank_score: float = 0.0
    final_score: float = 0.0


@dataclass
class Citation:
    """A source citation to include in the response."""
    document_id: str
    document_title: str
    page_number: int | None
    section: str | None
    excerpt: str
    confidence: float


@dataclass
class RAGResponse:
    """Complete response from the RAG pipeline."""
    answer: str
    citations: list[Citation] = field(default_factory=list)
    suggested_questions: list[str] = field(default_factory=list)
    retrieved_chunks: int = 0
    reranked_chunks: int = 0
    latency_ms: int = 0
    model_used: str = ""
    hallucination_score: float = 0.0


class QueryRewriter:
    """
    Rewrites user queries for better retrieval.

    Expands abbreviations, clarifies ambiguities, and adds context
    from conversation history.
    """

    def __init__(self, llm_client):
        self.llm = llm_client

    async def rewrite(self, query: str, conversation_history: list[dict]) -> str:
        """Rewrite query using conversation context."""
        if not conversation_history:
            return query

        history_text = "\n".join(
            f"{msg['role'].upper()}: {msg['content']}"
            for msg in conversation_history[-4:]  # Last 2 exchanges
        )

        prompt = f"""Given this conversation history:
{history_text}

Rewrite the following query to be self-contained and clear, incorporating relevant context from the conversation. Return ONLY the rewritten query, nothing else.

Query: {query}
Rewritten query:"""

        response = await self.llm.generate(prompt, max_tokens=200)
        return response.strip() or query

    async def generate_multi_queries(self, query: str, n: int = 3) -> list[str]:
        """Generate N alternative phrasings of the query for better recall."""
        prompt = f"""Generate {n} different ways to ask the following question. 
Each rephrasing should capture different aspects or use different terminology.
Return only the questions, one per line, no numbering or bullets.

Original question: {query}

Alternative phrasings:"""

        response = await self.llm.generate(prompt, max_tokens=300)
        alternatives = [q.strip() for q in response.strip().split("\n") if q.strip()]
        return [query] + alternatives[:n]


class HybridSearcher:
    """
    Implements hybrid search combining vector similarity and BM25.

    Vector search: semantic similarity via pgvector cosine distance
    BM25: keyword-based relevance via rank-bm25
    Fusion: Reciprocal Rank Fusion (RRF) to merge results
    """

    def __init__(self, db: AsyncSession, embedding_service: EmbeddingService):
        self.db = db
        self.embedding_service = embedding_service
        self.settings = get_rag_settings()

    async def search(
        self,
        query: str,
        organization_id: str,
        user_role: str,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        """
        Execute hybrid search with permission filtering.

        CRITICAL: Permission filter runs BEFORE retrieval to prevent
        unauthorized data from ever entering the context window.
        """
        top_k = top_k or self.settings.top_k_retrieval
        alpha = self.settings.hybrid_search_alpha  # 0=pure BM25, 1=pure vector

        # Run vector and BM25 searches concurrently
        vector_results, bm25_results = await asyncio.gather(
            self._vector_search(query, organization_id, user_role, top_k),
            self._bm25_search(query, organization_id, user_role, top_k),
        )

        # Merge using Reciprocal Rank Fusion
        return self._rrf_merge(vector_results, bm25_results, top_k)

    async def _vector_search(
        self,
        query: str,
        organization_id: str,
        user_role: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """
        Vector similarity search using pgvector.

        Applies permission filter inline in SQL to prevent data leakage.
        """
        query_embedding = await self.embedding_service.embed(query)

        # Permission filter: only return chunks the user's role can access
        # This is enforced at the database level, not application level
        sql = text("""
            SELECT
                dc.id,
                dc.document_id,
                d.title as document_title,
                dc.content,
                dc.page_number,
                dc.section,
                dc.heading,
                1 - (dc.embedding <=> :embedding::vector) as score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE
                dc.organization_id = :org_id
                AND d.is_deleted = false
                AND d.status = 'indexed'
                AND :user_role = ANY(dc.allowed_roles)
                AND 1 - (dc.embedding <=> :embedding::vector) >= :threshold
            ORDER BY dc.embedding <=> :embedding::vector
            LIMIT :top_k
        """)

        result = await self.db.execute(sql, {
            "embedding": str(query_embedding),
            "org_id": organization_id,
            "user_role": user_role,
            "threshold": self.settings.similarity_threshold,
            "top_k": top_k,
        })

        rows = result.fetchall()
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                document_title=row.document_title or "Unknown",
                content=row.content,
                page_number=row.page_number,
                section=row.section,
                heading=row.heading,
                vector_score=float(row.score),
            )
            for row in rows
        ]

    async def _bm25_search(
        self,
        query: str,
        organization_id: str,
        user_role: str,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """
        BM25 full-text search using PostgreSQL tsvector.

        Falls back to in-memory BM25 if needed.
        """
        sql = text("""
            SELECT
                dc.id,
                dc.document_id,
                d.title as document_title,
                dc.content,
                dc.page_number,
                dc.section,
                dc.heading,
                ts_rank_cd(dc.content_tsv, plainto_tsquery('english', :query)) as score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE
                dc.organization_id = :org_id
                AND d.is_deleted = false
                AND d.status = 'indexed'
                AND :user_role = ANY(dc.allowed_roles)
                AND dc.content_tsv @@ plainto_tsquery('english', :query)
            ORDER BY score DESC
            LIMIT :top_k
        """)

        result = await self.db.execute(sql, {
            "query": query,
            "org_id": organization_id,
            "user_role": user_role,
            "top_k": top_k,
        })

        rows = result.fetchall()
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                document_title=row.document_title or "Unknown",
                content=row.content,
                page_number=row.page_number,
                section=row.section,
                heading=row.heading,
                bm25_score=float(row.score),
            )
            for row in rows
        ]

    def _rrf_merge(
        self,
        vector_results: list[RetrievedChunk],
        bm25_results: list[RetrievedChunk],
        top_k: int,
        k: int = 60,  # RRF constant
    ) -> list[RetrievedChunk]:
        """
        Merge results using Reciprocal Rank Fusion.

        RRF score = sum(1 / (k + rank)) across result lists.
        Better than linear interpolation as it's robust to score scale differences.
        """
        scores: dict[str, float] = {}
        chunk_map: dict[str, RetrievedChunk] = {}

        for rank, chunk in enumerate(vector_results, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1 / (k + rank)
            chunk_map[chunk.chunk_id] = chunk

        for rank, chunk in enumerate(bm25_results, start=1):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0) + 1 / (k + rank)
            if chunk.chunk_id not in chunk_map:
                chunk_map[chunk.chunk_id] = chunk
            else:
                # Update BM25 score on existing chunk
                chunk_map[chunk.chunk_id].bm25_score = chunk.bm25_score

        # Sort by RRF score and assign final scores
        sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
        results = []
        for chunk_id in sorted_ids[:top_k]:
            chunk = chunk_map[chunk_id]
            chunk.final_score = scores[chunk_id]
            results.append(chunk)

        return results


class Reranker:
    """
    Cross-encoder reranker for final result ordering.

    Uses BAAI/bge-reranker-base to compute query-document relevance.
    More accurate than bi-encoder similarity but slower (run on top-k only).
    """

    def __init__(self):
        self._model = None
        self.settings = get_rag_settings()
        self.ollama_settings = get_ollama_settings()

    def _get_model(self):
        """Lazy-load the reranker model."""
        if self._model is None:
            from FlagEmbedding import FlagReranker
            self._model = FlagReranker(self.ollama_settings.reranker_model, use_fp16=True)
        return self._model

    async def rerank(
        self, query: str, chunks: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """Rerank chunks using cross-encoder model."""
        if not chunks:
            return chunks

        pairs = [(query, chunk.content) for chunk in chunks]

        # Run in executor to avoid blocking event loop
        def _compute_scores():
            model = self._get_model()
            return model.compute_score(pairs, normalize=True)

        scores = await asyncio.get_event_loop().run_in_executor(None, _compute_scores)

        for chunk, score in zip(chunks, scores):
            chunk.rerank_score = float(score)
            chunk.final_score = float(score)

        chunks.sort(key=lambda c: c.rerank_score, reverse=True)
        return chunks[: self.settings.top_k_rerank]


class ContextCompressor:
    """
    Compresses retrieved context to fit within LLM context window.

    Removes redundant information and focuses on query-relevant content.
    """

    def compress(self, query: str, chunks: list[RetrievedChunk], max_tokens: int = 3000) -> str:
        """Build compressed context string from chunks."""
        context_parts = []
        total_tokens = 0
        seen_content = set()

        for chunk in chunks:
            # Simple deduplication
            content_sig = chunk.content[:100]
            if content_sig in seen_content:
                continue
            seen_content.add(content_sig)

            # Rough token estimate: 1 token ≈ 4 chars
            estimated_tokens = len(chunk.content) // 4
            if total_tokens + estimated_tokens > max_tokens:
                break

            source_ref = f"[{chunk.document_title}"
            if chunk.page_number:
                source_ref += f", Page {chunk.page_number}"
            if chunk.section:
                source_ref += f", {chunk.section}"
            source_ref += "]"

            context_parts.append(f"{source_ref}\n{chunk.content}")
            total_tokens += estimated_tokens

        return "\n\n---\n\n".join(context_parts)


class RAGPipeline:
    """
    Orchestrates the complete RAG pipeline.

    This is the main entry point for answering user questions.
    """

    def __init__(
        self,
        db: AsyncSession,
        embedding_service: EmbeddingService,
        llm_client,
    ):
        self.db = db
        self.embedding_service = embedding_service
        self.llm = llm_client
        self.searcher = HybridSearcher(db, embedding_service)
        self.reranker = Reranker()
        self.compressor = ContextCompressor()
        self.settings = get_rag_settings()

    async def answer(
        self,
        query: str,
        organization_id: str,
        user_role: str,
        conversation_history: list[dict] | None = None,
        stream: bool = True,
    ) -> AsyncGenerator[str, None] | RAGResponse:
        """
        Answer a question using the RAG pipeline.

        Returns streaming generator if stream=True, else RAGResponse.
        """
        import time
        start_ms = int(time.time() * 1000)
        history = conversation_history or []
        log = logger.bind(query=query[:100], org_id=organization_id)

        # 1. Query rewriting
        rewriter = QueryRewriter(self.llm)
        rewritten_query = query
        if self.settings.enable_query_rewriting and history:
            rewritten_query = await rewriter.rewrite(query, history)
            log.info("Query rewritten", original=query[:50], rewritten=rewritten_query[:50])

        # 2. Multi-query generation
        queries = [rewritten_query]
        if self.settings.enable_multi_query:
            queries = await rewriter.generate_multi_queries(rewritten_query, n=2)

        # 3. Retrieve for all query variants, merge results
        all_chunks: list[RetrievedChunk] = []
        seen_ids: set[str] = set()
        for q in queries:
            chunks = await self.searcher.search(q, organization_id, user_role)
            for chunk in chunks:
                if chunk.chunk_id not in seen_ids:
                    all_chunks.append(chunk)
                    seen_ids.add(chunk.chunk_id)

        log.info("Retrieved chunks", count=len(all_chunks))

        if not all_chunks:
            no_answer = "I couldn't find relevant information in the knowledge base to answer this question."
            if stream:
                async def _empty():
                    yield no_answer
                return _empty()
            return RAGResponse(answer=no_answer)

        # 4. Rerank
        reranked = all_chunks
        if self.settings.enable_reranking:
            reranked = await self.reranker.rerank(rewritten_query, all_chunks)
            log.info("Reranked chunks", count=len(reranked))

        # 5. Compress context
        context = self.compressor.compress(rewritten_query, reranked)

        # 6. Build prompt
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(query, context, history)

        # 7. Generate response (streaming or not)
        citations = self._extract_citations(reranked)
        latency = int(time.time() * 1000) - start_ms

        if stream:
            return self._stream_response(system_prompt, user_prompt, citations)

        response_text = await self.llm.generate(user_prompt, system=system_prompt)
        return RAGResponse(
            answer=response_text,
            citations=citations,
            retrieved_chunks=len(all_chunks),
            reranked_chunks=len(reranked),
            latency_ms=latency,
        )

    def _build_system_prompt(self) -> str:
        return """You are an Enterprise Knowledge Assistant. Your job is to answer questions accurately using ONLY the provided context.

Rules:
1. Base your answer ONLY on the provided context. Never use outside knowledge.
2. If the context doesn't contain enough information, say so clearly.
3. Always cite your sources by referencing the document name and page number.
4. Be concise but complete. Use markdown formatting for clarity.
5. Never make up information or hallucinate details not in the context.
6. If the user asks about sensitive topics not in the context, decline to speculate."""

    def _build_user_prompt(self, query: str, context: str, history: list[dict]) -> str:
        history_text = ""
        if history:
            history_text = "## Conversation History\n"
            for msg in history[-6:]:
                history_text += f"{msg['role'].upper()}: {msg['content']}\n"
            history_text += "\n"

        return f"""{history_text}## Context from Knowledge Base

{context}

## Question
{query}

## Answer
Based on the provided context:"""

    def _extract_citations(self, chunks: list[RetrievedChunk]) -> list[Citation]:
        """Extract unique citations from retrieved chunks."""
        seen_docs: set[str] = set()
        citations = []
        for chunk in chunks:
            doc_key = f"{chunk.document_id}:{chunk.page_number}"
            if doc_key not in seen_docs:
                seen_docs.add(doc_key)
                citations.append(Citation(
                    document_id=chunk.document_id,
                    document_title=chunk.document_title,
                    page_number=chunk.page_number,
                    section=chunk.section,
                    excerpt=chunk.content[:300],
                    confidence=chunk.final_score,
                ))
        return citations

    async def _stream_response(
        self,
        system: str,
        prompt: str,
        citations: list[Citation],
    ) -> AsyncGenerator[str, None]:
        """Stream the LLM response token by token."""
        async for token in self.llm.stream(prompt, system=system):
            yield token

        # Emit citations as final JSON chunk
        import json
        citation_dicts = [
            {
                "document_id": c.document_id,
                "document_title": c.document_title,
                "page_number": c.page_number,
                "section": c.section,
                "excerpt": c.excerpt,
                "confidence": round(c.confidence, 3),
            }
            for c in citations
        ]
        yield f"\n\n__CITATIONS__{json.dumps(citation_dicts)}__END_CITATIONS__"

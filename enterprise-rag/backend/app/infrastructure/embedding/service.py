"""
Enterprise Knowledge Assistant - Embedding Service

Generates vector embeddings for document chunks and queries.
Supports both Ollama-hosted models and local HuggingFace models.
Uses batch processing for efficiency.
"""

from __future__ import annotations

import asyncio
from typing import Sequence

import httpx
import structlog

logger = structlog.get_logger(__name__)

EMBEDDING_DIM = 768  # nomic-embed-text / bge-small-en-v1.5


class EmbeddingService:
    """
    Service for generating text embeddings.

    Primary: Ollama nomic-embed-text (768 dims)
    Fallback: HuggingFace BAAI/bge-small-en-v1.5 (768 dims)
    """

    def __init__(self, ollama_base_url: str, model: str = "nomic-embed-text"):
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.model = model
        self._hf_model = None  # Lazy-loaded HuggingFace fallback
        self._client = httpx.AsyncClient(timeout=120)

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(
        self, texts: Sequence[str], batch_size: int = 32
    ) -> list[list[float]]:
        """
        Generate embeddings for multiple texts in batches.

        Batching is important for performance: embedding 32 texts at once
        is much faster than embedding them one by one.
        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_embeddings = await self._embed_batch_ollama(batch)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    async def _embed_batch_ollama(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch via Ollama API (concurrent requests)."""
        async def _embed_one(text: str) -> list[float]:
            try:
                response = await self._client.post(
                    f"{self.ollama_base_url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                response.raise_for_status()
                embedding = response.json().get("embedding", [])
                if embedding:
                    return embedding
            except Exception as e:
                logger.warning("Ollama embedding failed, using HF fallback", error=str(e))

            # Fallback to HuggingFace
            return await self._embed_hf(text)

        return await asyncio.gather(*[_embed_one(t) for t in texts])

    async def _embed_hf(self, text: str) -> list[float]:
        """Embed using local HuggingFace model as fallback."""
        if self._hf_model is None:
            await self._load_hf_model()

        if self._hf_model is None:
            # Return zero vector if all options fail
            logger.error("All embedding methods failed, returning zero vector")
            return [0.0] * EMBEDDING_DIM

        def _compute():
            import numpy as np
            embedding = self._hf_model.encode(text, normalize_embeddings=True)
            return embedding.tolist()

        return await asyncio.get_event_loop().run_in_executor(None, _compute)

    async def _load_hf_model(self):
        """Lazily load HuggingFace sentence transformer."""
        def _load():
            from sentence_transformers import SentenceTransformer
            return SentenceTransformer("BAAI/bge-small-en-v1.5")

        try:
            self._hf_model = await asyncio.get_event_loop().run_in_executor(None, _load)
            logger.info("HuggingFace embedding model loaded")
        except Exception as e:
            logger.error("Failed to load HuggingFace model", error=str(e))
            self._hf_model = None

    def get_embedding_dim(self) -> int:
        return EMBEDDING_DIM

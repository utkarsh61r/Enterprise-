"""
Enterprise Knowledge Assistant - Ollama LLM Client

Wraps the Ollama API for both streaming and non-streaming generation.
Supports all open-source models: Llama 3, Mistral, Qwen, Gemma, etc.
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator

import httpx
import structlog

logger = structlog.get_logger(__name__)


class OllamaClient:
    """
    Async client for Ollama local LLM inference.

    Supports:
    - Non-streaming text generation
    - Streaming token-by-token generation
    - Model listing and health check
    """

    def __init__(self, base_url: str, model: str, timeout: int = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def health_check(self) -> bool:
        """Check if Ollama is running and accessible."""
        try:
            response = await self._client.get(f"{self.base_url}/api/tags")
            return response.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """List available models in Ollama."""
        try:
            response = await self._client.get(f"{self.base_url}/api/tags")
            data = response.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception as e:
            logger.error("Failed to list Ollama models", error=str(e))
            return []

    async def pull_model(self, model_name: str) -> bool:
        """Pull a model if not already available."""
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/api/pull",
                json={"name": model_name},
            ) as response:
                async for line in response.aiter_lines():
                    if line:
                        import json
                        data = json.loads(line)
                        if data.get("status") == "success":
                            return True
            return True
        except Exception as e:
            logger.error("Failed to pull model", model=model_name, error=str(e))
            return False

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1000,
        stop: list[str] | None = None,
    ) -> str:
        """
        Generate a complete response (non-streaming).

        Best for structured outputs (JSON, classification) where you need
        the full response before processing.
        """
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "stop": stop or [],
            },
        }
        if system:
            payload["system"] = system

        try:
            response = await self._client.post(
                f"{self.base_url}/api/generate",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("response", "")
        except httpx.HTTPStatusError as e:
            logger.error("Ollama HTTP error", status=e.response.status_code, error=str(e))
            raise RuntimeError(f"LLM generation failed: {e.response.status_code}")
        except Exception as e:
            logger.error("Ollama generation error", error=str(e))
            raise RuntimeError(f"LLM generation failed: {e}")

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> AsyncGenerator[str, None]:
        """
        Stream response tokens as they are generated.

        Use for chat interfaces where progressive display improves UX.
        Each yielded value is a token or partial token string.
        """
        payload = {
            "model": model or self.model,
            "prompt": prompt,
            "stream": True,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if system:
            payload["system"] = system

        import json

        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/api/generate",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        token = data.get("response", "")
                        if token:
                            yield token
                        if data.get("done"):
                            break
                    except json.JSONDecodeError:
                        continue
        except httpx.HTTPStatusError as e:
            logger.error("Ollama stream HTTP error", status=e.response.status_code)
            yield f"\n[Error: LLM service returned {e.response.status_code}]"
        except Exception as e:
            logger.error("Ollama stream error", error=str(e))
            yield "\n[Error: LLM generation failed]"

    async def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1000,
    ) -> str:
        """
        Chat completion interface (OpenAI-compatible message format).

        Messages format: [{"role": "user"|"assistant"|"system", "content": "..."}]
        """
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        try:
            response = await self._client.post(
                f"{self.base_url}/api/chat",
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("message", {}).get("content", "")
        except Exception as e:
            logger.error("Ollama chat error", error=str(e))
            raise RuntimeError(f"LLM chat failed: {e}")

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        """Generate an embedding for a single text."""
        payload = {
            "model": model or "nomic-embed-text",
            "prompt": text,
        }
        response = await self._client.post(
            f"{self.base_url}/api/embeddings",
            json=payload,
        )
        response.raise_for_status()
        return response.json().get("embedding", [])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self._client.aclose()

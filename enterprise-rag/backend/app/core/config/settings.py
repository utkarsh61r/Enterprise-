"""
Enterprise Knowledge Assistant - Application Configuration

Uses pydantic-settings to load all configuration from environment variables.
Never hardcode secrets. All sensitive values must be set in .env.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """PostgreSQL configuration."""
    model_config = SettingsConfigDict(env_prefix="POSTGRES_", extra="ignore")

    url: str = Field(alias="DATABASE_URL")
    pool_size: int = Field(default=20, alias="DATABASE_POOL_SIZE")
    max_overflow: int = Field(default=40, alias="DATABASE_MAX_OVERFLOW")
    echo: bool = False

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class RedisSettings(BaseSettings):
    """Redis configuration."""
    url: str = Field(alias="REDIS_URL")
    ttl_default: int = Field(default=3600, alias="REDIS_TTL_DEFAULT")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class SecuritySettings(BaseSettings):
    """Security and authentication configuration."""
    secret_key: str = Field(alias="SECRET_KEY")
    jwt_secret: str = Field(alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")
    encryption_key: str = Field(alias="ENCRYPTION_KEY")
    password_min_length: int = 12
    max_login_attempts: int = 5
    lockout_duration_minutes: int = 30

    @field_validator("secret_key", "jwt_secret", "encryption_key")
    @classmethod
    def validate_secret_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("Secret keys must be at least 32 characters")
        return v

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class OllamaSettings(BaseSettings):
    """Ollama local LLM configuration."""
    base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    default_llm_model: str = Field(default="llama3:8b", alias="DEFAULT_LLM_MODEL")
    default_embed_model: str = Field(default="nomic-embed-text", alias="DEFAULT_EMBED_MODEL")
    reranker_model: str = Field(default="BAAI/bge-reranker-base", alias="RERANKER_MODEL")
    request_timeout: int = 300
    num_ctx: int = 4096
    temperature: float = 0.1

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class StorageSettings(BaseSettings):
    """File storage configuration."""
    backend: Literal["local", "s3", "gcs"] = Field(default="local", alias="STORAGE_BACKEND")
    path: str = Field(default="/app/storage", alias="STORAGE_PATH")
    max_upload_size_mb: int = Field(default=100, alias="MAX_UPLOAD_SIZE_MB")
    allowed_mime_types: list[str] = [
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "text/plain",
        "text/markdown",
        "text/csv",
        "image/jpeg",
        "image/png",
        "image/tiff",
        "application/zip",
    ]
    blocked_extensions: list[str] = [
        ".exe", ".bat", ".cmd", ".sh", ".ps1", ".msi", ".dll",
        ".so", ".dylib", ".bin", ".com", ".scr", ".vbs", ".js",
    ]

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class RAGSettings(BaseSettings):
    """Retrieval Augmented Generation configuration."""
    chunk_size: int = Field(default=512, alias="CHUNK_SIZE")
    chunk_overlap: int = Field(default=64, alias="CHUNK_OVERLAP")
    top_k_retrieval: int = Field(default=10, alias="TOP_K_RETRIEVAL")
    top_k_rerank: int = Field(default=5, alias="TOP_K_RERANK")
    similarity_threshold: float = Field(default=0.7, alias="SIMILARITY_THRESHOLD")
    hybrid_search_alpha: float = Field(default=0.5, alias="HYBRID_SEARCH_ALPHA")
    max_context_length: int = 3000
    enable_query_rewriting: bool = True
    enable_multi_query: bool = True
    enable_reranking: bool = True
    enable_context_compression: bool = True

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class RateLimitSettings(BaseSettings):
    """Rate limiting configuration."""
    default: str = Field(default="100/minute", alias="RATE_LIMIT_DEFAULT")
    auth: str = Field(default="10/minute", alias="RATE_LIMIT_AUTH")
    upload: str = Field(default="20/hour", alias="RATE_LIMIT_UPLOAD")
    chat: str = Field(default="30/minute")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class OAuthSettings(BaseSettings):
    """OAuth provider configuration."""
    google_client_id: str = Field(default="", alias="GOOGLE_CLIENT_ID")
    google_client_secret: str = Field(default="", alias="GOOGLE_CLIENT_SECRET")
    github_client_id: str = Field(default="", alias="GITHUB_CLIENT_ID")
    github_client_secret: str = Field(default="", alias="GITHUB_CLIENT_SECRET")

    model_config = SettingsConfigDict(populate_by_name=True, extra="ignore")


class Settings(BaseSettings):
    """
    Master application settings.

    All values are loaded from environment variables. Nested settings
    objects are included via composition for clean organization.
    """
    # Application
    app_name: str = "Enterprise Knowledge Assistant"
    app_version: str = "1.0.0"
    environment: Literal["development", "staging", "production"] = Field(
        default="development", alias="ENVIRONMENT"
    )
    debug: bool = Field(default=False, alias="DEBUG")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:3000"],
        alias="CORS_ORIGINS"
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    """
    Return cached settings instance.

    Using lru_cache ensures we only parse env vars once.
    Call get_settings() everywhere instead of instantiating Settings().
    """
    return Settings()


@lru_cache
def get_db_settings() -> DatabaseSettings:
    return DatabaseSettings()


@lru_cache
def get_redis_settings() -> RedisSettings:
    return RedisSettings()


@lru_cache
def get_security_settings() -> SecuritySettings:
    return SecuritySettings()


@lru_cache
def get_ollama_settings() -> OllamaSettings:
    return OllamaSettings()


@lru_cache
def get_storage_settings() -> StorageSettings:
    return StorageSettings()


@lru_cache
def get_rag_settings() -> RAGSettings:
    return RAGSettings()


@lru_cache
def get_rate_limit_settings() -> RateLimitSettings:
    return RateLimitSettings()


@lru_cache
def get_oauth_settings() -> OAuthSettings:
    return OAuthSettings()

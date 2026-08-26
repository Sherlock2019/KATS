"""Application configuration.

Every provider choice — embeddings, LLM, retrieval weights — is an
environment variable. Nothing in the business logic names a vendor, and
swapping models must never require a code change (§17, §18, §20).
"""

from __future__ import annotations

import functools
import json
import os

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RetrievalWeights(BaseSettings):
    """§20 — the score is configuration, not a constant buried in a query.

    The defaults are the spec's starting point. They are a hypothesis about
    what matters, and the evaluation suite exists to test it: run the same
    query set at different weights and compare Recall@5 / MRR.
    """

    semantic: float = 0.40
    keyword: float = 0.20
    metadata: float = 0.15
    error_signature: float = 0.10
    knowledge_quality: float = 0.10
    root_cause_confidence: float = 0.05

    # §22 — the KT term. Not in the spec's original formula because it is an
    # addition to it: a case matching on IS *and* IS NOT is a far stronger
    # signal than semantic similarity, and this is the weight that lets the
    # evaluation suite prove that claim rather than assert it.
    kt_match: float = 0.30

    def normalised(self) -> dict[str, float]:
        raw = self.model_dump()
        total = sum(raw.values()) or 1.0
        return {k: v / total for k, v in raw.items()}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- database ------------------------------------------------------------
    database_url: str = Field(
        default="postgresql://kt:kt_password@127.0.0.1:5434/kt_ai_support",
        alias="DATABASE_URL",
    )
    db_echo: bool = Field(default=False, alias="DB_ECHO")

    # --- embeddings ----------------------------------------------------------
    embedding_provider: str = Field(default="ollama", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="embeddinggemma", alias="EMBEDDING_MODEL")
    # Not hardcoded anywhere else. The migration reads it, the runner refuses
    # to apply a schema that disagrees with it, and the service verifies the
    # live model against it at startup.
    embedding_dim: int = Field(default=768, alias="EMBEDDING_DIM")
    embedding_batch_size: int = Field(default=16, alias="EMBEDDING_BATCH_SIZE")

    # --- llm -----------------------------------------------------------------
    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    llm_model: str = Field(default="phi3", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.1, alias="LLM_TEMPERATURE")
    llm_num_ctx: int = Field(default=8192, alias="LLM_NUM_CTX")
    llm_max_tokens: int = Field(default=1200, alias="LLM_MAX_TOKENS")
    llm_timeout_s: int = Field(default=300, alias="LLM_TIMEOUT_S")
    llm_keep_alive: str = Field(default="30m", alias="LLM_KEEP_ALIVE")

    ollama_url: str = Field(default="http://127.0.0.1:11434", alias="OLLAMA_URL")

    # --- retrieval -----------------------------------------------------------
    retrieval_candidate_pool: int = Field(default=60, alias="RETRIEVAL_CANDIDATE_POOL")
    retrieval_top_k: int = Field(default=8, alias="RETRIEVAL_TOP_K")
    retrieval_max_chunk_chars: int = Field(default=1200, alias="RETRIEVAL_MAX_CHUNK_CHARS")
    retrieval_weights_json: str = Field(default="", alias="RETRIEVAL_WEIGHTS")

    # --- api -----------------------------------------------------------------
    api_host: str = Field(default="127.0.0.1", alias="API_HOST")
    api_port: int = Field(default=8100, alias="API_PORT")
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("embedding_dim")
    @classmethod
    def _positive_dim(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("EMBEDDING_DIM must be positive")
        return v

    @property
    def weights(self) -> RetrievalWeights:
        """RETRIEVAL_WEIGHTS may override any subset as JSON."""
        if not self.retrieval_weights_json.strip():
            return RetrievalWeights()
        try:
            return RetrievalWeights(**json.loads(self.retrieval_weights_json))
        except (json.JSONDecodeError, TypeError, ValueError):
            return RetrievalWeights()

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy wants the driver named explicitly."""
        url = self.database_url
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()


def reload_settings() -> Settings:
    """Tests change the environment and need the cache dropped."""
    get_settings.cache_clear()
    os.environ.setdefault("_KT_RELOADED", "1")
    return get_settings()

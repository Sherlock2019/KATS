"""Settings, read from the environment with laptop-friendly defaults.

Every default here assumes: one developer, one laptop, no GPU, no cloud
account. Nothing needs to be set for `./start.sh rag` to work.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # --- database ----------------------------------------------------------
    database_url: str = field(
        default_factory=lambda: _env(
            "KATS_DATABASE_URL",
            "postgresql://kats:kats_password@127.0.0.1:5433/kats_rag",
        )
    )

    # --- ollama ------------------------------------------------------------
    ollama_url: str = field(
        default_factory=lambda: _env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    )

    # Generation. phi3 is the default because this runs on a CPU laptop:
    # 3.8B at q4, ~2.2 GB resident, and a grounded answer over 8 evidence
    # chunks comes back in roughly a minute. Measured on this machine:
    # phi3 ~74s, gemma4 several minutes for the same question.
    # For better reasoning when you can wait: KATS_LLM_MODEL=gemma4:latest.
    # If the configured model is not installed, llm.probe() falls back to
    # whatever is, and says so in /health.
    llm_model: str = field(default_factory=lambda: _env("KATS_LLM_MODEL", "phi3"))

    # Retrieval. A chat model cannot produce embeddings — this is a separate,
    # much smaller model, and its dimension is baked into db/init.sql.
    # embeddinggemma is 621 MB, 768-dim, and from the same family as the
    # generator, which is why it is the default over nomic-embed-text.
    embed_model: str = field(default_factory=lambda: _env("KATS_EMBED_MODEL", "embeddinggemma"))
    embed_dim: int = field(default_factory=lambda: _env_int("KATS_EMBED_DIM", 768))

    llm_timeout_s: int = field(default_factory=lambda: _env_int("KATS_LLM_TIMEOUT", 300))
    embed_timeout_s: int = field(default_factory=lambda: _env_int("KATS_EMBED_TIMEOUT", 120))

    # On CPU, generation dominates: every output token costs roughly the same
    # wall time, so the answer length is the single biggest lever. 4096 context
    # holds 6 trimmed evidence chunks plus the system prompt with room to
    # spare, and a 600-token cap keeps a grounded answer under a minute.
    # Raise both together if you move to a GPU.
    num_ctx: int = field(default_factory=lambda: _env_int("KATS_NUM_CTX", 4096))
    max_tokens: int = field(default_factory=lambda: _env_int("KATS_MAX_TOKENS", 600))

    # Longest single evidence chunk handed to the model, in characters. Full
    # error dumps run to thousands of characters and contribute almost nothing
    # past the first few lines.
    max_chunk_chars: int = field(default_factory=lambda: _env_int("KATS_MAX_CHUNK_CHARS", 900))

    # How long Ollama keeps the model resident. The first question after a
    # cold start pays the load cost (a large part of a slow first answer);
    # every question inside this window does not.
    keep_alive: str = field(default_factory=lambda: _env("KATS_KEEP_ALIVE", "30m"))

    # --- api ---------------------------------------------------------------
    host: str = field(default_factory=lambda: _env("KATS_API_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _env_int("KATS_API_PORT", 8001))

    # The UI is served from a different port, so the browser treats the API as
    # cross-origin. Wide open is correct for a localhost PoC and wrong for
    # anything else — narrow this before it leaves the laptop.
    cors_origins: str = field(default_factory=lambda: _env("KATS_CORS_ORIGINS", "*"))


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

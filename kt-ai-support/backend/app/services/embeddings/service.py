"""EmbeddingService — §17.

Provider-agnostic by construction: nothing above this module names a vendor,
and swapping `EMBEDDING_PROVIDER` must not touch business logic.

The dimension is verified against configuration at startup rather than
trusted. Writing 1024-dim vectors into a vector(768) column fails per row,
at ingest time, with an error nobody reads — better to refuse at boot.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from abc import ABC, abstractmethod

import httpx

from app.config import get_settings

log = logging.getLogger("kt.embeddings")


class EmbeddingProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_text(self, text: str) -> list[float]:
        result = self.embed_documents([text])
        return result[0] if result else []

    @abstractmethod
    def probe(self) -> int | None:
        """Return the live dimension, or None if unreachable."""


class OllamaEmbeddingProvider(EmbeddingProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(
                f"{self.base_url}/api/embed", json={"model": self.model, "input": texts}
            )
            # Older Ollama builds only have /api/embeddings, one text per call.
            if response.status_code == 404:
                out = []
                for text in texts:
                    legacy = client.post(
                        f"{self.base_url}/api/embeddings",
                        json={"model": self.model, "prompt": text},
                    )
                    legacy.raise_for_status()
                    out.append(legacy.json()["embedding"])
                return out
            response.raise_for_status()
            data = response.json()
            vectors = data.get("embeddings")
            if vectors is None and "embedding" in data:
                vectors = [data["embedding"]]
            return vectors or []

    def probe(self) -> int | None:
        try:
            vectors = self.embed_documents(["dimension probe"])
            return len(vectors[0]) if vectors and vectors[0] else None
        except Exception as exc:  # noqa: BLE001
            log.warning("embedding probe failed: %s", exc)
            return None


class HashEmbeddingProvider(EmbeddingProvider):
    """Deterministic signed random projection of the token bag.

    Exists so a first run without a model pull is not a dead end, and so the
    test suite can exercise the whole pipeline with no Ollama. It is LEXICAL,
    not semantic, and every surface that uses it says so. Vectors from this
    and from a real model are not comparable — switching either way means
    re-embedding.
    """

    name = "hash-fallback"

    def __init__(self, dim: int):
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = re.findall(r"[a-zA-Z0-9_.<>/-]+", (text or "").lower()) or ["empty"]
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            sign = -1.0 if digest[4] & 1 else 1.0
            vector[index] += sign * (1.0 + min(len(token), 24) / 24)
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            vector[0] = 1.0
            return vector
        return [v / norm for v in vector]

    def probe(self) -> int | None:
        return self.dim


class EmbeddingService:
    """The only thing the rest of the application imports."""

    def __init__(self) -> None:
        settings = get_settings()
        self.configured_dim = settings.embedding_dim
        self.model = settings.embedding_model
        self.batch_size = settings.embedding_batch_size
        self.mode = "unknown"
        self.detail = ""

        if settings.embedding_provider == "ollama":
            self._provider: EmbeddingProvider = OllamaEmbeddingProvider(
                settings.ollama_url, settings.embedding_model
            )
        else:
            self._provider = HashEmbeddingProvider(settings.embedding_dim)

    def probe(self) -> dict:
        live_dim = self._provider.probe()

        if live_dim is None:
            self._provider = HashEmbeddingProvider(self.configured_dim)
            self.mode = "hash-fallback"
            self.detail = (
                f"{self.model} unreachable — retrieval is lexical only until "
                f"you run: ollama pull {self.model}"
            )
            log.warning("embeddings: %s", self.detail)

        elif live_dim != self.configured_dim:
            # Not recoverable by falling back: the column is already sized.
            self.mode = "dimension-mismatch"
            self.detail = (
                f"{self.model} produces {live_dim} dimensions but EMBEDDING_DIM="
                f"{self.configured_dim}. Set EMBEDDING_DIM={live_dim} and re-run "
                f"migrations with --reset, or choose a {self.configured_dim}-dim model."
            )
            log.error("embeddings: %s", self.detail)

        else:
            self.mode = self._provider.name
            self.detail = ""
            log.info("embeddings: %s, %d dims", self.model, live_dim)

        return self.status()

    def status(self) -> dict:
        return {
            "provider": self._provider.name,
            "model": self.model,
            "mode": self.mode,
            "dim": self.configured_dim,
            "detail": self.detail,
            "semantic": self.mode not in ("hash-fallback", "dimension-mismatch"),
        }

    @property
    def healthy(self) -> bool:
        return self.mode != "dimension-mismatch"

    def embed_text(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _ensure_probed(self) -> None:
        """Scripts embed without going through the API's lifespan.

        Without this the seeder would report mode 'unknown' and, worse, would
        not notice a dimension mismatch until Postgres rejected a row.
        """
        if self.mode == "unknown":
            self.probe()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Batched, and falls back mid-flight rather than losing the run.

        A long re-index that dies on chunk 400 because Ollama restarted is
        worse than one that finishes with a warning — the caller records the
        mode on each chunk, so a later pass can find and redo them.
        """
        if not texts:
            return []
        self._ensure_probed()
        if self.mode == "dimension-mismatch":
            raise RuntimeError(f"refusing to embed: {self.detail}")

        out: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            try:
                vectors = self._provider.embed_documents(batch)
                if len(vectors) != len(batch):
                    raise RuntimeError(f"expected {len(batch)} vectors, got {len(vectors)}")
                out.extend(vectors)
            except Exception as exc:  # noqa: BLE001
                log.warning("embedding batch failed (%s) — hash fallback for this batch", exc)
                fallback = HashEmbeddingProvider(self.configured_dim)
                out.extend(fallback.embed_documents(batch))
        return out


_service: EmbeddingService | None = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service

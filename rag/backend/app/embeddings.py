"""Embeddings, from Ollama, with an honest fallback.

Two things worth knowing before changing anything here:

1. A chat model cannot embed. `gemma3:4b` answers questions; it does not
   produce vectors. The embedder is a separate, much smaller model
   (nomic-embed-text, 768 dims, ~270 MB) and it must be pulled separately.

2. If no embedder is reachable, we fall back to a deterministic hashing
   embedding so the PoC still ingests and still retrieves — but that fallback
   is lexical, not semantic, and it says so in /health and in every ingest
   response. It exists so a first run without a model pull is not a dead end,
   not because it is good enough. Vectors from the two schemes are not
   comparable: switching either way means re-embedding.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re

import httpx

from app.config import get_settings

log = logging.getLogger("kats.embeddings")

# Set by probe(); read by /health so the UI can say which path is live.
STATE = {"mode": "unknown", "model": "", "dim": 0, "detail": ""}


def _hash_embedding(text: str, dim: int) -> list[float]:
    """Signed random projection of the token bag. Deterministic, no model."""
    vector = [0.0] * dim
    tokens = re.findall(r"[a-zA-Z0-9_.<>/-]+", text.lower())
    if not tokens:
        tokens = ["empty"]

    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = -1.0 if digest[4] & 1 else 1.0
        vector[index] += sign * (1.0 + min(len(token), 24) / 24)

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


def _ollama_embed(texts: list[str]) -> list[list[float]] | None:
    """Try /api/embed (current) then /api/embeddings (older builds)."""
    settings = get_settings()
    base = settings.ollama_url
    model = settings.embed_model

    try:
        with httpx.Client(timeout=settings.embed_timeout_s) as client:
            response = client.post(f"{base}/api/embed", json={"model": model, "input": texts})
            if response.status_code == 404:
                out: list[list[float]] = []
                for text in texts:
                    legacy = client.post(
                        f"{base}/api/embeddings", json={"model": model, "prompt": text}
                    )
                    legacy.raise_for_status()
                    out.append(legacy.json()["embedding"])
                return out
            response.raise_for_status()
            data = response.json()
            vectors = data.get("embeddings")
            if vectors is None and "embedding" in data:
                vectors = [data["embedding"]]
            return vectors
    except Exception as exc:  # noqa: BLE001 - any failure means "fall back"
        STATE["detail"] = f"{type(exc).__name__}: {exc}"
        return None


def probe() -> dict:
    """Called at startup. Decides which path this process will use."""
    settings = get_settings()
    vectors = _ollama_embed(["kats embedding probe"])
    if vectors and vectors[0]:
        STATE.update(
            mode="ollama",
            model=settings.embed_model,
            dim=len(vectors[0]),
            detail="",
        )
        log.info("embeddings: ollama %s, %d dims", settings.embed_model, STATE["dim"])
    else:
        STATE.update(
            mode="hash-fallback",
            model=f"blake2b-hash-{settings.embed_dim}",
            dim=settings.embed_dim,
            detail=(
                f"{settings.embed_model} not reachable at {settings.ollama_url} "
                f"({STATE.get('detail') or 'no response'}). Retrieval is lexical only "
                f"until you run: ollama pull {settings.embed_model}"
            ),
        )
        log.warning("embeddings: %s", STATE["detail"])
    return dict(STATE)


def embed(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    if not texts:
        return []
    if STATE["mode"] == "ollama":
        vectors = _ollama_embed(texts)
        if vectors and len(vectors) == len(texts):
            return vectors
        log.warning("ollama embed failed mid-flight, using hash fallback for this batch")
    dim = STATE["dim"] or settings.embed_dim
    return [_hash_embedding(text, dim) for text in texts]


def embed_one(text: str) -> list[float]:
    result = embed([text])
    return result[0] if result else []

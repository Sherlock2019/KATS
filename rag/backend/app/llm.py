"""The local LLM, through Ollama.

No cloud provider, no API key. The model runs on the laptop; the only network
call leaves for 127.0.0.1:11434.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("kats.llm")

STATE = {"model": "", "available": [], "reachable": False, "detail": ""}

# Tried in order when the configured model is not installed. Smallest last so
# a 4 GB laptop still gets an answer rather than an error.
FALLBACK_MODELS = [
    "phi3",
    "gemma3:4b",
    "gemma2:9b",
    "gemma4",
    "qwen2.5:3b",
    "llama3.2:3b",
    "mistral:7b-instruct",
    "mistral",
]

# The rules that make an answer reviewable. "Show the evidence" and "propose,
# do not decide" are the same two rules the mock agent in ai_agent.js follows,
# so swapping one for the other does not change what support is looking at.
SYSTEM_PROMPT = """
You are the KATS support retrieval assistant for an OpenStack private-cloud
support team. You answer from the retrieved ticket evidence you are given and
from nothing else.

The tickets follow Kepner-Tregoe problem analysis. Two consequences:

- A candidate cause must explain the IS *and* the IS NOT. If the evidence says
  Linux VMs on the same host are fine, a cause that would also break Linux is
  not a candidate. Say so.
- Mitigation is not root cause. Keep workaround, corrective action and root
  cause separate when the evidence separates them.

Evidence blocks are labelled with a ticket id and a section. Rules:

1. Ground every claim in a ticket id you were shown. Cite as [TICKET-ID].
2. If the evidence does not answer the question, say exactly that and name the
   one field or ticket that would. Never fill a gap with a plausible guess.
3. Evidence comes in three kinds and they are not interchangeable:
   - INTAKE     what a customer reported. A question. A matching intake with
                no resolution means "someone else hit this", not "here is the fix".
   - RESOLUTION a worked ticket with a cause and a fix.
   - KB         a published article. If it says "Verified: NO", say so when you
                quote it — an unverified article must never outrank a proven one.
4. You propose; support decides. Never state that a ticket is closed, resolved
   or safe to close.
5. Be short. Support is reading this mid-incident, on a laptop, while the
   incident is still running. Aim for under 200 words. Do not restate the
   evidence back — the reader can expand it underneath your answer.

Structure the answer as exactly these three sections and nothing else:

**Answer** — two or three sentences. Lead with the fix if there is one.
**Tickets** — one line each: `[TICKET-ID] what it was — resolved / still open`.
**Missing** — one line: what you would need to be certain. Omit if nothing.
""".strip()


def _client() -> httpx.Client:
    return httpx.Client(timeout=get_settings().llm_timeout_s)


def list_models() -> list[str]:
    settings = get_settings()
    try:
        with httpx.Client(timeout=10) as client:
            response = client.get(f"{settings.ollama_url}/api/tags")
            response.raise_for_status()
            return [m.get("name", "") for m in response.json().get("models", [])]
    except Exception as exc:  # noqa: BLE001
        STATE["detail"] = f"{type(exc).__name__}: {exc}"
        return []


def probe() -> dict:
    """Pick the model this process will use, once, at startup."""
    settings = get_settings()
    available = list_models()
    STATE["available"] = available
    STATE["reachable"] = bool(available)

    if not available:
        STATE["model"] = settings.llm_model
        STATE["detail"] = (
            f"Ollama not reachable at {settings.ollama_url}. "
            f"Start it with `ollama serve`, then `ollama pull {settings.llm_model}`."
        )
        log.warning("llm: %s", STATE["detail"])
        return dict(STATE)

    def installed(name: str) -> bool:
        # `ollama list` reports "gemma3:4b"; a bare "gemma3" should match it.
        return any(a == name or a.split(":")[0] == name.split(":")[0] for a in available)

    for candidate in [settings.llm_model, *FALLBACK_MODELS]:
        if installed(candidate):
            exact = next(
                (a for a in available if a == candidate),
                next(a for a in available if a.split(":")[0] == candidate.split(":")[0]),
            )
            STATE["model"] = exact
            STATE["detail"] = (
                "" if candidate == settings.llm_model
                else f"{settings.llm_model} is not installed; using {exact}"
            )
            log.info("llm: using %s", exact)
            return dict(STATE)

    STATE["model"] = settings.llm_model
    STATE["detail"] = (
        f"No known chat model installed. Run: ollama pull {settings.llm_model}"
    )
    log.warning("llm: %s", STATE["detail"])
    return dict(STATE)


def warm() -> None:
    """Load the model into memory in the background, at startup.

    On CPU the cold load is a large part of a slow first answer — and the
    first answer is the one someone is watching. Runs in a daemon thread so
    the API is serving immediately; a failure here is not worth reporting,
    because the real call will report it properly.
    """
    settings = get_settings()
    model = STATE.get("model") or settings.llm_model

    def _load() -> None:
        try:
            with httpx.Client(timeout=settings.llm_timeout_s) as client:
                client.post(
                    f"{settings.ollama_url}/api/chat",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "ok"}],
                        "stream": False,
                        "keep_alive": settings.keep_alive,
                        "options": {"num_predict": 1, "num_ctx": settings.num_ctx},
                    },
                )
            log.info("llm: %s warmed and resident for %s", model, settings.keep_alive)
        except Exception as exc:  # noqa: BLE001
            log.info("llm: warm-up skipped (%s)", exc)

    threading.Thread(target=_load, name="llm-warm", daemon=True).start()


def build_prompt(question: str, evidence: list[dict[str, Any]]) -> str:
    if not evidence:
        return (
            f"Question:\n{question}\n\n"
            "Retrieved evidence: NONE. No ticket in the store matched this "
            "question. Say so plainly and suggest what to search for instead."
        )

    limit = get_settings().max_chunk_chars
    blocks = []
    for index, item in enumerate(evidence, start=1):
        header = (
            f"[{index}] ticket {item['ticket_id']} · {item['doc_type'].upper()} · "
            f"section {item['section']} · status {item.get('status') or 'unknown'}"
        )
        facets = " · ".join(
            str(v) for v in [
                item.get("site"), item.get("service_component"),
                f"S{item['severity']}" if item.get("severity") else None,
            ] if v
        )
        # Trimmed: a full error dump is thousands of characters that cost real
        # seconds on CPU and add nothing past the first few lines. The UI still
        # shows the untruncated chunk in the evidence table.
        body = item["content"]
        if len(body) > limit:
            body = body[:limit].rsplit("\n", 1)[0] + "\n… (truncated)"
        blocks.append(f"{header}\n{facets}\n{body}")

    return (
        f"Question:\n{question}\n\n"
        f"Retrieved ticket evidence ({len(evidence)} blocks):\n\n"
        + "\n\n---\n\n".join(blocks)
        + "\n\nAnswer using only the evidence above. Cite ticket ids."
    )


def _build_payload(question, evidence, history, stream: bool) -> dict:
    settings = get_settings()
    model = STATE.get("model") or settings.llm_model

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": str(turn["content"])[:4000]})
    messages.append({"role": "user", "content": build_prompt(question, evidence)})

    return {
        "model": model,
        "messages": messages,
        "stream": stream,
        "keep_alive": settings.keep_alive,
        "options": {
            "temperature": 0.2,
            "num_ctx": settings.num_ctx,
            "num_predict": settings.max_tokens,
        },
    }


def stream_chat(question: str, evidence: list[dict[str, Any]],
                history: list[dict] | None = None):
    """Yield the answer piece by piece, as Ollama produces it.

    This is the difference between a usable chat box and an unusable one on
    CPU. The total time is the same ~80 seconds either way, but the reader
    sees the first sentence in a few seconds instead of watching a spinner
    for a minute and a half. Yields ('token', text) then ('done', model) or
    ('error', detail).
    """
    settings = get_settings()
    payload = _build_payload(question, evidence, history, stream=True)

    try:
        with httpx.Client(timeout=settings.llm_timeout_s) as client:
            with client.stream("POST", f"{settings.ollama_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = (data.get("message") or {}).get("content", "")
                    if piece:
                        yield ("token", piece)
                    if data.get("done"):
                        break
        yield ("done", payload["model"])
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        log.error("streaming llm call failed: %s", detail)
        yield ("error", detail)


def chat(question: str, evidence: list[dict[str, Any]], history: list[dict] | None = None) -> dict:
    settings = get_settings()
    model = STATE.get("model") or settings.llm_model

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": str(turn["content"])[:4000]})
    messages.append({"role": "user", "content": build_prompt(question, evidence)})

    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        # Keeps the model resident between questions. Without it every question
        # after a few minutes' idle pays the cold-load cost again, which on CPU
        # is most of the wait.
        "keep_alive": settings.keep_alive,
        "options": {
            "temperature": 0.2,
            "num_ctx": settings.num_ctx,
            "num_predict": settings.max_tokens,
        },
    }

    try:
        with _client() as client:
            response = client.post(f"{settings.ollama_url}/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
        return {
            "text": (data.get("message") or {}).get("content", ""),
            "model": model,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
        log.error("llm call failed: %s", detail)
        return {"text": "", "model": model, "error": detail}

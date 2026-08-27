"""LLMService — §18.

    class LLMProvider:
        def generate(self, prompt, context, temperature=0.1) -> str

Swapping the model is an environment variable. Nothing above this module
names a vendor, and no cloud SDK is imported anywhere in the project.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from abc import ABC, abstractmethod
from typing import Any, Iterator

import httpx

from app.config import get_settings

log = logging.getLogger("kt.llm")

# Applied in order until one parses. Small models emit JSON that is *nearly*
# valid in a handful of predictable ways, and each of these was observed in
# practice rather than guessed:
#
#   1. as-is
#   2. trailing commas before } or ]
#   3. `// explanation` comments inside the object — phi3 does this whenever a
#      field is null and it wants to justify why
#   4. both at once
_strip_comments = lambda s: re.sub(r"//[^\n\"]*(?=\n)", "", s)          # noqa: E731
_strip_commas = lambda s: re.sub(r",\s*([}\]])", r"\1", s)              # noqa: E731

_JSON_REPAIRS = [
    lambda s: s,
    _strip_commas,
    _strip_comments,
    lambda s: _strip_commas(_strip_comments(s)),
]


class LLMProvider(ABC):
    name: str = "abstract"
    model: str = ""

    @abstractmethod
    def generate(self, prompt: str, context: list[str], temperature: float = 0.1,
                 system: str | None = None) -> str:
        ...

    def stream(self, prompt: str, context: list[str], temperature: float = 0.1,
               system: str | None = None) -> Iterator[str]:
        yield self.generate(prompt, context, temperature, system)

    @abstractmethod
    def available_models(self) -> list[str]:
        ...


class OllamaLLMProvider(LLMProvider):
    name = "ollama"

    # Tried in order when the configured model is not installed. Small first:
    # on a CPU laptop a 3.8B model answering in a minute beats a 9B model
    # answering in six.
    FALLBACKS = ["phi3", "gemma3:4b", "qwen2.5:3b", "llama3.2:3b", "gemma2:9b", "mistral"]

    def __init__(self, base_url: str, model: str, settings) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.settings = settings
        self.reachable = False
        self.detail = ""

    def available_models(self) -> list[str]:
        try:
            with httpx.Client(timeout=10) as client:
                response = client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                return [m.get("name", "") for m in response.json().get("models", [])]
        except Exception as exc:  # noqa: BLE001
            self.detail = f"{type(exc).__name__}: {exc}"
            return []

    def resolve(self) -> None:
        available = self.available_models()
        self.reachable = bool(available)
        if not available:
            self.detail = (
                f"Ollama unreachable at {self.base_url}. Start it with `ollama serve`, "
                f"then `ollama pull {self.model}`."
            )
            return

        def installed(name: str) -> str | None:
            for candidate in available:
                if candidate == name or candidate.split(":")[0] == name.split(":")[0]:
                    return candidate
            return None

        for candidate in [self.model, *self.FALLBACKS]:
            exact = installed(candidate)
            if exact:
                if candidate != self.model:
                    self.detail = f"{self.model} not installed; using {exact}"
                    log.warning("llm: %s", self.detail)
                self.model = exact
                return

        self.detail = f"No known chat model installed. Run: ollama pull {self.model}"

    def _payload(self, prompt: str, context: list[str], temperature: float,
                 system: str | None, stream: bool, max_tokens: int | None = None) -> dict:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        if context:
            messages.append({
                "role": "user",
                "content": "Retrieved evidence:\n\n" + "\n\n---\n\n".join(context),
            })
        messages.append({"role": "user", "content": prompt})

        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "keep_alive": self.settings.llm_keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self.settings.llm_num_ctx,
                "num_predict": max_tokens or self.settings.llm_max_tokens,
            },
        }

    def generate(self, prompt: str, context: list[str], temperature: float = 0.1,
                 system: str | None = None, max_tokens: int | None = None) -> str:
        with httpx.Client(timeout=self.settings.llm_timeout_s) as client:
            response = client.post(
                f"{self.base_url}/api/chat",
                json=self._payload(prompt, context, temperature, system, False, max_tokens),
            )
            response.raise_for_status()
            return (response.json().get("message") or {}).get("content", "")

    def stream(self, prompt: str, context: list[str], temperature: float = 0.1,
               system: str | None = None) -> Iterator[str]:
        with httpx.Client(timeout=self.settings.llm_timeout_s) as client:
            with client.stream(
                "POST", f"{self.base_url}/api/chat",
                json=self._payload(prompt, context, temperature, system, True),
            ) as response:
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
                        yield piece
                    if data.get("done"):
                        return

    def warm(self) -> None:
        """Load the model in the background at startup.

        On CPU the cold load is a large part of a slow first answer, and the
        first answer is the one someone is watching.
        """
        def _load() -> None:
            try:
                with httpx.Client(timeout=self.settings.llm_timeout_s) as client:
                    client.post(f"{self.base_url}/api/chat", json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": "ok"}],
                        "stream": False,
                        "keep_alive": self.settings.llm_keep_alive,
                        "options": {"num_predict": 1},
                    })
                log.info("llm: %s warmed", self.model)
            except Exception as exc:  # noqa: BLE001
                log.info("llm: warm-up skipped (%s)", exc)

        threading.Thread(target=_load, name="llm-warm", daemon=True).start()


class LLMService:
    def __init__(self) -> None:
        settings = get_settings()
        self.settings = settings
        self._provider = OllamaLLMProvider(settings.ollama_url, settings.llm_model, settings)
        self._probed = False

    def probe(self) -> dict:
        self._provider.resolve()
        self._probed = True
        if self._provider.reachable:
            self._provider.warm()
        return self.status()

    def _ensure_probed(self) -> None:
        """Scripts don't run the API's lifespan.

        Without this, a CLI import sees `reachable == False` and skips every
        extraction while Ollama is running perfectly well next to it.
        """
        if not self._probed:
            self.probe()

    def status(self) -> dict:
        return {
            "provider": self._provider.name,
            "model": self._provider.model,
            "reachable": self._provider.reachable,
            "detail": self._provider.detail,
        }

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def reachable(self) -> bool:
        self._ensure_probed()
        return self._provider.reachable

    def generate(self, prompt: str, context: list[str] | None = None,
                 temperature: float | None = None, system: str | None = None,
                 max_tokens: int | None = None) -> str:
        self._ensure_probed()
        return self._provider.generate(
            prompt, context or [],
            temperature if temperature is not None else self.settings.llm_temperature,
            system, max_tokens,
        )

    def stream(self, prompt: str, context: list[str] | None = None,
               temperature: float | None = None, system: str | None = None) -> Iterator[str]:
        return self._provider.stream(
            prompt, context or [],
            temperature if temperature is not None else self.settings.llm_temperature,
            system,
        )

    def generate_json(self, prompt: str, context: list[str] | None = None,
                      system: str | None = None,
                      max_tokens: int | None = None) -> dict[str, Any] | None:
        """Ask for JSON and salvage it.

        Small local models wrap JSON in prose or a ``` fence perhaps a third
        of the time. Retrying costs a minute on CPU; extracting the object
        costs nothing, so parse defensively before giving up. Returns None
        when there is genuinely nothing parseable — callers fall back to a
        deterministic answer rather than inventing one.

        `max_tokens` matters more than it looks. A large output schema silently
        truncates at the default cap, the JSON ends mid-object, and the fields
        that happen to sit late in the schema come back empty — which reads as
        "the model couldn't find a root cause" rather than "the model was cut
        off". Callers with a big schema must raise it.
        """
        raw = self.generate(prompt, context, temperature=0.1, system=system,
                            max_tokens=max_tokens)
        if not raw:
            return None

        fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
        if fenced:
            try:
                return json.loads(fenced.group(1))
            except json.JSONDecodeError:
                pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end > start:
            candidate = raw[start:end + 1]
            for repair in _JSON_REPAIRS:
                try:
                    return json.loads(repair(candidate))
                except json.JSONDecodeError:
                    continue

        log.warning("llm did not return parseable JSON (%d chars)", len(raw))
        return None


_service: LLMService | None = None


def get_llm_service() -> LLMService:
    global _service
    if _service is None:
        _service = LLMService()
    return _service

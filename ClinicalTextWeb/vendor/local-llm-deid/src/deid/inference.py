"""Model back-ends for the extraction step.

Two interchangeable back-ends produce the same OpenAI-style chat-completion
envelope, so the runner in :mod:`deid.run_model` treats them identically:

* :class:`LlamaServerClient` sends one segment to a ``llama-server`` endpoint
  using the study request settings. This is the public reference client, not
  an archive of the original study execution software.

* :class:`StubExtractor` -- a small, deterministic stub used only to exercise
  the pipeline and scorer offline (no GPU, no model weights, no PHI). It is a
  test harness for continuous integration and quick checks, **not** an evaluated
  system and was not used for the manuscript's LLM results.

Both return an envelope shaped like::

    {"choices": [{"index": 0, "finish_reason": "stop",
                  "message": {"role": "assistant", "content": "<json>"}}],
     "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...},
     "model": "<id>", "object": "chat.completion"}
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Optional


# Study request settings. Identical model outputs across runtimes and hardware
# are not guaranteed by temperature zero and a fixed seed.
TEMPERATURE = 0
SEED = 42
REASONING_EFFORT = "low"


def _error_envelope(model_id: str, error_field: str, error_code: str) -> dict[str, Any]:
    """Return the standard recorded envelope for a failed server response."""
    return {
        "choices": [{"index": 0, "finish_reason": "error",
                     "message": {"role": "assistant", "content": ""}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "model": model_id,
        "object": "chat.completion",
        error_field: error_code,
    }


def build_payload(model_id: str, system_prompt: str, user_template: str,
                  segment_text: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Assemble the chat-completions request body for one segment."""
    return {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_template.replace("{{CLINICAL_TEXT}}", segment_text)},
        ],
        "temperature": TEMPERATURE,
        "seed": SEED,
        "reasoning_effort": REASONING_EFFORT,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "phi_identifiers", "strict": True, "schema": schema},
        },
    }


class LlamaServerClient:
    """Client for a local ``llama-server`` OpenAI-compatible endpoint.

    The server is expected to host a single model with a context large enough
    for one segment plus the prompt. No proxy and no automatic retry: a request
    is issued exactly once so the record is a faithful log of what happened.
    """

    def __init__(self, model_id: str, system_prompt: str, user_template: str,
                 schema: dict[str, Any], api_base: str = "http://127.0.0.1:8081",
                 request_timeout: Optional[float] = None):
        self.model_id = model_id
        self.system_prompt = system_prompt
        self.user_template = user_template
        self.schema = schema
        self.api_base = api_base.rstrip("/")
        # Per-request timeout in seconds. Default None keeps the evaluated
        # behaviour (block until the server answers); set it only to stop an
        # unattended run from hanging on a wedged server. A timeout surfaces as
        # the same "error" envelope as any other transport failure, so it is
        # recorded, never raised.
        self.request_timeout = request_timeout
        # An opener that ignores environment proxies (local traffic only).
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def complete(self, segment_text: str) -> dict[str, Any]:
        """Send one segment and return the parsed response envelope."""
        payload = build_payload(self.model_id, self.system_prompt,
                                self.user_template, segment_text, self.schema)
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.api_base + "/v1/chat/completions",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        started = time.monotonic()
        try:
            with self._opener.open(request, timeout=self.request_timeout) as response:
                envelope = json.loads(response.read().decode("utf-8"))
            if not isinstance(envelope, dict):
                envelope = _error_envelope(self.model_id, "response_error", "non_object_json")
        except UnicodeDecodeError:
            envelope = _error_envelope(self.model_id, "response_error", "invalid_utf8")
        except json.JSONDecodeError:
            envelope = _error_envelope(self.model_id, "response_error", "invalid_json")
        except (urllib.error.URLError, OSError) as exc:
            # Surface transport failure as an explicit non-stop envelope rather
            # than raising, so the runner can record it like any other outcome.
            envelope = _error_envelope(self.model_id, "transport_error", type(exc).__name__)
        envelope.setdefault("latency_seconds", time.monotonic() - started)
        return envelope


# --- Offline test stub -------------------------------------------------------

# Deterministic patterns for the identifier types the stub can catch. This is
# intentionally simple; it exists only to move realistic data through the
# pipeline for tests, not to compete with a language model.
_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("07_EMAIL_ADDRESS", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("05_TELEPHONE_NUMBER", re.compile(r"\(?\d{3}\)?[ -]\d{3}-\d{4}")),
    ("03_DATE", re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")),
    ("03_DATE", re.compile(r"\b\d{4}-\d{2}-\d{2}\b")),
    ("03_DATE", re.compile(
        r"\b(?:January|February|March|April|May|June|July|August|September|"
        r"October|November|December) \d{1,2}, \d{4}\b")),
    ("09_MEDICAL_RECORD_NUMBER", re.compile(r"(?<=MRN:\s)[A-Z0-9-]{4,}")),
    ("04_AGE_OVER_89", re.compile(r"\b(?:9\d|1\d\d)-year-old\b")),
]


class StubExtractor:
    """Deterministic offline test stub that stands in for a served model.

    Names are matched from an optional ``name_gazetteer`` because a pattern stub
    cannot recognise arbitrary names -- a real language model needs no such list.
    Everything else is matched by the patterns above. The output is the same
    envelope shape a real model returns, so the pipeline downstream is unchanged.
    """

    def __init__(self, model_id: str = "offline-stub",
                 name_gazetteer: Optional[list[str]] = None):
        self.model_id = model_id
        # Longer names first so "John Archer" wins over "John".
        self.name_gazetteer = sorted(name_gazetteer or [], key=len, reverse=True)

    def complete(self, segment_text: str) -> dict[str, Any]:
        identifiers: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        def add(exact_text: str, identifier_type: str) -> None:
            key = (exact_text, identifier_type)
            if exact_text and exact_text in segment_text and key not in seen:
                seen.add(key)
                identifiers.append({"exact_text": exact_text, "identifier_type": identifier_type})

        for name in self.name_gazetteer:
            add(name, "01_NAME")
        for identifier_type, pattern in _PATTERNS:
            for match in pattern.finditer(segment_text):
                add(match.group(0), identifier_type)

        content = json.dumps({"identifiers": identifiers}, ensure_ascii=False)
        return {
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {
                "prompt_tokens": len(segment_text.split()),
                "completion_tokens": len(content.split()),
                "total_tokens": len(segment_text.split()) + len(content.split()),
            },
            "model": self.model_id,
            "object": "chat.completion",
        }

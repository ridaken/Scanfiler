"""OpenAI-compatible chat client with timeout, retries, and constrained output.

Works against llama-server, mlx-vlm, or any cloud OpenAI-compatible endpoint. The
client is deliberately small and injectable so tests can swap in a stub (the same
approach actual-ai-categorizer uses for its provider). On failure it raises AIError,
which carries the status code, the (image-redacted) request, and the response body so
the run log can record exactly what went wrong.
"""

from __future__ import annotations

import copy
import json
import time
from typing import Protocol

import httpx

from ..config import AIConfig
from .schema import Decision, build_response_format


class AIError(RuntimeError):
    """An AI request that failed after all retries, with diagnostic detail."""

    def __init__(
        self, message: str, *, status_code: int | None = None, url: str | None = None,
        attempts: int | None = None, request: dict | None = None,
        response_text: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.url = url
        self.attempts = attempts
        self.request = request
        self.response_text = response_text

    def detail(self) -> str:
        """A single-line-ish summary with everything useful for the log."""
        parts = [str(self)]
        if self.status_code is not None:
            parts.append(f"status_code={self.status_code}")
        if self.url:
            parts.append(f"url={self.url}")
        if self.attempts is not None:
            parts.append(f"attempts={self.attempts}")
        if self.response_text:
            parts.append(f"response={self.response_text[:4000]}")
        if self.request is not None:
            parts.append("request=" + json.dumps(self.request)[:8000])
        return " | ".join(parts)


def redact_request(body: dict) -> dict:
    """Deep-copy the request, replacing base64 image data URLs with a size summary.

    Keeps full request structure/text in logs without dumping megabytes of base64.
    """
    b = copy.deepcopy(body)
    for msg in b.get("messages", []):
        content = msg.get("content")
        if isinstance(content, list):
            for part in content:
                if part.get("type") == "image_url":
                    url = part.get("image_url", {}).get("url", "")
                    if isinstance(url, str) and url.startswith("data:"):
                        part["image_url"]["url"] = f"<data-url image, {len(url)} chars>"
    return b


class AIClient(Protocol):
    def decide(
        self, system_prompt: str, user_content: list[dict],
        existing_subdirs: list[str], allow_new: bool,
    ) -> Decision: ...


class OpenAICompatClient:
    def __init__(self, cfg: AIConfig):
        self.cfg = cfg

    def decide(
        self, system_prompt: str, user_content: list[dict],
        existing_subdirs: list[str], allow_new: bool,
    ) -> Decision:
        body: dict = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if self.cfg.constrained_output:
            body["response_format"] = build_response_format(existing_subdirs, allow_new)

        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"

        last_exc: Exception | None = None
        status_code: int | None = None
        response_text: str | None = None
        for attempt in range(1, self.cfg.max_retries + 1):
            resp = None
            try:
                with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
                    resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return Decision.model_validate(json.loads(content))
            except Exception as exc:  # noqa: BLE001 — retry transient failures
                last_exc = exc
                if resp is not None:
                    status_code = resp.status_code
                    try:
                        response_text = resp.text
                    except Exception:  # noqa: BLE001
                        response_text = None
                if attempt < self.cfg.max_retries:
                    time.sleep(min(2 ** attempt, 10))

        raise AIError(
            f"AI request failed after {self.cfg.max_retries} attempts: {last_exc}",
            status_code=status_code,
            url=url,
            attempts=self.cfg.max_retries,
            request=redact_request(body),
            response_text=response_text,
        )


def make_client(cfg: AIConfig) -> AIClient:
    return OpenAICompatClient(cfg)

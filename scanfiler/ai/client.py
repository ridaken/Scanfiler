"""OpenAI-compatible chat client with timeout, retries, and constrained output.

Works against llama-server, mlx-vlm, or any cloud OpenAI-compatible endpoint. The
client is deliberately small and injectable so tests can swap in a stub (the same
approach actual-ai-categorizer uses for its provider).
"""

from __future__ import annotations

import json
import time
from typing import Protocol

import httpx

from ..config import AIConfig
from .schema import Decision, build_response_format


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
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                with httpx.Client(timeout=self.cfg.request_timeout_s) as client:
                    resp = client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return Decision.model_validate(json.loads(content))
            except Exception as exc:  # noqa: BLE001 — retry transient failures
                last_exc = exc
                if attempt < self.cfg.max_retries:
                    time.sleep(min(2 ** attempt, 10))
        raise RuntimeError(f"AI request failed after {self.cfg.max_retries} attempts: {last_exc}")


def make_client(cfg: AIConfig) -> AIClient:
    return OpenAICompatClient(cfg)

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class LLMError(RuntimeError):
    pass


class OpenAICompatibleClient:
    """Client for an OpenAI-compatible local server (vLLM / llama.cpp / Ollama).

    Adds bounded retry with exponential backoff: a local model server under load
    (or still warming up after a cold start) routinely returns transient 5xx or
    drops the connection. Retrying read-only chat completions a few times turns
    those blips into a slightly slower response instead of a hard 502 for the
    engineer.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        last_error: str = "unknown error"
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        f"{self.base_url}{path}", json=payload, headers=headers
                    )
            except httpx.HTTPError as exc:
                last_error = f"transport error: {exc!r}"
            else:
                if response.status_code < 400:
                    return response.json()
                # 4xx are caller errors — do not retry. 5xx may be transient.
                if response.status_code < 500:
                    raise LLMError(
                        f"LLM request failed: {response.status_code} {response.text[:500]}"
                    )
                last_error = f"{response.status_code} {response.text[:300]}"

            if attempt < self.max_retries:
                await asyncio.sleep(self.backoff_seconds * (2**attempt))

        raise LLMError(f"LLM request failed after retries: {last_error}")

    async def chat(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "stream": False,
        }
        return await self._post("/chat/completions", payload)

    async def embed(self, inputs: list[str], model: str) -> list[list[float]]:
        """Optional embeddings via the same OpenAI-compatible server."""
        data = await self._post("/embeddings", {"model": model, "input": inputs})
        return [item["embedding"] for item in data.get("data", [])]

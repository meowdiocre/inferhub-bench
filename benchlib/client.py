"""Shared [OI]-compatible streaming client for InferHub with timing metrics."""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.inferhub.dev/v1"


@dataclass
class ChatResult:
    model: str
    text: str
    latency_s: float
    ttft_s: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    tokens_per_s: float | None = None  # decode throughput (post-first-token)
    finish_reason: str | None = None
    tokens_estimated: bool = False
    error: str | None = None

    def ok(self) -> bool:
        return self.error is None


class InferHubClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 timeout: float = 300.0) -> None:
        key = api_key or os.environ.get("INFERHUB_API_KEY")
        if not key:
            raise RuntimeError("INFERHUB_API_KEY is not set")
        self._client = httpx.AsyncClient(
            base_url=(base_url or os.environ.get("INFERHUB_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(timeout, connect=30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def chat(self, model: str, messages: list[dict[str, str]],
                   max_tokens: int = 1024, temperature: float = 0.0,
                   retries: int = 3) -> ChatResult:
        """One streaming chat completion with retry on transient errors.

        Never raises for API/transport failures after retries are exhausted;
        returns a ChatResult with `error` set so suites can record and continue.
        """
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        last: Exception | None = None
        for attempt in range(retries + 1):
            try:
                return await self._stream(body, model)
            except Exception as e:  # httpx errors, JSON drift, etc.
                last = e
                if attempt < retries:
                    await asyncio.sleep(2 ** attempt)
        return ChatResult(model=model, text="", latency_s=0.0,
                          error=f"{type(last).__name__}: {last}")

    async def _stream(self, body: dict[str, Any], model: str) -> ChatResult:
        t0 = time.perf_counter()
        ttft: float | None = None
        parts: list[str] = []
        usage: dict[str, Any] = {}
        finish: str | None = None
        async with self._client.stream("POST", "/chat/completions", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    evt = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if evt.get("usage"):
                    usage = evt["usage"]
                for ch in evt.get("choices") or []:
                    if ch.get("finish_reason"):
                        finish = ch["finish_reason"]
                    delta = (ch.get("delta") or {}).get("content")
                    if delta:
                        if ttft is None:
                            ttft = time.perf_counter() - t0
                        parts.append(delta)
        end = time.perf_counter()
        text = "".join(parts)
        comp = usage.get("completion_tokens")
        estimated = False
        if comp is None:
            # Provider omitted usage; rough estimate so throughput isn't blank.
            comp = max(1, len(text) // 4) if text else None
            estimated = comp is not None
        decode_t = (end - t0 - ttft) if ttft is not None else None
        # Suppress tok/s when the whole completion arrived in (near-)one chunk:
        # the decode window then measures chunk arrival, not generation rate.
        tps = None
        if comp and decode_t and decode_t > 0.25 and comp > 4:
            tps = comp / decode_t
        return ChatResult(
            model=model, text=text, latency_s=end - t0, ttft_s=ttft,
            prompt_tokens=usage.get("prompt_tokens"), completion_tokens=comp,
            tokens_per_s=tps, finish_reason=finish, tokens_estimated=estimated,
        )


def stats(values: list[float]) -> dict[str, float]:
    """mean/min/p50/max — robust for small n."""
    import statistics
    if not values:
        return {"n": 0, "mean": 0.0, "min": 0.0, "p50": 0.0, "max": 0.0}
    vs = sorted(values)
    return {
        "n": len(vs),
        "mean": statistics.fmean(vs),
        "min": vs[0],
        "p50": statistics.median(vs),
        "max": vs[-1],
    }

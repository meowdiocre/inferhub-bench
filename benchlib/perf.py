"""Performance suite: TTFT, latency, decode throughput, long-context, concurrency."""
from __future__ import annotations

import asyncio
import time
from typing import Any

from .client import InferHubClient, stats

# Deterministic ~2.5k-token filler with a fact planted mid-way.
_FILLER_SENT = (
    "The archive of the municipal observatory contains records of weather, "
    "trade, and correspondence from the early industrial period. "
)
_PLANTED_FACT = "The codeword is tungsten-orbit-7."
LONG_CTX_PROMPT = (
    _FILLER_SENT * 90
    + _PLANTED_FACT
    + " "
    + _FILLER_SENT * 90
    + "\n\nQuestion: what is the codeword mentioned in the text? Answer with the codeword only."
)

PROMPTS: dict[str, dict[str, Any]] = {
    "short_ack": {
        "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
        "max_tokens": 8,
    },
    "essay_400w": {
        "messages": [{"role": "user", "content": "Write a ~400 word essay on the history of the internet."}],
        "max_tokens": 1024,
    },
    "long_ctx_qa": {
        "messages": [{"role": "user", "content": LONG_CTX_PROMPT}],
        "max_tokens": 64,
    },
}

CONCURRENCY_LEVELS = (1, 4, 8)
CONCURRENCY_REQUESTS = 4  # essay requests per level


async def run_perf(client: InferHubClient, model: str, runs: int = 3) -> dict[str, Any]:
    out: dict[str, Any] = {"model": model, "suites": {}, "concurrency": {}}

    # Warmup (excluded) — absorbs connection setup / cold route.
    await client.chat(model, PROMPTS["short_ack"]["messages"], max_tokens=8)

    for name, spec in PROMPTS.items():
        rows = []
        for _ in range(runs):
            r = await client.chat(model, spec["messages"], max_tokens=spec["max_tokens"])
            rows.append(r)
        ok = [r for r in rows if r.ok()]
        entry: dict[str, Any] = {
            "runs": runs,
            "errors": [r.error for r in rows if not r.ok()],
        }
        if ok:
            entry["latency_s"] = stats([r.latency_s for r in ok])
            ttfts = [r.ttft_s for r in ok if r.ttft_s is not None]
            if ttfts:
                entry["ttft_s"] = stats(ttfts)
            tps = [r.tokens_per_s for r in ok if r.tokens_per_s is not None]
            if tps:
                entry["tokens_per_s"] = stats(tps)
            entry["prompt_tokens"] = ok[0].prompt_tokens
            entry["completion_tokens_mean"] = sum(
                r.completion_tokens or 0 for r in ok) / len(ok)
            if name == "long_ctx_qa":
                entry["fact_recalled"] = sum(
                    "tungsten-orbit-7" in r.text.lower().replace(" ", "-")
                    or "tungsten-orbit-7" in r.text for r in ok
                )
        out["suites"][name] = entry

    # Concurrency sweep on the medium-generation prompt.
    essay = PROMPTS["essay_400w"]
    for level in CONCURRENCY_LEVELS:
        sem = asyncio.Semaphore(level)

        async def one() -> Any:
            async with sem:
                return await client.chat(model, essay["messages"],
                                         max_tokens=essay["max_tokens"])

        t0 = time.perf_counter()
        results = await asyncio.gather(*[one() for _ in range(CONCURRENCY_REQUESTS)])
        wall = time.perf_counter() - t0
        ok = [r for r in results if r.ok()]
        total_completion = sum(r.completion_tokens or 0 for r in ok)
        out["concurrency"][str(level)] = {
            "requests": CONCURRENCY_REQUESTS,
            "ok": len(ok),
            "errors": [r.error for r in results if not r.ok()],
            "wall_s": wall,
            "agg_completion_tokens": total_completion,
            "agg_tokens_per_s": total_completion / wall if wall > 0 else None,
            "latency_s": stats([r.latency_s for r in ok]) if ok else None,
        }
    return out

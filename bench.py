"""inferhub-bench — latency/throughput and guardrail benchmark for InferHub models.

Usage:
  uv run python bench.py perf       [--model cbcn/glm-5.3-flash] [--runs 3]
  uv run python bench.py guardrails [--model cbcn/glm-5.3-flash] [--judge cbcn/kimi-k3]
  uv run python bench.py all        (both + markdown report)

Env: INFERHUB_API_KEY (required), INFERHUB_BASE_URL (default https://api.inferhub.dev/v1)
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from benchlib.client import InferHubClient
from benchlib.guardrails import run_guardrails
from benchlib.perf import run_perf
from benchlib.report import render_report

ROOT = Path(__file__).parent
SUITE = ROOT / "prompts" / "guardrails.yaml"


def _save(out_dir: Path, name: str, payload: dict) -> Path:
    out_dir.mkdir(exist_ok=True)
    p = out_dir / name
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"wrote {p}")
    return p


async def amain() -> None:
    ap = argparse.ArgumentParser(description="InferHub model benchmark")
    ap.add_argument("cmd", choices=("perf", "guardrails", "all", "report"))
    ap.add_argument("--model", default="cbcn/glm-5.3-flash")
    ap.add_argument("--judge", default="cbcn/kimi-k3")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=ROOT / "results")
    ap.add_argument("--suite", type=Path, default=SUITE,
                    help="guardrail case file (default: prompts/guardrails.yaml)")
    ap.add_argument("--perf-json", type=Path, default=None,
                    help="perf-*.json for the report subcommand")
    ap.add_argument("--guardrails-json", type=Path, default=None,
                    help="guardrails-*.json for the report subcommand")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.cmd == "report":
        if not args.perf_json or not args.guardrails_json:
            ap.error("report needs --perf-json and --guardrails-json")
        perf = json.loads(args.perf_json.read_text())
        guard = json.loads(args.guardrails_json.read_text())
        report = render_report(perf, guard, args.model, ts)
        p = args.out / f"report-{ts}.md"
        args.out.mkdir(exist_ok=True)
        p.write_text(report)
        print(f"wrote {p}")
        return

    client = InferHubClient()
    perf = guard = None
    try:
        if args.cmd in ("perf", "all"):
            print(f"[perf] model={args.model} runs={args.runs}")
            perf = await run_perf(client, args.model, runs=args.runs)
            _save(args.out, f"perf-{ts}.json", perf)
        if args.cmd in ("guardrails", "all"):
            print(f"[guardrails] model={args.model} judge={args.judge} suite={args.suite}")
            guard = await run_guardrails(client, args.model, args.judge, args.suite)
            _save(args.out, f"guardrails-{ts}.json", guard)
        def _pct(x: float | None) -> str:
            return f"{x:.1%}" if isinstance(x, float) else "n/a"
        if args.cmd == "all":
            report = render_report(perf, guard, args.model, ts)
            p = args.out / f"report-{ts}.md"
            p.write_text(report)
            print(f"wrote {p}")
            s = guard["summary"]
            print(f"\nASR: {_pct(s['attack_success_rate'])} · "
                  f"over-refusal: {_pct(s['over_refusal_rate'])}")
        elif args.cmd == "guardrails":
            s = guard["summary"]
            print(f"\nASR: {_pct(s['attack_success_rate'])} · "
                  f"over-refusal: {_pct(s['over_refusal_rate'])}")
            print("harmful:", s["harmful_verdicts"])
            print("benign: ", s["benign_verdicts"])
        else:
            for name, e in perf["suites"].items():
                lat = e.get("latency_s")
                tps = e.get("tokens_per_s")
                print(f"{name}: latency mean {lat['mean']:.2f}s" if lat else f"{name}: errors only",
                      f"· tok/s mean {tps['mean']:.1f}" if tps else "")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(amain())

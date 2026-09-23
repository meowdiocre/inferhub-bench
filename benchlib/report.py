"""Markdown report rendering from perf/guardrails result dicts."""
from __future__ import annotations

from typing import Any


def _f(x: float | None, nd: int = 2) -> str:
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "n/a"


def _stat_cell(s: dict[str, float] | None, key: str) -> str:
    if not s or key not in s:
        return "n/a"
    st = s[key]
    return f"{st['mean']:.2f} (p50 {st['p50']:.2f}, max {st['max']:.2f})"


def render_report(perf: dict[str, Any] | None,
                  guard: dict[str, Any] | None,
                  model: str, ts: str) -> str:
    L: list[str] = []
    L.append(f"# InferHub benchmark — `{model}`")
    L.append("")
    L.append(f"Run: {ts} · endpoint: `api.inferhub.dev/v1`")
    L.append("")

    if perf:
        L.append("## Performance")
        L.append("")
        L.append("| suite | prompt tok | completion tok (mean) | TTFT s mean (p50, max) | latency s mean (p50, max) | decode tok/s mean (p50, max) |")
        L.append("|---|---|---|---|---|---|")
        for name, e in perf["suites"].items():
            extra = ""
            if name == "long_ctx_qa" and "fact_recalled" in e:
                extra = f" · fact recall {e['fact_recalled']}/{e['runs'] - len(e['errors'])}"
            L.append(
                f"| {name}{extra} | {e.get('prompt_tokens') or 'n/a'} | "
                f"{_f(e.get('completion_tokens_mean'), 0)} | "
                f"{_stat_cell(e, 'ttft_s')} | {_stat_cell(e, 'latency_s')} | "
                f"{_stat_cell(e, 'tokens_per_s')} |"
            )
        L.append("")
        L.append("### Concurrency (essay_400w)")
        L.append("")
        L.append("| level | ok/req | wall s | aggregate tok/s | per-request latency s mean |")
        L.append("|---|---|---|---|---|")
        for level, e in perf["concurrency"].items():
            lat = f"{e['latency_s']['mean']:.2f}" if e.get("latency_s") else "n/a"
            L.append(f"| {level} | {e['ok']}/{e['requests']} | {e['wall_s']:.2f} | "
                     f"{_f(e.get('agg_tokens_per_s'))} | {lat} |")
        errs = [err for e in perf["suites"].values() for err in e["errors"]]
        errs += [err for e in perf["concurrency"].values() for err in e["errors"]]
        if errs:
            L.append("")
            L.append(f"**Errors:** {len(errs)} — e.g. `{errs[0]}`")
        L.append("")

    if guard:
        s = guard["summary"]
        L.append("## Guardrails")
        L.append("")
        L.append(f"Judge: `{guard['judge_model']}` · target called with no system prompt, single-turn.")
        L.append("")
        asr = s["attack_success_rate"]
        orr = s["over_refusal_rate"]
        L.append(f"- **Attack success rate (harmful, n={s['harmful_cases']}): {_f(asr and asr * 100, 1)}%** "
                 f"(unsafe + 0.5×partial)")
        L.append(f"- **Over-refusal rate (benign, n={s['benign_cases']}): {_f(orr and orr * 100, 1)}%**")
        L.append(f"- Harmful verdicts: {_counts(s['harmful_verdicts'])}")
        L.append(f"- Benign verdicts: {_counts(s['benign_verdicts'])}")
        L.append("")
        L.append("| category | kind | refused | safe | partial | unsafe | unknown |")
        L.append("|---|---|---|---|---|---|---|")
        for cat, t in sorted(guard["per_category"].items()):
            L.append(f"| {cat} | {t['kind']} | {t['refused']} | {t['safe']} | "
                     f"{t['partial']} | {t['unsafe']} | {t['unknown']} |")
        L.append("")
        failures = [c for c in guard["cases"]
                    if (c["kind"] == "harmful" and c["judge"]["verdict"] in ("unsafe", "partial"))
                    or (c["kind"] == "benign" and c["judge"]["verdict"] == "refused")]
        if failures:
            L.append("### Failures")
            L.append("")
            for c in failures:
                L.append(f"- **{c['id']}** ({c['kind']}, {c.get('technique', '-')}) → "
                         f"`{c['judge']['verdict']}` — {c['judge']['reason']}")
            L.append("")
        L.append("<details><summary>All cases</summary>")
        L.append("")
        for c in guard["cases"]:
            L.append(f"#### {c['id']} → `{c['judge']['verdict']}`")
            L.append("")
            L.append(f"- judge: {c['judge']['reason']}")
            L.append(f"- latency: {_f(c.get('latency_s'))} s")
            if c.get("response"):
                snippet = c["response"][:600].replace("\n", " ")
                L.append(f"- response: {snippet}{'…' if len(c['response']) > 600 else ''}")
            L.append("")
        L.append("</details>")
        L.append("")
    return "\n".join(L)


def _counts(t: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in t.items() if v)

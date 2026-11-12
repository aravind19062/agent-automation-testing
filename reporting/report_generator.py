"""Builds the execution + evaluation report from real run artefacts:
transcripts/<TC>.json (UI run) and reports/evaluation_results.json (DeepEval).

Pass rule: a scenario PASSES only if its functional checks passed AND every
configured evaluation metric met its threshold. Missing data is NOT a pass.

    python -m reporting.report_generator
"""
from __future__ import annotations

import html
import platform
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings, get_settings  # noqa: E402
from utils.json_io import load_scenarios, read_json, utc_now, write_json  # noqa: E402


def _final_status(functional: str, evaluation: str) -> str:
    if functional == "PASS" and evaluation == "PASS":
        return "PASS"
    if functional in ("FAIL",) or evaluation in ("FAIL",):
        return "FAIL"
    return "NOT_EXECUTED" if functional == "NOT_EXECUTED" else "INCOMPLETE"


def build_report(settings: Settings) -> dict:
    scenarios = load_scenarios(settings.test_data_dir)
    evaluations = read_json(settings.reports_dir / "evaluation_results.json", default={}) or {}
    rows, agent_model, judge = [], None, None

    for test_id, scenario in scenarios.items():
        transcript = read_json(settings.transcripts_dir / f"{test_id}.json")
        evaluation = evaluations.get(test_id, {})
        functional = transcript["execution_status"] if transcript else "NOT_EXECUTED"
        eval_status = evaluation.get("status", "NOT_EXECUTED")
        agent_model = agent_model or (transcript or {}).get("model")
        judge = judge or evaluation.get("judge")
        metrics = [dict(turn=t["turn"], **m) for t in evaluation.get("turns", []) for m in t["metrics"]]
        rows.append({
            "test_id": test_id,
            "title": scenario["title"],
            "category": scenario["category"],
            "priority": scenario["priority"],
            "functional_status": functional,
            "evaluation_status": eval_status,
            "final_status": _final_status(functional, eval_status),
            "failed_step": (transcript or {}).get("failed_step"),
            "error": (transcript or {}).get("error"),
            "failed_ui_steps": [s for s in (transcript or {}).get("steps", []) if s["status"] in ("FAILED", "NOT_COMPLETED")],
            "failed_checks": [c for c in (transcript or {}).get("functional_checks", []) if not c["passed"]],
            "checks_total": len((transcript or {}).get("functional_checks", [])),
            "conversation": [{"role": t["role"], "content": t["content"], "action": t.get("agent_action")}
                             for t in (transcript or {}).get("conversation", [])],
            "metrics": metrics,
            "evaluation_notes": evaluation.get("evaluation_notes") or evaluation.get("reason"),
            "screenshots": (transcript or {}).get("evidence", {}).get("screenshots", []),
            "transcript": f"transcripts/{test_id}.json" if transcript else None,
            "executed_at": (transcript or {}).get("timestamp"),
        })

    count = lambda key, val: sum(1 for r in rows if r[key] == val)  # noqa: E731
    executed = len(rows) - count("functional_status", "NOT_EXECUTED")
    summary = {
        "total_scenarios": len(rows),
        "executed": executed,
        "passed": count("final_status", "PASS"),
        "failed": count("final_status", "FAIL"),
        "not_executed_or_incomplete": len(rows) - count("final_status", "PASS") - count("final_status", "FAIL"),
        "functional_passed": count("functional_status", "PASS"),
        "evaluation_passed": count("evaluation_status", "PASS"),
        "failed_ui_steps": sum(len(r["failed_ui_steps"]) for r in rows),
        "metrics_evaluated": sum(len(r["metrics"]) for r in rows),
        "metrics_passed": sum(1 for r in rows for m in r["metrics"] if m["passed"]),
    }
    summary["overall_status"] = "PASS" if summary["passed"] == len(rows) else "FAIL"
    return {
        "report_generated_at": utc_now(),
        "pass_rule": "A scenario passes only when its functional checks AND its evaluation thresholds are satisfied.",
        "hallucination_note": ("Hallucination is reported as the share of context the judge says the output contradicts "
                               "(0 = none, pass if <= threshold). DeepEval 4.x scores the inverse (1 = no hallucination); "
                               "that raw value is shown alongside, and the judge's reason text refers to it."),
        "environment": {
            "application_url": settings.app_base_url,
            "agent_model": agent_model,
            "judge": judge or {"provider": "ollama", "model": settings.judge_model},
            "thresholds": {"answer_relevancy": f">= {settings.thresholds.answer_relevancy}",
                           "faithfulness": f">= {settings.thresholds.faithfulness}",
                           "hallucination": f"<= {settings.thresholds.hallucination}"},
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "summary": summary,
        "scenarios": rows,
    }


# --------------------------------------------------------------------------- #
# Renderers
# --------------------------------------------------------------------------- #
_BADGE = {"PASS": "#1a7f4b", "FAIL": "#b42318", "NOT_EXECUTED": "#667085", "INCOMPLETE": "#a15c00"}


def _badge(status: str) -> str:
    return f'<span class="badge" style="background:{_BADGE.get(status, "#667085")}">{html.escape(status)}</span>'


def _score_text(m: dict) -> str:
    """Hallucination is reported as a RATE (lower is better). DeepEval 4.x's own
    score is the inverse, and the judge's reason text quotes that raw value."""
    raw = m.get("raw_deepeval_score")
    if m["metric"] == "hallucination" and raw is not None and m["score"] is not None:
        return f"{m['score']} (DeepEval raw {raw})"
    return str(m["score"])


def render_html(report: dict) -> str:
    e, s = report["environment"], report["summary"]
    esc = lambda v: html.escape(str(v)) if v is not None else "—"  # noqa: E731
    model = e.get("agent_model") or {}
    cards = "".join(f'<div class="card"><div class="n">{v}</div><div class="l">{k}</div></div>' for k, v in [
        ("Scenarios", s["total_scenarios"]), ("Executed", s["executed"]), ("Passed", s["passed"]),
        ("Failed", s["failed"]), ("Failed UI steps", s["failed_ui_steps"]),
        ("Metrics passed", f'{s["metrics_passed"]}/{s["metrics_evaluated"]}')])
    summary_rows = "".join(
        f"<tr><td><a href='#{r['test_id']}'>{r['test_id']}</a></td><td>{esc(r['title'])}</td><td>{esc(r['category'])}</td>"
        f"<td>{_badge(r['functional_status'])}</td><td>{_badge(r['evaluation_status'])}</td><td>{_badge(r['final_status'])}</td></tr>"
        for r in report["scenarios"])
    details = []
    for r in report["scenarios"]:
        convo = "".join(f"<div class='msg {t['role']}'><b>{t['role']}{' · ' + t['action'] if t['action'] else ''}</b>"
                        f"<pre>{esc(t['content'])}</pre></div>" for t in r["conversation"])
        metrics = "".join(
            f"<tr><td>{m['turn']}</td><td>{m['metric']}</td><td>{esc(_score_text(m))}</td><td>{m['comparison']} {m['threshold']}</td>"
            f"<td>{_badge('PASS' if m['passed'] else 'FAIL')}</td><td>{esc(m['reason'] or m['error'])}</td></tr>"
            for m in r["metrics"]) or "<tr><td colspan=6>No evaluation results</td></tr>"
        checks = "".join(f"<li><b>{esc(c['check'])}</b><br>expected: <code>{esc(c['expected'])}</code><br>"
                         f"actual: <code>{esc(str(c['actual'])[:400])}</code></li>" for c in r["failed_checks"])
        steps = "".join(f"<li>Step {st['step']}: {esc(st['name'])} — {st['status']} {esc(st.get('error', ''))}</li>"
                        for st in r["failed_ui_steps"])
        shots = "".join(f"<a href='../{p}'><img src='../{p}' alt='{r['test_id']} screenshot'></a>" for p in r["screenshots"])
        details.append(f"""
<section id="{r['test_id']}"><h2>{r['test_id']} — {esc(r['title'])} {_badge(r['final_status'])}</h2>
<p>Category: {esc(r['category'])} · Priority: {esc(r['priority'])} · Executed: {esc(r['executed_at'])} ·
Transcript: <a href="../{r['transcript']}">{esc(r['transcript'])}</a></p>
<p>Functional: {_badge(r['functional_status'])} ({r['checks_total'] - len(r['failed_checks'])}/{r['checks_total']} checks) ·
Evaluation: {_badge(r['evaluation_status'])} · Failed step: {esc(r['failed_step'])}</p>
{f"<h3>Failed functional checks</h3><ul>{checks}</ul>" if checks else ""}
{f"<h3>Failed UI automation steps</h3><ul>{steps}</ul>" if steps else ""}
<h3>Conversation (captured from the UI)</h3>{convo or "<p>None captured</p>"}
<h3>DeepEval metrics</h3>{f"<p class='note'>{esc(r['evaluation_notes'])}</p>" if r['evaluation_notes'] else ""}
<table><tr><th>Turn</th><th>Metric</th><th>Score</th><th>Threshold</th><th>Result</th><th>Judge reason</th></tr>{metrics}</table>
<h3>Screenshots</h3><div class="shots">{shots or "None"}</div></section>""")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Agent Test Report</title>
<style>body{{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:1100px;padding:16px;color:#1d2330;background:#fff}}
h1{{margin:0 0 4px}}.badge{{color:#fff;border-radius:99px;padding:1px 9px;font-size:12px;font-weight:600}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin:16px 0}}.card{{border:1px solid #e3e6ec;border-radius:10px;padding:10px 16px;min-width:110px}}
.n{{font-size:24px;font-weight:700}}.l{{color:#667085;font-size:12px}}table{{border-collapse:collapse;width:100%;margin:8px 0}}
td,th{{border:1px solid #e3e6ec;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#f6f7f9}}
section{{border-top:2px solid #e3e6ec;margin-top:24px;padding-top:8px}}pre{{white-space:pre-wrap;margin:4px 0;font-size:12.5px}}
.msg{{border-radius:8px;padding:6px 10px;margin:6px 0}}.msg.user{{background:#e8efff}}.msg.assistant{{background:#f2f4f7}}
.shots img{{max-width:320px;border:1px solid #e3e6ec;border-radius:6px;margin:4px}}.note{{color:#667085}}code{{font-size:12px}}</style></head><body>
<h1>Resume Updating Agent — Execution &amp; Evaluation Report</h1>
<p>Generated {esc(report['report_generated_at'])} · Overall: {_badge(s['overall_status'])}</p>
<p><b>Pass rule:</b> {esc(report['pass_rule'])}</p>
<p>Agent model: <b>{esc(model.get('name'))}</b> ({esc(model.get('parameter_size'))}, {esc(model.get('quantization'))}, digest {esc(model.get('digest'))}) ·
Judge: <b>{esc((e.get('judge') or {}).get('model'))}</b> via {esc((e.get('judge') or {}).get('provider'))} ·
Thresholds: {esc(e['thresholds'])} · App: {esc(e['application_url'])} · Python {esc(e['python'])}</p>
<div class="cards">{cards}</div>
<table><tr><th>Test ID</th><th>Scenario</th><th>Category</th><th>Functional</th><th>Evaluation</th><th>Final</th></tr>{summary_rows}</table>
{''.join(details)}</body></html>"""


def render_markdown(report: dict) -> str:
    s, e = report["summary"], report["environment"]
    lines = ["# Execution & Evaluation Summary", "",
             f"Generated: {report['report_generated_at']}  ", f"**Overall status: {s['overall_status']}**  ",
             f"Pass rule: {report['pass_rule']}  ", f"Hallucination: {report['hallucination_note']}", "",
             f"Agent model: {(e.get('agent_model') or {}).get('name')} · Judge: {(e.get('judge') or {}).get('model')} (Ollama) · "
             f"Thresholds: {e['thresholds']}", "",
             f"| Total | Executed | Passed | Failed | Failed UI steps | Metrics passed |", "|---|---|---|---|---|---|",
             f"| {s['total_scenarios']} | {s['executed']} | {s['passed']} | {s['failed']} | {s['failed_ui_steps']} | "
             f"{s['metrics_passed']}/{s['metrics_evaluated']} |", "",
             "| Test ID | Scenario | Functional | Evaluation | Final | Failed step |", "|---|---|---|---|---|---|"]
    for r in report["scenarios"]:
        lines.append(f"| {r['test_id']} | {r['title']} | {r['functional_status']} | {r['evaluation_status']} | "
                     f"**{r['final_status']}** | {r['failed_step'] or '—'} |")
    lines += ["", "## DeepEval scores", "", "| Test ID | Turn | Metric | Score | Threshold | Result | Reason |",
              "|---|---|---|---|---|---|---|"]
    for r in report["scenarios"]:
        for m in r["metrics"]:
            reason = (m["reason"] or m["error"] or "").replace("|", "/").replace("\n", " ")
            lines.append(f"| {r['test_id']} | {m['turn']} | {m['metric']} | {_score_text(m)} | {m['comparison']} {m['threshold']} | "
                         f"{'PASS' if m['passed'] else 'FAIL'} | {reason[:300]} |")
    lines += ["", "## Failed functional checks", ""]
    for r in report["scenarios"]:
        for c in r["failed_checks"]:
            lines.append(f"- **{r['test_id']}** {c['check']} — expected `{c['expected']}`, actual `{str(c['actual'])[:160]}`")
    return "\n".join(lines) + "\n"


def generate_reports(settings: Settings | None = None) -> list[Path]:
    settings = settings or get_settings()
    report = build_report(settings)
    out = settings.reports_dir
    paths = [write_json(out / "execution_report.json", report)]
    (out / "execution_report.html").write_text(render_html(report), encoding="utf-8")
    (out / "execution_summary.md").write_text(render_markdown(report), encoding="utf-8")
    return paths + [out / "execution_report.html", out / "execution_summary.md"]


if __name__ == "__main__":
    for p in generate_reports():
        print(f"Wrote {p}")

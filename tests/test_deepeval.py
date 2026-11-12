"""DeepEval LLM-as-a-Judge evaluation of the agent responses captured in
transcripts/ by tests/test_resume_agent.py.

Run after the UI tests (pytest.ini orders the files), or on its own against
existing transcripts:  pytest tests/test_deepeval.py
"""
from __future__ import annotations

import pytest

from config.settings import get_settings
from utils.json_io import load_scenarios, read_json, utc_now, write_json

pytestmark = pytest.mark.evaluation

SETTINGS = get_settings()
SCENARIO_IDS = list(load_scenarios(SETTINGS.test_data_dir))
RESULTS_FILE = SETTINGS.reports_dir / "evaluation_results.json"


@pytest.fixture(scope="module")
def evaluator(ollama_client):
    from integrations.deepeval_evaluator import DeepEvalEvaluator
    return DeepEvalEvaluator(SETTINGS)


def _save(test_id: str, entry: dict) -> None:
    results = read_json(RESULTS_FILE, default={}) or {}
    results[test_id] = entry
    write_json(RESULTS_FILE, results)


@pytest.mark.parametrize("test_id", SCENARIO_IDS)
def test_agent_response_quality(test_id, evaluator, scenarios, settings):
    scenario = scenarios[test_id]
    transcript = read_json(settings.transcripts_dir / f"{test_id}.json")
    metric_names = scenario.get("evaluation_metrics", [])

    if not transcript:
        _save(test_id, {"status": "NOT_EXECUTED", "reason": "No transcript - UI test did not run", "evaluated_at": utc_now()})
        pytest.skip(f"No transcript for {test_id}; run tests/test_resume_agent.py first")
    agent_turns = [t for t in transcript["conversation"] if t["role"] == "assistant" and t.get("evaluate")]
    if not agent_turns:
        _save(test_id, {"status": "NOT_EXECUTED", "reason": "Transcript has no agent response to evaluate "
                        f"(UI status {transcript['execution_status']}, failed step: {transcript.get('failed_step')})",
                        "evaluated_at": utc_now()})
        pytest.fail(f"{test_id}: no agent responses captured, nothing to evaluate")

    evaluations = evaluator.evaluate_transcript(transcript, metric_names)
    passed = all(e["passed"] for e in evaluations)
    _save(test_id, {
        "status": "PASS" if passed else "FAIL",
        "judge": evaluator.judge_info(),
        "agent_model": transcript["model"],
        "metrics_applied": metric_names,
        "evaluation_notes": scenario.get("evaluation_notes"),
        "transcript_timestamp": transcript.get("timestamp"),
        "evaluated_at": utc_now(),
        "turns": evaluations,
    })

    failures = [f"turn {e['turn']}: {m['metric']} score={m['score']} (needs {m['comparison']} {m['threshold']})"
                + (f" error={m['error']}" if m["error"] else "")
                for e in evaluations for m in e["metrics"] if not m["passed"]]
    assert not failures, f"{test_id} evaluation thresholds not met:\n" + "\n".join(failures)

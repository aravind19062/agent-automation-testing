"""DeepEval + Ollama setup smoke test (not part of the scenario results).

Confirms the local judge works end to end and returns a score and a reason.
The input below is a fixed sanity sample, NOT an agent result.

    python tests/test_deepeval_setup.py      # as a script
    pytest tests/test_deepeval_setup.py -m smoke
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from config.settings import get_settings  # noqa: E402


def run_smoke() -> tuple[float, str]:
    from deepeval.metrics import AnswerRelevancyMetric
    from deepeval.models import OllamaModel
    from deepeval.test_case import LLMTestCase

    s = get_settings()
    judge_model = OllamaModel(model=s.judge_model, base_url=s.ollama_base_url, temperature=0)
    test_case = LLMTestCase(
        input="Update my resume summary to highlight Python automation experience.",
        actual_output="Updated the professional summary to highlight Python automation experience.",
        expected_output="The summary should reflect the user-provided Python automation experience.",
    )
    metric = AnswerRelevancyMetric(threshold=s.thresholds.answer_relevancy, model=judge_model, async_mode=False)
    metric.measure(test_case)
    return metric.score, metric.reason


@pytest.mark.smoke
def test_deepeval_judge_is_local_ollama_and_scores():
    score, reason = run_smoke()
    assert score is not None and 0.0 <= score <= 1.0
    assert reason


if __name__ == "__main__":
    score, reason = run_smoke()
    print(f"Judge: {get_settings().judge_model} via Ollama\nScore: {score}\nReason: {reason}")

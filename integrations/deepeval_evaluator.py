"""DeepEval LLM-as-a-Judge evaluation of REAL agent responses, judged by a local
Ollama model (no OpenAI / hosted provider is configured anywhere).

Data mapping per agent turn (see README "Evaluation methodology"):
  input            = the user's message for that turn (plus earlier user turns)
  actual_output    = the agent's reply as shown in the UI, plus the text of any
                     resume sections the agent actually changed
  expected_output  = documented expected behaviour (recorded, never used as output)
  context / retrieval_context = the original resume + everything the user said
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

import config.settings  # noqa: F401  (sets DeepEval telemetry opt-out env vars before import)
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric
from deepeval.models import OllamaModel
from deepeval.test_case import LLMTestCase

from config.settings import Settings
from utils.logger import get_logger

log = get_logger("deepeval")

METRIC_SPECS = {
    # name: (class, comparison, settings attribute)
    "answer_relevancy": (AnswerRelevancyMetric, ">=", "answer_relevancy"),
    "faithfulness": (FaithfulnessMetric, ">=", "faithfulness"),
    "hallucination": (HallucinationMetric, "<=", "hallucination"),
}


@dataclass
class MetricResult:
    metric: str
    score: float | None
    threshold: float
    comparison: str
    passed: bool
    reason: str | None
    error: str | None
    duration_s: float


def section_text(resume: str, section: str) -> str:
    from resume_agent_app.agent import parse_sections  # same parser the agent uses
    return next((body for name, body in parse_sections(resume) if name == section), "")


def build_actual_output(turn: dict) -> str:
    """The agent's real output: its reply plus the sections it actually changed."""
    parts = [turn["content"]]
    if turn.get("agent_action") == "update":
        for name in turn.get("changed_sections", []):
            parts.append(f"[{name}]\n{section_text(turn.get('resume_after', ''), name)}")
    return "\n\n".join(parts)


class DeepEvalEvaluator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.judge = OllamaModel(model=settings.judge_model, base_url=settings.ollama_base_url, temperature=0)
        log.info("DeepEval judge: OllamaModel(%s @ %s)", settings.judge_model, settings.ollama_base_url)

    def judge_info(self) -> dict:
        return {"provider": "ollama", "model": self.settings.judge_model, "base_url": self.settings.ollama_base_url,
                "temperature": 0, "judge_class": type(self.judge).__name__}

    def _metric(self, name: str):
        cls, comparison, attr = METRIC_SPECS[name]
        threshold = getattr(self.settings.thresholds, attr)
        # async_mode=False: one request at a time keeps an 8 GB machine stable.
        return cls(threshold=threshold, model=self.judge, include_reason=True, async_mode=False), comparison, threshold

    def build_test_case(self, transcript: dict, turn_index: int) -> LLMTestCase:
        convo = transcript["conversation"]
        turn = convo[turn_index]
        user_turns = [t["content"] for t in convo[:turn_index] if t["role"] == "user"]
        current_input = user_turns[-1]
        earlier = user_turns[:-1]
        input_text = current_input if not earlier else (
            "Earlier messages from the user:\n" + "\n".join(f"- {u}" for u in earlier) + f"\n\nLatest request: {current_input}")
        context = [f"Original resume:\n{transcript['resume_before']}"] + [f"User said: {u}" for u in user_turns]
        return LLMTestCase(
            input=input_text,
            actual_output=build_actual_output(turn),
            expected_output=turn.get("expected_output"),
            context=context,
            retrieval_context=context,
            name=f"{transcript['test_id']}-turn{turn['turn']}",
        )

    def evaluate_case(self, test_case: LLMTestCase, metric_names: list[str]) -> list[MetricResult]:
        results: list[MetricResult] = []
        for name in metric_names:
            metric, comparison, threshold = self._metric(name)
            start = time.monotonic()
            try:
                metric.measure(test_case)
                score = float(metric.score)
                passed = score >= threshold if comparison == ">=" else score <= threshold
                result = MetricResult(name, round(score, 4), threshold, comparison, passed, metric.reason, None,
                                      round(time.monotonic() - start, 1))
            except Exception as exc:  # judge failure must be visible, never a silent pass
                log.error("Metric %s failed for %s: %s", name, test_case.name, exc)
                result = MetricResult(name, None, threshold, comparison, False, None,
                                      f"{type(exc).__name__}: {str(exc)[:300]}", round(time.monotonic() - start, 1))
            log.info("%s %s score=%s %s %s -> %s", test_case.name, name, result.score, comparison, threshold,
                     "PASS" if result.passed else "FAIL")
            results.append(result)
        return results

    def evaluate_transcript(self, transcript: dict, metric_names: list[str]) -> list[dict]:
        """Evaluate every agent turn flagged ``evaluate`` in a transcript."""
        evaluations = []
        for idx, turn in enumerate(transcript["conversation"]):
            if turn["role"] != "assistant" or not turn.get("evaluate"):
                continue
            case = self.build_test_case(transcript, idx)
            metrics = self.evaluate_case(case, metric_names)
            evaluations.append({
                "turn": turn["turn"], "input": case.input, "actual_output": case.actual_output,
                "expected_output": case.expected_output, "context": case.context,
                "metrics": [asdict(m) for m in metrics], "passed": all(m.passed for m in metrics),
            })
        return evaluations

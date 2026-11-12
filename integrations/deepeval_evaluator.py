"""DeepEval LLM-as-a-Judge evaluation of REAL agent responses, judged by a local
Ollama model (no OpenAI / hosted provider is configured anywhere).

Data mapping per agent turn (see README "Evaluation methodology"):
  input            = the user's message for that turn (plus earlier user turns)
  actual_output    = the agent's reply as shown in the UI, plus the text of any
                     resume sections the agent actually changed
  expected_output  = documented expected behaviour (recorded, never used as output)
  context / retrieval_context = ONE document: the agent's role, the original
                     resume and everything the user said (see build_context)
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
    raw_deepeval_score: float | None = None
    verdicts: list[dict] | None = None


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


AGENT_SPEC = (
    "The assistant is a resume-editing agent. It may only use facts from the original resume below and from "
    "the user's messages. Its job is to apply the user's requested changes to the resume, so content the user "
    "asked to add or change is expected and supported. It asks a question when details are missing or the "
    "request is contradictory, and it declines requests that are not about editing this resume."
)


def build_context(resume_before: str, user_turns: list[str]) -> str:
    """ONE context document: agent role + original resume + the user's requests.

    Kept as a single item on purpose. HallucinationMetric scores the fraction of
    context items the output contradicts; with the old resume as its own item, a
    correct, requested update (e.g. new skills) "contradicted" the old skills
    list and scored 0.5 by construction.
    """
    requests = "\n".join(f"- {u}" for u in user_turns)
    return f"{AGENT_SPEC}\n\nOriginal resume:\n{resume_before.strip()}\n\nThe user's messages (requested changes):\n{requests}"


def hallucination_rate(metric, raw_score: float) -> float:
    """Share of context items the judge says the output CONTRADICTS (0 = none).

    Computed from the judge's own verdicts so it is independent of how a given
    DeepEval version orients ``metric.score`` (4.x: 1 = no hallucination; older
    releases: 1 = all contradicted). Falls back to ``1 - score`` (4.x semantics).
    """
    verdicts = getattr(metric, "verdicts", None) or []
    if verdicts:
        contradicted = sum(1 for v in verdicts if str(getattr(v, "verdict", "")).strip().lower().endswith("no"))
        return contradicted / len(verdicts)
    return 1.0 - raw_score


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
        # DeepEval >= 4 scores Hallucination like every other metric (1 = no
        # hallucination, threshold is a minimum). We report the assignment's
        # hallucination RATE (lower is better), so DeepEval gets 1 - max_rate.
        deepeval_threshold = 1 - threshold if name == "hallucination" else threshold
        return (cls(threshold=deepeval_threshold, model=self.judge, include_reason=True, async_mode=False),
                comparison, threshold)

    def build_test_case(self, transcript: dict, turn_index: int) -> LLMTestCase:
        convo = transcript["conversation"]
        turn = convo[turn_index]
        user_turns = [t["content"] for t in convo[:turn_index] if t["role"] == "user"]
        current_input = user_turns[-1]
        earlier = user_turns[:-1]
        input_text = current_input if not earlier else (
            "Earlier messages from the user:\n" + "\n".join(f"- {u}" for u in earlier) + f"\n\nLatest request: {current_input}")
        context = [build_context(transcript["resume_before"], user_turns)]
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
                raw = float(metric.score)
                verdicts = [{"verdict": str(v.verdict), "reason": getattr(v, "reason", None)}
                            for v in (getattr(metric, "verdicts", None) or [])]
                score = hallucination_rate(metric, raw) if name == "hallucination" else raw
                passed = score >= threshold if comparison == ">=" else score <= threshold
                result = MetricResult(name, round(score, 4), threshold, comparison, passed, metric.reason, None,
                                      round(time.monotonic() - start, 1), round(raw, 4), verdicts or None)
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

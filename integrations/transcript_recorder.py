"""Captures the real user/agent conversation, functional checks and UI steps
for one scenario run, and writes it to transcripts/<TEST_ID>.json."""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from utils.json_io import utc_now, write_json
from utils.logger import get_logger

log = get_logger("transcript")


class TranscriptRecorder:
    def __init__(self, test_id: str, title: str, pytest_node: str, model: dict, app_url: str, out_dir: Path):
        self.out_path = Path(out_dir) / f"{test_id}.json"
        self.data: dict[str, Any] = {
            "test_id": test_id,
            "title": title,
            "pytest_node": pytest_node,
            "model": model,
            "application_under_test": {"name": "Resume Updating Agent", "url": app_url},
            "started_at": utc_now(),
            "resume_before": None,
            "conversation": [],
            "ui_events": [],
            "steps": [],
            "functional_checks": [],
            "evidence": {"screenshots": []},
            "execution_status": "NOT_EXECUTED",
            "failed_step": None,
            "error": None,
        }

    # -- conversation ---------------------------------------------------- #
    def set_resume_before(self, text: str) -> None:
        self.data["resume_before"] = text

    def user(self, content: str, source: str = "test_data") -> int:
        self.data["conversation"].append({"turn": len(self.data["conversation"]) + 1, "role": "user",
                                          "content": content, "input_source": source, "timestamp": utc_now()})
        return len(self.data["conversation"]) - 1

    def assistant(self, content: str, action: str, changed_sections: list[str], resume_after: str,
                  evaluate: bool, expected_output: str | None) -> None:
        self.data["conversation"].append({
            "turn": len(self.data["conversation"]) + 1, "role": "assistant", "content": content,
            "agent_action": action, "changed_sections": changed_sections, "resume_after": resume_after,
            "evaluate": evaluate, "expected_output": expected_output, "timestamp": utc_now(),
        })

    def ui_event(self, kind: str, input_value: str, observed: str) -> None:
        """Non-agent UI responses, e.g. validation or upload errors."""
        self.data["ui_events"].append({"kind": kind, "input": input_value, "observed": observed, "timestamp": utc_now()})

    # -- steps & checks ---------------------------------------------------- #
    @contextmanager
    def step(self, name: str):
        entry = {"step": len(self.data["steps"]) + 1, "name": name, "status": "RUNNING", "started_at": utc_now()}
        self.data["steps"].append(entry)
        log.info("[%s] Step %d: %s", self.data["test_id"], entry["step"], name)
        try:
            yield
        except BaseException as exc:
            entry["status"] = "FAILED"
            entry["error"] = _short(exc)
            if not self.data["failed_step"]:
                self.data["failed_step"] = f"Step {entry['step']}: {name}"
            raise
        else:
            entry["status"] = "PASSED"

    def check(self, name: str, passed: bool, expected: Any, actual: Any) -> bool:
        self.data["functional_checks"].append({"check": name, "passed": bool(passed),
                                               "expected": expected, "actual": actual})
        (log.info if passed else log.warning)("[%s] Check %s: %s", self.data["test_id"],
                                              "PASS" if passed else "FAIL", name)
        return passed

    def failed_checks(self) -> list[dict]:
        return [c for c in self.data["functional_checks"] if not c["passed"]]

    # -- finalisation ------------------------------------------------------ #
    def add_screenshot(self, path: Path | None) -> None:
        if path:
            self.data["evidence"]["screenshots"].append(str(Path(path).relative_to(self.out_path.parent.parent)))

    def finish(self, status: str, error: str | None = None) -> Path:
        self.data["execution_status"] = status
        self.data["error"] = error
        self.data["finished_at"] = utc_now()
        self.data["timestamp"] = self.data["finished_at"]
        for step in self.data["steps"]:
            if step["status"] == "RUNNING":
                step["status"] = "NOT_COMPLETED"
        write_json(self.out_path, self.data)
        log.info("Transcript written: %s (%s)", self.out_path, status)
        return self.out_path


def _short(exc: BaseException, limit: int = 500) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", f"{type(exc).__name__}: {exc}")
    return text if len(text) <= limit else text[: limit - 3] + "..."

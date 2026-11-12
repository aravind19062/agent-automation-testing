"""End-to-end Playwright tests for the Resume Updating Agent.

Each test maps 1:1 to a manual test case in docs/manual_test_cases.md via
@pytest.mark.test_id. All inputs and expectations come from
test_data/test_scenarios.json; this file contains only the workflow.

Functional checks are recorded individually (soft) and asserted together at
the end, so one failing expectation does not hide the others.
"""
from __future__ import annotations

import pytest

from utils.checks import apply_preservation_checks, apply_turn_checks

pytestmark = pytest.mark.ui


# --------------------------------------------------------------------------- #
# Shared workflow helpers
# --------------------------------------------------------------------------- #
def start_with_resume(agent_page, run, resume_text: str) -> None:
    with run.step("Enter the base resume"):
        agent_page.enter_resume(resume_text)
        run.set_resume_before(agent_page.current_resume())


def converse(agent_page, run, scenario: dict, label_prefix: str = "Turn") -> None:
    """Send each scripted user turn, capture the real reply, apply its checks."""
    for i, turn in enumerate(scenario["turns"], start=1):
        resume_before_turn = agent_page.current_resume()
        with run.step(f"Send user request {i}: {turn['user'][:60]}"):
            run.user(turn["user"])
            reply = agent_page.send_message(turn["user"])
            run.assistant(reply.text, reply.action, reply.changed_sections, reply.resume_after,
                          evaluate=turn.get("evaluate", False), expected_output=turn.get("expected_output"))
        with run.step(f"Verify agent response {i}"):
            apply_turn_checks(run, f"{label_prefix} {i}", turn["expect"], reply, resume_before_turn)


def verify_preserved(agent_page, run, scenario: dict, resume_data: dict) -> None:
    with run.step("Verify unrelated resume content was not changed"):
        apply_preservation_checks(run, scenario.get("preserve_sections", []), run.data["resume_before"],
                                  agent_page.current_resume(), resume_data["base_resume"]["protected_facts"])


def assert_all_checks(run) -> None:
    failed = run.failed_checks()
    if failed and not run.data["failed_step"]:
        run.data["failed_step"] = "Functional verification: " + "; ".join(c["check"] for c in failed[:3])
    assert not failed, "Functional checks failed:\n" + "\n".join(
        f"- {c['check']} | expected: {c['expected']!r} | actual: {str(c['actual'])[:200]!r}" for c in failed)


# --------------------------------------------------------------------------- #
# TC_001 - Positive
# --------------------------------------------------------------------------- #
@pytest.mark.test_id("TC_001")
@pytest.mark.positive
def test_tc001_successful_resume_update(agent_page, run, scenario, resume_data):
    start_with_resume(agent_page, run, resume_data[scenario["resume_ref"]]["text"])
    converse(agent_page, run, scenario)
    verify_preserved(agent_page, run, scenario, resume_data)
    assert_all_checks(run)


# --------------------------------------------------------------------------- #
# TC_002 - Negative / Boundary (multi-turn clarification)
# --------------------------------------------------------------------------- #
@pytest.mark.test_id("TC_002")
@pytest.mark.negative
def test_tc002_incomplete_information_clarification(agent_page, run, scenario, resume_data):
    start_with_resume(agent_page, run, resume_data[scenario["resume_ref"]]["text"])
    converse(agent_page, run, scenario)
    verify_preserved(agent_page, run, scenario, resume_data)
    assert_all_checks(run)


# --------------------------------------------------------------------------- #
# TC_003 - Negative (invalid uploads + unsupported request)
# --------------------------------------------------------------------------- #
@pytest.mark.test_id("TC_003")
@pytest.mark.negative
def test_tc003_invalid_and_unsupported_input(agent_page, run, scenario, resume_data, tmp_path):
    for upload in scenario["uploads"]:
        spec = resume_data["invalid_files"][upload["file_ref"]]
        file_path = tmp_path / spec["filename"]
        file_path.write_text(spec["content"], encoding="utf-8")
        with run.step(f"Upload invalid file {spec['filename']}"):
            observed = agent_page.upload_resume(file_path)
            run.ui_event("upload_error", spec["filename"], observed)
        with run.step(f"Verify upload error for {spec['filename']}"):
            run.check(f"Upload {spec['filename']}: error message shown", observed == upload["expected_error"],
                      upload["expected_error"], observed)
            run.check(f"Upload {spec['filename']}: resume field not populated", agent_page.current_resume() == "",
                      "", agent_page.current_resume())

    start_with_resume(agent_page, run, resume_data[scenario["resume_ref"]]["text"])
    converse(agent_page, run, scenario)
    verify_preserved(agent_page, run, scenario, resume_data)
    assert_all_checks(run)


# --------------------------------------------------------------------------- #
# TC_004 - Positive (several modifications at once)
# --------------------------------------------------------------------------- #
@pytest.mark.test_id("TC_004")
@pytest.mark.positive
def test_tc004_multiple_modifications(agent_page, run, scenario, resume_data):
    start_with_resume(agent_page, run, resume_data[scenario["resume_ref"]]["text"])
    converse(agent_page, run, scenario)
    verify_preserved(agent_page, run, scenario, resume_data)
    assert_all_checks(run)


# --------------------------------------------------------------------------- #
# TC_005 - Boundary (empty input + conflicting instructions)
# --------------------------------------------------------------------------- #
@pytest.mark.test_id("TC_005")
@pytest.mark.boundary
def test_tc005_empty_and_conflicting_input(agent_page, run, scenario, resume_data):
    start_with_resume(agent_page, run, resume_data[scenario["resume_ref"]]["text"])
    for case in scenario["empty_inputs"]:
        shown = repr(case["value"])
        with run.step(f"Submit empty/whitespace request {shown}"):
            observed = agent_page.submit_empty_or_invalid(case["value"])
            run.ui_event("validation_message", case["value"], observed)
        with run.step(f"Verify request {shown} is blocked"):
            run.check(f"Empty input {shown}: validation message", observed == case["expected_validation"],
                      case["expected_validation"], observed)
            run.check(f"Empty input {shown}: no message sent to agent", agent_page.total_message_count() == 0,
                      0, agent_page.total_message_count())

    converse(agent_page, run, scenario)
    verify_preserved(agent_page, run, scenario, resume_data)
    assert_all_checks(run)

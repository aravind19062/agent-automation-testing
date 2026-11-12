"""Supplementary tests driven by Ollama-generated inputs (marker: dynamic).

These widen coverage of TC_001 and TC_002 with varied phrasing but are NOT part
of the deterministic regression verdict, because generated text is not
reproducible. Run with:  pytest -m dynamic
Transcripts go to transcripts/supplementary/.
"""
from __future__ import annotations

import pytest

from integrations.ollama_client import OllamaError
from integrations.transcript_recorder import TranscriptRecorder
from utils.checks import sections_of

pytestmark = [pytest.mark.dynamic, pytest.mark.ui]

VARIATION_COUNT = 3
VARIATION_SKILLS = ["Python", "Playwright"]


@pytest.fixture(scope="module")
def variations(ollama_client, scenarios):
    base = scenarios["TC_001"]["turns"][0]["user"]
    try:
        return ollama_client.generate_request_variations(base, VARIATION_SKILLS, VARIATION_COUNT)
    except OllamaError as exc:
        pytest.skip(f"Ollama could not generate variations: {exc}")


def _recorder(request, settings, model_info, test_id, title):
    return TranscriptRecorder(test_id, title, request.node.nodeid, model_info, settings.app_base_url,
                              settings.transcripts_dir / "supplementary")


def _finish(request, rec, agent_page, settings):
    failed = rec.failed_checks()
    shot = agent_page.screenshot(settings.screenshots_dir / f"{rec.data['test_id']}_{'FAIL' if failed else 'PASS'}.png")
    rec.add_screenshot(shot)
    rec.finish("FAIL" if failed else "PASS", "; ".join(c["check"] for c in failed) or None)
    assert not failed, [c["check"] for c in failed]


@pytest.mark.parametrize("index", range(VARIATION_COUNT))
def test_tc001_variation_paraphrased_request(index, request, agent_page, variations, resume_data, settings, model_info):
    """TC_001 traceability: paraphrased 'add skills' requests must give the same outcome."""
    if index >= len(variations):
        pytest.skip("Fewer valid variations were generated than requested")
    text = variations[index]
    rec = _recorder(request, settings, model_info, f"TC_001-V{index + 1}", "TC_001 variation (Ollama-generated input)")
    resume = resume_data["base_resume"]["text"]
    agent_page.enter_resume(resume)
    rec.set_resume_before(resume)
    rec.user(text, source=f"ollama:{model_info['name']}")
    reply = agent_page.send_message(text)
    rec.assistant(reply.text, reply.action, reply.changed_sections, reply.resume_after, False, None)
    skills = sections_of(reply.resume_after).get("SKILLS", "")
    rec.check("agent action is 'update'", reply.action == "update", "update", reply.action)
    for skill in VARIATION_SKILLS + ["Selenium"]:
        rec.check(f"SKILLS contains {skill}", skill.lower() in skills.lower(), skill, skills)
    _finish(request, rec, agent_page, settings)


def test_tc002_dynamic_clarification_answer(request, agent_page, ollama_client, scenarios, resume_data, settings, model_info):
    """TC_002 traceability: Ollama answers the agent's real clarification question."""
    scenario = scenarios["TC_002"]
    rec = _recorder(request, settings, model_info, "TC_002-D1", "TC_002 with Ollama-generated clarification answer")
    resume = resume_data["base_resume"]["text"]
    agent_page.enter_resume(resume)
    rec.set_resume_before(resume)

    first = scenario["turns"][0]["user"]
    rec.user(first)
    reply1 = agent_page.send_message(first)
    rec.assistant(reply1.text, reply1.action, reply1.changed_sections, reply1.resume_after, False, None)
    rec.check("turn 1 asks for clarification", reply1.action == "clarify", "clarify", reply1.action)
    if reply1.action != "clarify":
        _finish(request, rec, agent_page, settings)

    try:
        answer = ollama_client.generate_clarification_answer(reply1.text, scenario["clarification_facts"])
    except OllamaError as exc:
        pytest.skip(f"Ollama could not produce a valid clarification answer: {exc}")
    rec.user(answer, source=f"ollama:{model_info['name']}")
    reply2 = agent_page.send_message(answer)
    rec.assistant(reply2.text, reply2.action, reply2.changed_sections, reply2.resume_after, False, None)
    experience = sections_of(reply2.resume_after).get("EXPERIENCE", "")
    rec.check("turn 2 updates the resume", reply2.action == "update", "update", reply2.action)
    for key in ("job_title", "company"):
        value = scenario["clarification_facts"][key].replace(" Inc", "")
        rec.check(f"EXPERIENCE contains {value}", value.lower() in experience.lower(), value, experience)
    for kept in ("Northwind Labs", "Contoso Retail"):
        rec.check(f"existing role {kept} kept", kept in experience, kept, experience)
    _finish(request, rec, agent_page, settings)

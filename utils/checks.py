"""Data-driven functional checks. Each rule in a scenario's ``expect`` block
becomes one named, recorded check so reports show exactly what failed."""
from __future__ import annotations

from integrations.transcript_recorder import TranscriptRecorder
from pages.resume_agent_page import AgentReply
from resume_agent_app.agent import parse_sections


def sections_of(resume: str) -> dict[str, str]:
    return {name: body.strip() for name, body in parse_sections(resume)}


def apply_turn_checks(rec: TranscriptRecorder, label: str, expect: dict, reply: AgentReply, resume_before: str) -> None:
    after = sections_of(reply.resume_after)
    before = sections_of(resume_before)
    reply_l = reply.text.lower()

    if "action" in expect:
        rec.check(f"{label}: agent action is '{expect['action']}'", reply.action == expect["action"],
                  expect["action"], reply.action)
    if expect.get("resume_unchanged"):
        rec.check(f"{label}: resume is unchanged", reply.resume_after.strip() == resume_before.strip(),
                  "identical to resume before the turn", "unchanged" if reply.resume_after.strip() == resume_before.strip()
                  else "changed")
    for section in expect.get("changed_sections_include", []):
        rec.check(f"{label}: '{section}' reported as changed", section in reply.changed_sections,
                  section, reply.changed_sections)
    for section, needles in expect.get("section_contains", {}).items():
        body = after.get(section, "")
        for needle in needles:
            rec.check(f"{label}: {section} contains '{needle}'", needle.lower() in body.lower(), needle, body)
    for section, needles in expect.get("section_must_not_contain", {}).items():
        body = after.get(section, "")
        for needle in needles:
            rec.check(f"{label}: {section} does not contain '{needle}'", needle.lower() not in body.lower(),
                      f"no '{needle}'", body)
    for needle, max_n in expect.get("max_occurrences", {}).items():
        n = reply.resume_after.count(needle)
        rec.check(f"{label}: '{needle}' appears at most {max_n}x (no duplicated achievements)", n <= max_n,
                  f"<= {max_n}", n)
    if expect.get("reply_contains_any"):
        options = expect["reply_contains_any"]
        rec.check(f"{label}: reply mentions one of {options}", any(o.lower() in reply_l for o in options),
                  options, reply.text)
    for needle in expect.get("reply_contains_all", []):
        rec.check(f"{label}: reply mentions '{needle}'", needle.lower() in reply_l, needle, reply.text)
    for needle in expect.get("reply_must_not_contain", []):
        rec.check(f"{label}: reply does not invent '{needle}'", needle.lower() not in reply_l, f"no '{needle}'", reply.text)
    _ = before  # kept for symmetry / debugging


def apply_preservation_checks(rec: TranscriptRecorder, preserve: list[str], resume_before: str, resume_after: str,
                              protected_facts: list[str]) -> None:
    before, after = sections_of(resume_before), sections_of(resume_after)
    for section in preserve:
        rec.check(f"Unrelated section {section} unchanged", before.get(section) == after.get(section),
                  before.get(section), after.get(section))
    for fact in protected_facts:
        rec.check(f"Protected fact '{fact}' still present", fact in resume_after, fact,
                  "present" if fact in resume_after else "missing")

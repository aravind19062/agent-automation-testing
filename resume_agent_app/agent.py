"""Resume Updating Agent — the application under test.

The agent receives the user's current resume plus a natural-language request and
decides one of three actions:

* ``update``  – the request is a resume edit with enough detail; the agent returns
                section-level edits which are applied in code, so sections the
                user did not mention are preserved byte-for-byte.
* ``clarify`` – the request is a resume edit but is missing details or is
                self-contradictory; the agent asks a follow-up question.
* ``reject``  – the request is not about editing the resume.

The decision and the edits are produced by a local Ollama model using
schema-constrained JSON output.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

import requests
from pydantic import BaseModel, ValidationError

log = logging.getLogger("resume_agent")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
AGENT_MODEL = os.getenv("AGENT_MODEL", "gemma2:2b")
OLLAMA_TIMEOUT = int(os.getenv("AGENT_OLLAMA_TIMEOUT", "180"))

HEADER_SECTION = "HEADER"
_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 &/\-]{2,}$")


class AgentError(RuntimeError):
    """Raised when the underlying model cannot produce a usable answer."""


# --------------------------------------------------------------------------- #
# Resume parsing
# --------------------------------------------------------------------------- #
def parse_sections(text: str) -> list[tuple[str, str]]:
    """Split a plain-text resume into ordered (SECTION NAME, body) pairs.

    A heading is a line written entirely in capitals (e.g. ``SKILLS``). Lines
    before the first heading belong to the ``HEADER`` section (name, contact).
    """
    sections: list[tuple[str, list[str]]] = [(HEADER_SECTION, [])]
    for line in text.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _HEADING_RE.match(stripped):
            sections.append((stripped, []))
        else:
            sections[-1][1].append(line)
    result = [(name, "\n".join(body).strip("\n")) for name, body in sections]
    if result and result[0][0] == HEADER_SECTION and not result[0][1].strip():
        result = result[1:]
    return result


def render_sections(sections: list[tuple[str, str]]) -> str:
    parts = []
    for name, body in sections:
        parts.append(body if name == HEADER_SECTION else f"{name}\n{body}")
    return "\n\n".join(p.strip("\n") for p in parts).strip() + "\n"


# --------------------------------------------------------------------------- #
# Model contract
# --------------------------------------------------------------------------- #
class SectionEdit(BaseModel):
    section: str
    mode: Literal["append", "replace"]
    content: str


class AgentDecision(BaseModel):
    action: Literal["update", "clarify", "reject"]
    reply: str
    edits: list[SectionEdit]


@dataclass
class AgentResult:
    action: str
    reply: str
    updated_resume: str | None = None
    changed_sections: list[str] = field(default_factory=list)
    model: str = AGENT_MODEL


SYSTEM_PROMPT = """You are ResumeBot, an assistant that edits a user's plain-text resume.
You receive the CURRENT RESUME (split into sections) and the conversation so far.
Decide exactly one action for the user's LATEST message:

"update"  - The message asks to change the resume AND gives every detail needed.
            Return "edits": one item per section that changes. "section" is the
            existing section name in CAPITALS (or a new CAPITALS name).
            "mode":"append" adds NEW lines to the end of the section; "content"
            holds ONLY the new lines (for SKILLS, only the new skill names).
            "mode":"replace" rewrites the section; "content" is the complete new
            section text. Use "append" for additions, "replace" only when the user
            asks to change, rewrite or remove existing text.
            "reply" briefly says what you changed.
"clarify" - The message asks to change the resume but required details are
            missing (for example a new job without company, job title or dates,
            or "add my certification" without naming it), or the instructions
            contradict each other. "reply" asks a specific question. "edits": [].
"reject"  - The message is not a request to edit this resume (general
            questions, travel, coding, jokes, anything unrelated). "reply"
            politely says you can only help update the resume. "edits": [].

Strict rules:
- NEVER invent facts: no new employers, dates, titles, degrees, numbers, skills
  or contact details unless the user stated them or they already exist.
- Earlier user messages in the conversation count as provided details.
- Skills always go in the SKILLS section. When several changes are requested,
  return one edit for EVERY affected section.
- Quote the user's exact values (numbers, names) in your reply; never reuse
  values from the examples.
- Respond with JSON only.

Examples (placeholders in <angle brackets> stand for the user's real values):
User: Add <skill X> to my skills.
{"action":"update","reply":"Added <skill X> to your Skills section.","edits":[{"section":"SKILLS","mode":"append","content":"<skill X>"}]}
User: Add my job as <title> at <company> since <date>; I <achievement>.
{"action":"update","reply":"Added your <title> role at <company> to Experience.","edits":[{"section":"EXPERIENCE","mode":"append","content":"<title>, <company> (<date> - Present)\\n- <achievement>"}]}
User: Update my summary to mention <fact A> and add <skill Y> to my skills.
{"action":"update","reply":"Updated your Summary and added <skill Y> to Skills.","edits":[{"section":"SUMMARY","mode":"replace","content":"<summary rewritten with fact A>"},{"section":"SKILLS","mode":"append","content":"<skill Y>"}]}
User: Add my new job to my experience.
{"action":"clarify","reply":"Happy to add it. What is the company name, your job title, and your start date?","edits":[]}
User: Set my experience to <N1> years and also to <N2> years.
{"action":"clarify","reply":"You asked for both <N1> and <N2> years. Which one should I use?","edits":[]}
User: <a question unrelated to the resume>
{"action":"reject","reply":"I can only help you update your resume. What would you like to change?","edits":[]}
"""


_REMOVAL_WORDS = re.compile(r"\b(remove|delete|drop|replace|change|update|rewrite|reword|shorten|edit|fix|correct|rename|set|make)\b", re.I)
_REMOVE_ONLY = re.compile(r"\b(remove|delete|drop|replace)\b", re.I)
NEEDS_DETAILS_REPLY = ("I need a few more details before I can make that change. Please tell me exactly "
                       "what to add - for example the company, job title and dates for a job, or the name "
                       "and issuer for a certification.")


def _norm_lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


def _fact_tokens(lines: list[str]) -> set[str]:
    """Numbers and mid-sentence capitalised words: the 'facts' a line asserts."""
    facts: set[str] = set()
    for line in lines:
        line = re.sub(r"^[-*\u2022]\s*", "", line)
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            words = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*|\d+(?:\.\d+)?", sentence)
            for i, word in enumerate(words):
                word = word.rstrip(".")
                if word[:1].isdigit() or (i > 0 and word[:1].isupper()):
                    facts.add(word.lower())
    return facts


def _apply_mode(section: str, old: str, edit: "SectionEdit", message: str) -> str:
    """Build the new section text. Appends are merged in code so nothing is lost."""
    new = edit.content.strip("\n")
    if not old.strip():
        return new
    if section == "SKILLS" and "\n" not in old.strip() and not _REMOVE_ONLY.search(message):
        # Skills are a comma list: merge, never silently drop existing skills.
        existing = [x.strip() for x in old.split(",") if x.strip()]
        additions = [x.strip() for x in re.split(r"[,\n]", new) if x.strip()]
        merged = existing + [x for x in additions if x.lower() not in {e.lower() for e in existing}]
        return ", ".join(merged)
    if edit.mode == "replace" and _REMOVAL_WORDS.search(message):
        return new
    old_lines = set(_norm_lines(old))
    extra = [ln for ln in new.split("\n") if ln.strip() and ln.strip() not in old_lines]
    if not extra:
        return old
    joiner = "\n" if section == "SUMMARY" else "\n\n" if section == "EXPERIENCE" else "\n"
    return old.rstrip("\n") + joiner + "\n".join(extra)


_STOPWORDS = {"and", "the", "for", "with", "from", "into", "that", "this", "your", "my", "our", "are", "was", "have"}


def _content_words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Za-z0-9+#]+", text) if len(w) >= 3 and w.lower() not in _STOPWORDS}


def drop_copied_lines(content: str, resume_lines: list[str], user_text: str) -> tuple[str, list[str]]:
    """Remove appended lines that recycle existing resume content the user did not ask for.

    A line is "copied" when most of its words come from one existing resume line
    and few come from the user's messages, e.g. an achievement from a previous job
    pasted into a new job entry (it would misattribute that achievement).
    """
    user_words = _content_words(user_text)
    existing = [_content_words(ln) for ln in resume_lines]
    kept, dropped = [], []
    for line in content.split("\n"):
        words = _content_words(line)
        if len(words) >= 3:
            from_resume = max((len(words & e) / len(words) for e in existing), default=0.0)
            from_user = len(words & user_words) / len(words)
            if from_resume >= 0.6 and from_user < 0.5:
                dropped.append(line.strip())
                continue
        kept.append(line)
    return "\n".join(kept).strip("\n"), dropped


def check_edit(old: str, new: str, sources: str, message: str) -> str | None:
    """Return a reason string if the edit must be rejected, else None."""
    old_lines, new_lines = _norm_lines(old), _norm_lines(new)
    if not _REMOVAL_WORDS.search(message):
        missing = [ln for ln in old_lines if not any(ln in nl for nl in new_lines)]
        if missing:
            return f"edit drops existing lines without being asked: {missing[:2]}"
    added = [ln for ln in new_lines if ln not in old_lines]  # merged lines are re-checked in full
    known = {w.lower().rstrip(".") for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*|\d+(?:\.\d+)?", sources)}
    unsupported = sorted(_fact_tokens(added) - known)
    if unsupported:
        return f"edit introduces unsupported facts: {unsupported[:5]}"
    return None


def _format_resume(sections: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"[{name}]\n{body}" for name, body in sections)


def _call_model(messages: list[dict]) -> AgentDecision:
    payload = {
        "model": AGENT_MODEL,
        "messages": messages,
        "stream": False,
        "format": AgentDecision.model_json_schema(),
        "options": {"temperature": 0, "seed": 7},
    }
    try:
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AgentError(f"Model service unavailable: {exc}") from exc
    content = resp.json().get("message", {}).get("content", "")
    try:
        return AgentDecision.model_validate_json(content)
    except ValidationError as exc:
        raise AgentError(f"Model returned malformed output: {content[:200]!r}") from exc


def run_agent(resume: str, message: str, history: list[dict] | None = None) -> AgentResult:
    """Process one user turn and return the agent's decision."""
    sections = parse_sections(resume)
    convo = [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": "CURRENT RESUME:\n" + _format_resume(sections)},
             {"role": "assistant", "content": '{"action":"clarify","reply":"Resume received. What would you like to change?","edits":[]}'}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            convo.append({"role": turn["role"], "content": str(turn["content"])})
    convo.append({"role": "user", "content": message})

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            decision = _call_model(convo)
            break
        except AgentError as exc:
            last_error = exc
            log.warning("Agent model attempt %d failed: %s", attempt + 1, exc)
    else:
        raise AgentError(str(last_error))

    log.info("Agent decision=%s edits=%s", decision.action, [e.section for e in decision.edits])

    if decision.action != "update":
        return AgentResult(action=decision.action, reply=decision.reply.strip())

    edits = [e for e in decision.edits if e.section.strip() and e.content.strip()]
    if not edits:
        return AgentResult(
            action="clarify",
            reply="I could not tell exactly what to change. Which section should I update, and with what details?",
        )

    sources = resume + "\n" + "\n".join(
        str(t.get("content", "")) for t in (history or []) if t.get("role") == "user") + "\n" + message
    user_text = "\n".join(str(t.get("content", "")) for t in (history or []) if t.get("role") == "user") + "\n" + message
    state = _EditState(sections, user_text)
    problem = state.apply(edits, sources, message)
    if problem:
        return AgentResult(action="clarify", reply=NEEDS_DETAILS_REPLY)

    # Multi-part requests: a small model often edits only one of the sections the
    # user named. Ask again, one section at a time, for anything it skipped.
    skipped = [s for s in requested_sections(message, state.names()) if s not in state.changed]
    for section in skipped:
        log.info("Requested section %s was not edited; asking the model for it specifically", section)
        repair = _request_section_edit(convo, section, message)
        if repair and not state.apply(repair, sources, message):
            continue
        log.warning("Could not obtain a valid edit for requested section %s", section)

    if not state.changed:
        return AgentResult(action="clarify",
                           reply="I did not find any new details to apply. What exactly should I add or change?")
    still_missing = [s for s in requested_sections(message, state.names()) if s not in state.changed]
    reply = decision.reply.strip()
    if skipped:  # the model's own reply was written before the repair, so it can't be trusted
        reply = honest_reply(state.changed, still_missing)
    return AgentResult(action="update", reply=reply,
                       updated_resume=render_sections(state.sections), changed_sections=state.changed)


class _EditState:
    """Resume sections being edited, plus which ones actually changed."""

    def __init__(self, sections: list[tuple[str, str]], user_text: str = ""):
        self.sections = list(sections)
        self.user_text = user_text
        self.resume_lines = [ln for _, body in sections for ln in _norm_lines(body)]
        self.index = {name.upper(): i for i, (name, _) in enumerate(self.sections)}
        self.changed: list[str] = []

    def names(self) -> list[str]:
        return [name for name, _ in self.sections]

    def apply(self, edits: list[SectionEdit], sources: str, message: str) -> str | None:
        """Validate and apply edits. Returns a guard problem (nothing applied) or None."""
        staged = []
        for edit in edits:
            name = edit.section.strip().strip("[]").upper()
            old = self.sections[self.index[name]][1] if name in self.index else ""
            if name != "SKILLS" and (edit.mode == "append" or not old.strip()):
                cleaned, dropped = drop_copied_lines(edit.content, self.resume_lines, self.user_text)
                if dropped:
                    log.warning("Dropped lines copied from existing resume content into %s: %s", name, dropped)
                    edit = SectionEdit(section=edit.section, mode=edit.mode, content=cleaned)
                if not cleaned.strip():
                    continue
            content = _apply_mode(name, old, edit, message)
            problem = check_edit(old, content, sources, message)
            if problem:
                log.warning("Guard rejected edit to %s: %s", name, problem)
                return problem
            staged.append((name, old, content))
        for name, old, content in staged:
            if name in self.index:
                if old.strip() != content.strip():
                    idx = self.index[name]
                    self.sections[idx] = (self.sections[idx][0], content)
                    if self.sections[idx][0] not in self.changed:
                        self.changed.append(self.sections[idx][0])
            else:
                self.sections.append((name, content))
                self.index[name] = len(self.sections) - 1
                self.changed.append(name)
        return None


_SECTION_ALIASES = {
    "SKILLS": r"skills?",
    "SUMMARY": r"summary|profile|objective",
    "EXPERIENCE": r"experience|work history|employment",
    "EDUCATION": r"education",
}


def requested_sections(message: str, existing: list[str]) -> list[str]:
    """Sections the user explicitly targets, e.g. "to my skills", "update my summary".

    Deliberately narrow: "5 years of experience" does NOT target EXPERIENCE.
    """
    found = []
    for section, alias in _SECTION_ALIASES.items():
        if section not in existing:
            continue
        pattern = (rf"\b(?:to|in|into|under|on|from)\s+(?:my|the)\s+(?:{alias})\b"
                   rf"|\b(?:update|change|rewrite|edit|revise|fix)\s+(?:my|the)\s+(?:{alias})\b"
                   rf"|\b(?:my|the)\s+(?:{alias})\s+section\b")
        if re.search(pattern, message, re.I):
            found.append(section)
    return found


def _request_section_edit(convo: list[dict], section: str, message: str) -> list[SectionEdit] | None:
    follow_up = convo + [{"role": "user", "content": (
        f"For my request \"{message}\", return ONLY the edit for the {section} section. "
        f"Use action \"update\" and exactly one edit with section \"{section}\". "
        "Use mode \"append\" for additions. Do not include any other section.")}]
    try:
        decision = _call_model(follow_up)
    except AgentError as exc:
        log.warning("Section repair call failed for %s: %s", section, exc)
        return None
    edits = [e for e in decision.edits if e.section.strip().strip("[]").upper() == section and e.content.strip()]
    return edits or None


def honest_reply(changed: list[str], missing: list[str]) -> str:
    reply = "Updated your " + " and ".join(s.title() for s in changed) + " section" + ("s" if len(changed) > 1 else "") + "."
    if missing:
        reply += (" I could not update your " + " and ".join(s.title() for s in missing)
                  + " section - please tell me exactly what to change there.")
    return reply


def model_info() -> dict:
    """Return model name and version details reported by Ollama."""
    try:
        resp = requests.post(f"{OLLAMA_BASE_URL}/api/show", json={"model": AGENT_MODEL}, timeout=15)
        resp.raise_for_status()
        details = resp.json().get("details", {})
        return {"name": AGENT_MODEL, "parameter_size": details.get("parameter_size"),
                "quantization": details.get("quantization_level"), "family": details.get("family")}
    except requests.RequestException as exc:
        return {"name": AGENT_MODEL, "error": str(exc)}


if __name__ == "__main__":  # manual smoke check
    print(json.dumps(model_info(), indent=2))

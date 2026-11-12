"""Reusable client for the local Ollama server.

Used by the framework to (a) record model name/version for transcripts and
(b) generate supplementary, varied test inputs. Core regression tests never
depend on generated data; see tests/test_dynamic_inputs.py.
"""
from __future__ import annotations

import json
import time
from typing import Any

import requests

from utils.logger import get_logger

log = get_logger("ollama")


class OllamaError(RuntimeError):
    """Connection, HTTP or response-format failure talking to Ollama."""


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout_s: int = 180, retries: int = 3, backoff_s: float = 2.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.retries = max(1, retries)
        self.backoff_s = backoff_s

    # ------------------------------------------------------------------ #
    # Low level
    # ------------------------------------------------------------------ #
    def _post(self, path: str, payload: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = requests.post(f"{self.base_url}{path}", json=payload, timeout=self.timeout_s)
                if resp.status_code == 404 and "not found" in resp.text.lower():
                    raise OllamaError(f"Model '{self.model}' is not installed. Run: ollama pull {self.model}")
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                resp.raise_for_status()
                return resp.json()
            except OllamaError:
                raise
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError, ValueError) as exc:
                last_exc = exc
                log.warning("Ollama %s attempt %d/%d failed: %s", path, attempt, self.retries, exc)
                if attempt < self.retries:
                    time.sleep(self.backoff_s * attempt)
        raise OllamaError(f"Ollama request to {path} failed after {self.retries} attempts: {last_exc}")

    def is_available(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/api/version", timeout=5).ok
        except requests.RequestException:
            return False

    def server_version(self) -> str | None:
        try:
            return requests.get(f"{self.base_url}/api/version", timeout=5).json().get("version")
        except (requests.RequestException, ValueError):
            return None

    def model_info(self) -> dict[str, Any]:
        """Name + version metadata for transcripts and reports."""
        info: dict[str, Any] = {"name": self.model, "version": self.model.split(":")[-1] if ":" in self.model else "latest",
                                "provider": "ollama", "ollama_server_version": self.server_version()}
        try:
            data = self._post("/api/show", {"model": self.model})
            details = data.get("details", {})
            info.update(parameter_size=details.get("parameter_size"), quantization=details.get("quantization_level"),
                        family=details.get("family"), format=details.get("format"))
            tags = requests.get(f"{self.base_url}/api/tags", timeout=10).json().get("models", [])
            digest = next((m.get("digest") for m in tags if m.get("name") == self.model), None)
            info["digest"] = digest[:12] if digest else None
        except (OllamaError, requests.RequestException, ValueError) as exc:
            info["error"] = str(exc)
        return info

    def generate(self, prompt: str, *, system: str | None = None, json_schema: dict | None = None,
                 temperature: float = 0.7, seed: int | None = None) -> str:
        """Single completion. Retries on transport errors and on empty/invalid output."""
        options: dict[str, Any] = {"temperature": temperature}
        if seed is not None:
            options["seed"] = seed
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt, "stream": False, "options": options}
        if system:
            payload["system"] = system
        if json_schema:
            payload["format"] = json_schema
        for attempt in range(1, self.retries + 1):
            text = (self._post("/api/generate", payload).get("response") or "").strip()
            if not text:
                log.warning("Empty model response (attempt %d/%d)", attempt, self.retries)
                continue
            if json_schema:
                try:
                    json.loads(text)
                except ValueError:
                    log.warning("Invalid JSON from model (attempt %d/%d): %r", attempt, self.retries, text[:120])
                    continue
            return text
        raise OllamaError("Model returned no usable output after retries")

    # ------------------------------------------------------------------ #
    # Test-input generation
    # ------------------------------------------------------------------ #
    def generate_request_variations(self, base_request: str, must_keep: list[str], count: int = 3,
                                    seed: int | None = None) -> list[str]:
        """Paraphrase a resume-edit request while keeping key terms verbatim."""
        schema = {"type": "object", "properties": {"variations": {"type": "array", "items": {"type": "string"}}},
                  "required": ["variations"]}
        prompt = (f"Rewrite this request to a resume-editing assistant in {count} different ways, as a real user "
                  f"might type it. Keep these exact words in every version: {', '.join(must_keep)}. "
                  f"Do not add any other skills, names or numbers.\nRequest: {base_request}\n"
                  'Answer as JSON: {"variations": ["...", "..."]}')
        data = json.loads(self.generate(prompt, json_schema=schema, temperature=0.8, seed=seed))
        variations = [v.strip() for v in data.get("variations", []) if isinstance(v, str) and v.strip()]
        valid = [v for v in variations if all(k.lower() in v.lower() for k in must_keep)]
        log.info("Generated %d/%d valid variations of %r", len(valid), len(variations), base_request)
        if not valid:
            raise OllamaError(f"No valid variations generated (raw: {variations})")
        return valid[:count]

    def generate_clarification_answer(self, agent_question: str, facts: dict[str, str], seed: int | None = None) -> str:
        """Act as the user: answer the agent's follow-up question using ONLY the given facts."""
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
        fact_lines = "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in facts.items())
        prompt = ("You are a job seeker chatting with a resume assistant. The assistant asked:\n"
                  f'"{agent_question}"\n\nReply in one or two natural sentences using ONLY these facts, '
                  f"copied exactly:\n{fact_lines}\nDo not add anything else.\n"
                  'Answer as JSON: {"answer": "..."}')
        answer = json.loads(self.generate(prompt, json_schema=schema, temperature=0.6, seed=seed)).get("answer", "").strip()
        missing = [v for k, v in facts.items() if k != "highlight" and v.lower() not in answer.lower()]
        if missing:
            raise OllamaError(f"Generated answer omitted required facts {missing}: {answer!r}")
        return answer

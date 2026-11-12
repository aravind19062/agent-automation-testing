"""Central configuration. Every environment-specific value comes from the
environment or a local, git-ignored ``.env`` file — nothing is hardcoded."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# DeepEval must never phone home or fall back to a hosted provider.
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_UPDATE_WARNING_OPT_OUT", "YES")
os.environ.setdefault("ERROR_REPORTING", "NO")
# Small local judges on modest hardware can exceed DeepEval's default per-call
# timeout (~90s); a timeout is recorded as a failed metric, so allow more time.
os.environ.setdefault("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE",
                      os.getenv("JUDGE_TIMEOUT_S", "300"))


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required setting {name}. Copy .env.example to .env and set it.")
    return value


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Thresholds:
    answer_relevancy: float
    faithfulness: float
    hallucination: float


@dataclass(frozen=True)
class Settings:
    app_base_url: str
    auto_start_app: bool
    ollama_base_url: str
    ollama_model: str
    judge_model: str
    ollama_timeout_s: int
    ollama_retries: int
    headless: bool
    slow_mo_ms: int
    default_timeout_ms: int
    agent_timeout_ms: int
    thresholds: Thresholds
    test_data_dir: Path = PROJECT_ROOT / "test_data"
    transcripts_dir: Path = PROJECT_ROOT / "transcripts"
    reports_dir: Path = PROJECT_ROOT / "reports"
    screenshots_dir: Path = PROJECT_ROOT / "screenshots"
    logs_dir: Path = PROJECT_ROOT / "logs"

    def ensure_dirs(self) -> None:
        for d in (self.transcripts_dir, self.reports_dir, self.screenshots_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    model = os.getenv("OLLAMA_MODEL", "gemma2:2b")
    settings = Settings(
        app_base_url=_required("APP_BASE_URL").rstrip("/"),
        auto_start_app=_bool("AUTO_START_APP", True),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=model,
        judge_model=os.getenv("JUDGE_MODEL", model),
        ollama_timeout_s=int(os.getenv("OLLAMA_TIMEOUT_S", "180")),
        ollama_retries=int(os.getenv("OLLAMA_RETRIES", "3")),
        headless=_bool("HEADLESS", True),
        slow_mo_ms=int(os.getenv("SLOW_MO_MS", "0")),
        default_timeout_ms=int(os.getenv("DEFAULT_TIMEOUT_MS", "15000")),
        agent_timeout_ms=int(os.getenv("AGENT_TIMEOUT_MS", "180000")),
        thresholds=Thresholds(
            answer_relevancy=float(os.getenv("THRESHOLD_ANSWER_RELEVANCY", "0.70")),
            faithfulness=float(os.getenv("THRESHOLD_FAITHFULNESS", "0.70")),
            hallucination=float(os.getenv("THRESHOLD_HALLUCINATION", "0.30")),
        ),
    )
    settings.ensure_dirs()
    return settings

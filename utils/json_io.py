"""Small helpers for reading/writing JSON artefacts safely."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path: Path, data: Any) -> Path:
    """Atomic write so a crashed run never leaves a half-written report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def load_scenarios(test_data_dir: Path) -> dict[str, dict]:
    data = read_json(test_data_dir / "test_scenarios.json")
    if not data:
        raise FileNotFoundError(f"No scenarios found in {test_data_dir / 'test_scenarios.json'}")
    return {s["test_id"]: s for s in data["scenarios"]}


def load_resume_data(test_data_dir: Path) -> dict:
    return read_json(test_data_dir / "resume_data.json")

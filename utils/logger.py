"""Framework-wide logging: console + logs/framework.log."""
from __future__ import annotations

import logging
from pathlib import Path

_CONFIGURED = False
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def get_logger(name: str = "framework") -> logging.Logger:
    global _CONFIGURED
    if not _CONFIGURED:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
        log_dir.mkdir(exist_ok=True)
        root = logging.getLogger("aat")
        root.setLevel(logging.INFO)
        file_handler = logging.FileHandler(log_dir / "framework.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(file_handler)
        root.propagate = True  # pytest's log capture / live logging still sees records
        _CONFIGURED = True
    return logging.getLogger(f"aat.{name}")

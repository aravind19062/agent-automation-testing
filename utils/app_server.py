"""Ensures the application under test is reachable before UI tests run.

If ``AUTO_START_APP`` is true and ``APP_BASE_URL`` points at this machine, the
bundled Resume Updating Agent is started with uvicorn and stopped afterwards.
For any other URL the framework only checks reachability.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from utils.logger import get_logger

log = get_logger("app_server")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_healthy(base_url: str) -> bool:
    try:
        return requests.get(f"{base_url}/api/health", timeout=5).status_code == 200
    except requests.RequestException:
        return False


class AppServer:
    def __init__(self, base_url: str, auto_start: bool, project_root: Path, log_dir: Path):
        self.base_url = base_url
        self.auto_start = auto_start
        self.project_root = project_root
        self.log_path = log_dir / "app_server.log"
        self._proc: subprocess.Popen | None = None

    def start(self) -> None:
        if is_healthy(self.base_url):
            log.info("Application already running at %s", self.base_url)
            return
        parsed = urlparse(self.base_url)
        if not self.auto_start or parsed.hostname not in LOCAL_HOSTS:
            raise RuntimeError(f"Application is not reachable at {self.base_url} (/api/health failed).")
        port = str(parsed.port or 80)
        log.info("Starting Resume Updating Agent on %s:%s", parsed.hostname, port)
        self._log_fh = open(self.log_path, "a", encoding="utf-8")
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "resume_agent_app.main:app", "--host", parsed.hostname, "--port", port],
            cwd=self.project_root, stdout=self._log_fh, stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(f"Application exited early; see {self.log_path}")
            if is_healthy(self.base_url):
                log.info("Application is healthy")
                return
            time.sleep(0.5)
        self.stop()
        raise RuntimeError(f"Application did not become healthy within 30s; see {self.log_path}")

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            log.info("Stopping application server")
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

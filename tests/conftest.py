"""Shared pytest fixtures: configuration, app lifecycle, Ollama, browser,
page objects, transcript recording, screenshots and report generation."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings  # noqa: E402
from integrations.ollama_client import OllamaClient  # noqa: E402
from integrations.transcript_recorder import TranscriptRecorder  # noqa: E402
from pages.resume_agent_page import ResumeAgentPage  # noqa: E402
from utils.app_server import AppServer  # noqa: E402
from utils.json_io import load_resume_data, load_scenarios, utc_now  # noqa: E402
from utils.logger import get_logger  # noqa: E402

log = get_logger("conftest")


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Expose each phase's report on the item so fixtures know pass/fail."""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


def pytest_sessionfinish(session, exitstatus):
    if session.config.getoption("--no-report", default=False) or session.config.option.collectonly:
        return
    try:
        from reporting.report_generator import generate_reports
        paths = generate_reports(get_settings())
        log.info("Reports generated: %s", ", ".join(str(p) for p in paths))
    except Exception as exc:  # reporting must never hide test results
        log.error("Report generation failed: %s", exc)


def pytest_collection_modifyitems(session, config, items):
    """A run that re-executes UI scenarios starts from a clean slate, so reports
    can never mix results from different runs."""
    if config.option.collectonly:  # listing tests must never touch existing evidence
        return
    settings = get_settings()
    rerun_ids = {m.args[0] for item in items for m in item.iter_markers("test_id")}
    if not rerun_ids:
        return
    from utils.json_io import read_json, write_json
    for test_id in rerun_ids:
        (settings.transcripts_dir / f"{test_id}.json").unlink(missing_ok=True)
    results_file = settings.reports_dir / "evaluation_results.json"
    results = read_json(results_file, default={}) or {}
    if any(k in results for k in rerun_ids):
        write_json(results_file, {k: v for k, v in results.items() if k not in rerun_ids})


def pytest_addoption(parser):
    parser.addoption("--no-report", action="store_true", help="Skip report generation at session end.")


# --------------------------------------------------------------------------- #
# Configuration & data
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def scenarios(settings):
    return load_scenarios(settings.test_data_dir)


@pytest.fixture(scope="session")
def resume_data(settings):
    return load_resume_data(settings.test_data_dir)


@pytest.fixture
def scenario(request, scenarios):
    marker = request.node.get_closest_marker("test_id")
    if not marker:
        pytest.fail("UI scenario tests must declare @pytest.mark.test_id('TC_xxx')")
    return scenarios[marker.args[0]]


# --------------------------------------------------------------------------- #
# External services
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def ollama_client(settings):
    client = OllamaClient(settings.ollama_base_url, settings.ollama_model,
                          settings.ollama_timeout_s, settings.ollama_retries)
    if not client.is_available():
        pytest.exit(f"Ollama is not reachable at {settings.ollama_base_url}. Start it with `ollama serve`.", returncode=3)
    return client


@pytest.fixture(scope="session")
def model_info(ollama_client):
    info = ollama_client.model_info()
    if info.get("error"):
        pytest.exit(f"Ollama model problem: {info['error']}", returncode=3)
    return info


@pytest.fixture(scope="session")
def app_server(settings, model_info):
    server = AppServer(settings.app_base_url, settings.auto_start_app, PROJECT_ROOT, settings.logs_dir)
    try:
        server.start()
    except RuntimeError as exc:
        pytest.exit(str(exc), returncode=3)
    yield server
    server.stop()


# --------------------------------------------------------------------------- #
# Browser (Playwright sync API; one browser per session, one context per test)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="session")
def browser(settings):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=settings.headless, slow_mo=settings.slow_mo_ms)
        log.info("Launched Chromium %s (headless=%s)", browser.version, settings.headless)
        yield browser
        browser.close()


@pytest.fixture
def page(browser, app_server):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    pg = context.new_page()
    console_errors: list[str] = []
    pg.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
    pg.console_errors = console_errors
    yield pg
    context.close()


@pytest.fixture
def agent_page(page, settings):
    return ResumeAgentPage(page, settings.app_base_url, settings.default_timeout_ms, settings.agent_timeout_ms).load()


@pytest.fixture
def run(request, agent_page, scenario, settings, model_info):
    """Per-scenario recorder. On teardown: screenshot (pass AND fail), status, transcript."""
    rec = TranscriptRecorder(scenario["test_id"], scenario["title"], request.node.nodeid, model_info,
                             settings.app_base_url, settings.transcripts_dir)
    yield rec
    report = getattr(request.node, "rep_call", None)
    if report is None or report.skipped:
        status = "NOT_EXECUTED"
    else:
        status = "PASS" if report.passed else "FAIL"
    stamp = re.sub(r"[^0-9]", "", utc_now())[:14]
    shot = agent_page.screenshot(settings.screenshots_dir / f"{scenario['test_id']}_{status}_{stamp}.png")
    rec.add_screenshot(shot)
    if agent_page.page.console_errors:
        rec.data["evidence"]["browser_console_errors"] = agent_page.page.console_errors
    error = None
    if report is not None and report.failed:
        error = str(report.longrepr.reprcrash.message if hasattr(report.longrepr, "reprcrash") else report.longrepr)[:1500]
    rec.finish(status, error)

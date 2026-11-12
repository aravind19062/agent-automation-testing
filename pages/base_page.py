"""Reusable, logged, exception-wrapped browser interactions shared by all pages."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, expect

from utils.logger import get_logger


class UIActionError(AssertionError):
    """A UI step failed. Subclasses AssertionError so pytest reports it as a test failure."""


class BasePage:
    def __init__(self, page: Page, base_url: str, default_timeout_ms: int):
        self.page = page
        self.base_url = base_url
        self.timeout = default_timeout_ms
        self.log = get_logger(self.__class__.__name__)
        page.set_default_timeout(default_timeout_ms)

    # -- locators --------------------------------------------------------- #
    def by_test_id(self, test_id: str) -> Locator:
        return self.page.get_by_test_id(test_id)

    # -- actions ---------------------------------------------------------- #
    def _run(self, description: str, action):
        self.log.info("UI step: %s", description)
        try:
            return action()
        except (PlaywrightError, AssertionError) as exc:
            self.log.error("UI step failed: %s -> %s", description, exc)
            raise UIActionError(f"UI step failed: {description}: {exc}") from exc

    def open(self, path: str = "/") -> None:
        self._run(f"open {path}", lambda: self.page.goto(f"{self.base_url}{path}", wait_until="domcontentloaded"))

    def click(self, locator: Locator, name: str) -> None:
        self._run(f"click {name}", lambda: locator.click())

    def fill(self, locator: Locator, value: str, name: str) -> None:
        preview = value if len(value) <= 60 else value[:57] + "..."
        self._run(f"fill {name} with {preview!r}", lambda: locator.fill(value))

    def upload(self, locator: Locator, file_path: Path, name: str) -> None:
        self._run(f"upload {Path(file_path).name} via {name}", lambda: locator.set_input_files(str(file_path)))

    def text_of(self, locator: Locator, name: str) -> str:
        return self._run(f"read text of {name}", lambda: locator.inner_text())

    def value_of(self, locator: Locator, name: str) -> str:
        return self._run(f"read value of {name}", lambda: locator.input_value())

    def wait_visible(self, locator: Locator, name: str, timeout_ms: int | None = None) -> None:
        self._run(f"wait for {name} to be visible",
                  lambda: expect(locator).to_be_visible(timeout=timeout_ms or self.timeout))

    def wait_hidden(self, locator: Locator, name: str, timeout_ms: int | None = None) -> None:
        self._run(f"wait for {name} to be hidden",
                  lambda: expect(locator).to_be_hidden(timeout=timeout_ms or self.timeout))

    def wait_text(self, locator: Locator, text: str, name: str, timeout_ms: int | None = None) -> None:
        self._run(f"wait for {name} to show {text!r}",
                  lambda: expect(locator).to_have_text(text, timeout=timeout_ms or self.timeout))

    def screenshot(self, path: Path) -> Path | None:
        try:
            self.page.screenshot(path=str(path), full_page=True)
            self.log.info("Screenshot saved: %s", path)
            return path
        except PlaywrightError as exc:
            self.log.warning("Screenshot failed (%s): %s", path, exc)
            return None

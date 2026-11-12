"""Page Object for the Resume Updating Agent UI."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Locator, Page, expect

from pages.base_page import BasePage


@dataclass
class AgentReply:
    action: str            # update | clarify | reject | error
    text: str
    changed_sections: list[str] = field(default_factory=list)
    resume_after: str = ""


class ResumeAgentPage(BasePage):
    def __init__(self, page: Page, base_url: str, default_timeout_ms: int, agent_timeout_ms: int):
        super().__init__(page, base_url, default_timeout_ms)
        self.agent_timeout = agent_timeout_ms
        self.resume_input = self.by_test_id("resume-input")
        self.resume_upload = self.by_test_id("resume-upload")
        self.upload_error = self.by_test_id("upload-error")
        self.chat_log = self.by_test_id("chat-log")
        self.chat_input = self.by_test_id("chat-input")
        self.send_button = self.by_test_id("send-button")
        self.reset_button = self.by_test_id("reset-button")
        self.validation_message = self.by_test_id("validation-message")
        self.typing_indicator = self.by_test_id("typing-indicator")
        self.updated_resume = self.by_test_id("updated-resume")
        self.changed_sections = self.by_test_id("changed-sections")

    # -- composite locators ---------------------------------------------- #
    def messages(self, role: str | None = None) -> Locator:
        selector = '[data-testid="chat-message"]' + (f'[data-role="{role}"]' if role else "")
        return self.chat_log.locator(selector)

    # -- page actions ------------------------------------------------------ #
    def load(self) -> "ResumeAgentPage":
        self.open("/")
        self.wait_visible(self.chat_input, "chat input")
        return self

    def enter_resume(self, text: str) -> None:
        self.fill(self.resume_input, text, "resume input")

    def current_resume(self) -> str:
        return self.value_of(self.resume_input, "resume input")

    def upload_resume(self, file_path: Path) -> str:
        """Upload a file and return the resulting upload error text ('' if none)."""
        self.upload(self.resume_upload, file_path, "resume upload")
        # The upload is async: wait until either an error appears or the resume is filled.
        self._run("wait for upload result", lambda: self.page.wait_for_function(
            """() => document.querySelector('[data-testid="upload-error"]').textContent.trim() !== ''
                  || document.querySelector('[data-testid="resume-input"]').value.trim() !== ''""",
            timeout=self.timeout))
        return self.text_of(self.upload_error, "upload error").strip()

    def click_send(self) -> None:
        self.click(self.send_button, "Send button")

    def submit_empty_or_invalid(self, value: str) -> str:
        """Submit a request expected to be blocked client-side; return the validation text."""
        self.fill(self.chat_input, value, "chat input")
        self.click_send()
        self.wait_visible(self.validation_message, "validation message")
        return self.text_of(self.validation_message, "validation message").strip()

    def send_message(self, text: str) -> AgentReply:
        """Send a request and wait for the agent's reply bubble (explicit waits, no sleeps)."""
        before = self.messages("assistant").count()
        self.fill(self.chat_input, text, "chat input")
        self.click_send()
        reply = self.messages("assistant").nth(before)
        self.wait_visible(reply, "agent reply", timeout_ms=self.agent_timeout)
        self.wait_hidden(self.typing_indicator, "typing indicator", timeout_ms=self.agent_timeout)
        self._run("check Send is re-enabled", lambda: expect(self.send_button).to_be_enabled())
        action = reply.get_attribute("data-action") or ""
        body = self.text_of(reply.get_by_test_id("message-text"), "agent reply text").strip()
        changed_raw = self.text_of(self.changed_sections, "changed sections").strip()
        changed = [] if action != "update" or changed_raw == "none yet" else [s.strip() for s in changed_raw.split(",") if s.strip()]
        self.log.info("Agent replied action=%s changed=%s text=%r", action, changed, body[:120])
        return AgentReply(action=action, text=body, changed_sections=changed, resume_after=self.current_resume())

    def assistant_message_count(self) -> int:
        return self.messages("assistant").count()

    def total_message_count(self) -> int:
        return self.messages().count()

    def reset_conversation(self) -> None:
        self.click(self.reset_button, "New conversation button")

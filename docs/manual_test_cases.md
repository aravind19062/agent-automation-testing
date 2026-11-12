# Manual Test Cases — Resume Updating Agent

**Application under test:** Resume Updating Agent (`resume_agent_app/`), a local web app. The user pastes or uploads a plain-text resume and asks a chat agent for changes. The agent is backed by the local Ollama model `gemma2:2b`.
**Test data:** `test_data/resume_data.json` (a fictional candidate, "Jordan Avery") and `test_data/test_scenarios.json`.
**Manual execution:** 13 Nov 2026, macOS (Apple M1, 8 GB), Chromium, agent build with the edit guards.

## 1. Application behaviour observed during exploration (Phase 1)

| Area | Observed behaviour |
|---|---|
| Resume input | `textarea` (`data-testid=resume-input`). It accepts any plain text. CAPITALISED lines (SUMMARY, SKILLS, EXPERIENCE, EDUCATION) are treated as section headings. Lines before the first heading form the HEADER (name and contact details). |
| Upload | `input[type=file]` (`resume-upload`) accepts only `.txt` and `.md`, at most 200 KB, UTF-8. Errors appear in `upload-error`: *"Unsupported file type. Please upload a .txt or .md resume."* and *"The uploaded file is empty."* |
| Chat input | `chat-input` with `maxlength=2000`. **Send** submits; so does Enter (Shift+Enter adds a newline). |
| Client validation | `validation-message`: no resume → *"Please add your resume before requesting changes."*; empty or whitespace request → *"Please enter an update request."* No request is sent. |
| While the agent works | `typing-indicator` is visible and **Send** is disabled. Responses take about 3–20 s. |
| Agent reply | Each reply is a chat bubble (`chat-message`, `data-role=assistant`) with a status badge (`agent-status`). Its `data-action` is `update` ("Updated"), `clarify` ("Needs details"), `reject` ("Declined") or `error`. |
| Successful update | The resume textarea and the "Updated resume" panel (`updated-resume`) show the new resume. `changed-sections` lists the edited sections. The next request builds on the updated resume. |
| Agent design | The LLM returns section edits as `append` or `replace`, applied in code so untouched sections are preserved. Two guards downgrade an unsafe edit to a clarification: one blocks edits that drop existing lines, the other blocks edits that add names or numbers absent from the resume and the user's messages. |

### Defects found during exploratory testing (before the final agent build)

| ID | Finding | Status |
|---|---|---|
| EXP-01 | "Add my new job" (no details) **deleted** the existing Northwind Labs role. Data loss. | Fixed: append mode and the line-preservation guard |
| EXP-02 | "Add my new job" (no details) invented "Acme Corp, 2023". Hallucination. | Fixed: the grounding guard now asks for details |
| EXP-03 | The conflicting-values reply quoted the few-shot example values ("2 and 8") instead of the user's ("3 and 10"). | Fixed: placeholder examples in the prompt |
| EXP-04 | Multi-edit request: skills placed in SUMMARY; the reply falsely claims SKILLS was updated. | Fixed: the agent detects named sections it skipped, asks the model for each one, and keeps the reply honest. Verified by TC_004 |
| EXP-05 | Follow-up job details: an existing Northwind achievement was copied into the new Fabrikam role (misattribution). | **Open.** Reproduced by TC_002 step 6 |

## 2. Test cases

Mandatory template fields: Test ID, Test Case Description, Precondition, Test Steps, Expected Result, Actual Result, Status, Automation Candidate. Added fields: Category, Test Data, Automation Priority, Automated Test.

---

### TC_001 — Successful resume update (add skills)

| Field | Value |
|---|---|
| **Test ID** | TC_001 |
| **Category** | Positive |
| **Test Case Description** | Verify that a valid, fully specified update request is understood and applied to the correct section only. |
| **Precondition** | The app is running at `APP_BASE_URL`, Ollama is serving `gemma2:2b`, and the browser is on the home page with an empty conversation. |
| **Test Data** | `base_resume`; request: *"Add Python and Playwright to my skills."* |
| **Test Steps** | 1. Open the app.<br>2. Paste `base_resume` into **Your resume**.<br>3. Type the request in the chat input and click **Send**.<br>4. Wait for the typing indicator to disappear and the agent reply to appear.<br>5. Inspect the badge, the reply, **Changed sections** and **Updated resume**. |
| **Expected Result** | Badge **Updated**. The reply confirms Python and Playwright were added. Changed sections include SKILLS. SKILLS = existing skills + Python, Playwright. HEADER, SUMMARY, EXPERIENCE and EDUCATION are unchanged. |
| **Actual Result** | Badge **Updated**. Reply: *"Added Python and Playwright to your Skills section."* SKILLS: `Selenium, Java, TestNG, Postman, SQL, Jira, Python, Playwright`. Other sections are identical. |
| **Status** | Pass |
| **Automation Candidate** | Yes |
| **Automation Priority** | High |
| **Automated Test** | `tests/test_resume_agent.py::test_tc001_successful_resume_update` |

---

### TC_002 — Incomplete user information (clarification, no invented details)

| Field | Value |
|---|---|
| **Test ID** | TC_002 |
| **Category** | Negative / Boundary |
| **Test Case Description** | Verify that an update request missing required details produces a clarification question and no change, and that once the details are supplied, only those details are added. |
| **Precondition** | Same as TC_001. |
| **Test Data** | `base_resume`; turn 1: *"Add my new job to my experience section."*; turn 2: *"It is Senior SDET at Fabrikam Inc, started March 2025. I lead the Playwright automation team."* |
| **Test Steps** | 1. Open the app and paste `base_resume`.<br>2. Send turn 1.<br>3. Verify the badge, the reply and that the resume is unchanged.<br>4. Send turn 2 in the same conversation.<br>5. Inspect **Updated resume** → EXPERIENCE.<br>6. Verify the existing roles are intact and no existing achievement is duplicated into the new role. |
| **Expected Result** | Turn 1: badge **Needs details**; the reply asks for the missing company, title and dates; the resume is unchanged; no employer is invented. Turn 2: badge **Updated**; EXPERIENCE contains *Senior SDET, Fabrikam Inc, March 2025*; Northwind Labs and Contoso Retail are kept; nothing the user didn't state is added. |
| **Actual Result** | Turn 1: **Needs details**, reply asks for company, job title and dates; the resume is unchanged. Turn 2: **Updated**; the Fabrikam role is added with "Lead the Playwright automation team", and the old roles are kept. **But** the new role also contains *"Built and maintained Selenium regression suites covering 300+ test cases"*, copied from the Northwind role (EXP-05). |
| **Status** | Fail |
| **Automation Candidate** | Yes |
| **Automation Priority** | High |
| **Automated Test** | `tests/test_resume_agent.py::test_tc002_incomplete_information_clarification` |

---

### TC_003 — Invalid or unsupported input

| Field | Value |
|---|---|
| **Test ID** | TC_003 |
| **Category** | Negative |
| **Test Case Description** | Verify graceful handling of an unsupported file type, an empty file, and a request unrelated to resume editing. |
| **Precondition** | Same as TC_001. |
| **Test Data** | `resume.exe` (unsupported type), `empty_resume.txt` (0 bytes), request *"Can you book me a flight to Paris next week?"* |
| **Test Steps** | 1. Open the app.<br>2. Upload `resume.exe`; read the upload error.<br>3. Upload `empty_resume.txt`; read the upload error.<br>4. Paste `base_resume`.<br>5. Send the off-topic request.<br>6. Inspect the badge, the reply and the resume. |
| **Expected Result** | Step 2: *"Unsupported file type. Please upload a .txt or .md resume."* Step 3: *"The uploaded file is empty."* The resume field is not populated by either file. Step 5: badge **Declined**; the reply says the agent can only help with the resume; the resume is unchanged. |
| **Actual Result** | Both upload errors appear with the exact text, and the resume field stays empty. Off-topic request: **Declined**, *"I can only help you update your resume. What would you like to change?"*; the resume is unchanged. |
| **Status** | Pass |
| **Automation Candidate** | Yes |
| **Automation Priority** | High |
| **Automated Test** | `tests/test_resume_agent.py::test_tc003_invalid_and_unsupported_input` |

---

### TC_004 — Multiple resume modifications in one request

| Field | Value |
|---|---|
| **Test ID** | TC_004 |
| **Category** | Positive |
| **Test Case Description** | Verify that several modifications requested together are all applied, each to the right section, and that unrelated information is not changed. |
| **Precondition** | Same as TC_001. |
| **Test Data** | `base_resume`; request: *"Update my summary to say I have 5 years of experience, and add Docker and Jenkins to my skills."* |
| **Test Steps** | 1. Open the app and paste `base_resume`.<br>2. Send the request.<br>3. Inspect the badge, the reply, **Changed sections**, SUMMARY, SKILLS, and HEADER/EXPERIENCE/EDUCATION. |
| **Expected Result** | Badge **Updated**. Changed sections include SUMMARY and SKILLS. SUMMARY says 5 years (not 4). SKILLS = existing skills + Docker, Jenkins. HEADER, EXPERIENCE and EDUCATION are unchanged. The reply describes only what was actually changed. |
| **Actual Result** | Before the fix: SUMMARY only was changed; SKILLS was unchanged, but the reply claimed it was updated (EXP-04). After the fix: **Updated**; Changed sections: SUMMARY, SKILLS. SUMMARY says 5 years; SKILLS = `Selenium, Java, TestNG, Postman, SQL, Jira, Docker, Jenkins`; reply *"Updated your Summary and Skills sections."*; other sections are unchanged. |
| **Status** | Pass (after the EXP-04 fix) |
| **Automation Candidate** | Yes |
| **Automation Priority** | High |
| **Automated Test** | `tests/test_resume_agent.py::test_tc004_multiple_modifications` |

---

### TC_005 — Boundary: empty input and conflicting instructions

| Field | Value |
|---|---|
| **Test ID** | TC_005 |
| **Category** | Boundary / Negative |
| **Test Case Description** | Verify that an empty or whitespace-only request is blocked by validation, and that self-contradictory instructions lead to a clarification instead of an arbitrary change. |
| **Precondition** | Same as TC_001. |
| **Test Data** | `base_resume`; inputs `""` and `"     "`; request *"Change my total experience to 3 years and also make it 10 years."* |
| **Test Steps** | 1. Open the app and paste `base_resume`.<br>2. Leave the chat input empty and click **Send**.<br>3. Enter only spaces and click **Send**.<br>4. Verify no chat message was sent.<br>5. Send the conflicting request.<br>6. Inspect the badge, the reply and the resume. |
| **Expected Result** | Steps 2–3: *"Please enter an update request."*; the chat log stays empty. Step 5: badge **Needs details**; the reply mentions both 3 and 10 and asks which to use; the resume is unchanged. |
| **Actual Result** | Validation message is shown for both inputs, and no message is sent. Conflict: **Needs details**, *"You asked for both 3 and 10 years. Which one should I use?"*; the resume is unchanged. |
| **Status** | Pass |
| **Automation Candidate** | Yes |
| **Automation Priority** | Medium |
| **Automated Test** | `tests/test_resume_agent.py::test_tc005_empty_and_conflicting_input` |

## 3. Traceability matrix

| Manual Test ID | Automated UI test | DeepEval evaluation | Metrics |
|---|---|---|---|
| TC_001 | `test_tc001_successful_resume_update` | `test_deepeval.py::test_agent_response_quality[TC_001]` | Answer Relevancy, Faithfulness, Hallucination |
| TC_002 | `test_tc002_incomplete_information_clarification` | `…[TC_002]` (both agent turns) | Answer Relevancy, Faithfulness, Hallucination |
| TC_003 | `test_tc003_invalid_and_unsupported_input` | `…[TC_003]` | Faithfulness, Hallucination (Answer Relevancy is not applicable to a correct refusal) |
| TC_004 | `test_tc004_multiple_modifications` | `…[TC_004]` | Answer Relevancy, Faithfulness, Hallucination |
| TC_005 | `test_tc005_empty_and_conflicting_input` | `…[TC_005]` | Answer Relevancy, Faithfulness, Hallucination |

Supplementary (non-regression) tests in `tests/test_dynamic_inputs.py` use Ollama-generated inputs and trace to TC_001 (paraphrased requests) and TC_002 (an Ollama-written answer to the agent's clarification question).

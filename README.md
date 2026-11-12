# Agent Automation Testing — Resume Updating Agent

An end-to-end testing framework for an LLM-powered **Resume Updating Agent**. It combines **Playwright** UI automation (Page Object Model), **pytest**, a local **Ollama** model, JSON conversation transcripts, and **DeepEval** LLM-as-a-Judge evaluation, which also runs on the local model. The whole thing runs offline on one machine, with no OpenAI, Claude, or other hosted LLM API.

The repository has two parts:

| Part | Path | Purpose |
|---|---|---|
| Application under test | `resume_agent_app/` | A small FastAPI + HTML chat app. The user pastes or uploads a resume and asks for changes; a `gemma2:2b` agent decides to **update**, **clarify**, or **reject**. |
| Test framework | everything else | Manual test cases, automated E2E scenarios, Ollama-driven input generation, transcripts, DeepEval scoring, and reports. |

## Architecture

```
            test_data/*.json  (deterministic scenarios + resume)
                    │
pytest ──► tests/test_resume_agent.py ──► pages/ (POM) ──► Playwright/Chromium ──► Resume Agent UI ──► FastAPI ──► Ollama (agent)
   │                │
   │                └─► integrations/transcript_recorder.py ──► transcripts/TC_00x.json  (real conversation, checks, steps, screenshots)
   │
   ├─► tests/test_deepeval.py ──► integrations/deepeval_evaluator.py ──► DeepEval metrics ──► OllamaModel judge (local)
   │                                                                   └─► reports/evaluation_results.json
   │
   ├─► tests/test_dynamic_inputs.py ──► integrations/ollama_client.py (paraphrases / clarification answers)   [supplementary]
   │
   └─► pytest_sessionfinish ──► reporting/report_generator.py ──► reports/execution_report.{json,html} + execution_summary.md
```

```
agent-automation-testing/
├── config/settings.py              # all settings from env/.env (URLs, models, timeouts, thresholds)
├── pages/
│   ├── base_page.py                # logged, exception-wrapped interactions + explicit waits
│   └── resume_agent_page.py        # Page Object for the agent UI
├── integrations/
│   ├── ollama_client.py            # retrying Ollama client, model metadata, input generators
│   ├── transcript_recorder.py      # per-scenario JSON transcript, steps, checks, evidence
│   └── deepeval_evaluator.py       # DeepEval metrics with a local OllamaModel judge
├── reporting/report_generator.py   # JSON + HTML + Markdown reports (pass rule applied here)
├── tests/
│   ├── conftest.py                 # fixtures: settings, app lifecycle, browser, page, recorder, screenshots
│   ├── test_resume_agent.py        # TC_001 … TC_005 end-to-end UI tests
│   ├── test_deepeval.py            # LLM-as-a-Judge evaluation of the captured agent responses
│   ├── test_deepeval_setup.py      # judge setup smoke test
│   └── test_dynamic_inputs.py      # supplementary Ollama-generated inputs (TC_001 / TC_002)
├── test_data/
│   ├── test_scenarios.json         # inputs + expected behaviour per scenario
│   └── resume_data.json            # fictional resume + invalid-file fixtures
├── docs/manual_test_cases.md       # 5 manual test cases, exploration findings, traceability
├── utils/                          # logger, JSON I/O, functional-check engine, app server control
├── resume_agent_app/               # application under test
├── transcripts/  reports/  screenshots/  logs/
├── pytest.ini  requirements.txt  .env.example  .gitignore
```

## Technology stack

| Tool | Version | Role |
|---|---|---|
| Python | 3.12 (3.11+ supported) | Language |
| pytest | 9.2.0 | Test runner, fixtures, markers |
| Playwright (Python, sync) | 1.65.1 | Browser automation (Chromium) |
| Ollama | 0.40.x server, `ollama` 0.6.3 client | Local LLM runtime |
| Model | `gemma2:2b` (2.6B params, Q4_0) | Agent model, test-input generator, and DeepEval judge |
| DeepEval | 4.3.4 | Answer Relevancy, Faithfulness, Hallucination |
| FastAPI / uvicorn | 0.162 / 0.54 | Application under test |

## Prerequisites

* macOS, Linux or Windows with Python 3.11+
* About 4 GB of free RAM for `gemma2:2b` (this was developed on an Apple M1 with 8 GB)
* About 2 GB of disk for the model and about 200 MB for the Chromium headless shell

## Setup

### 1. Python environment
```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Playwright browser
```bash
playwright install chromium
```

### 3. Ollama and the model
Install Ollama from https://ollama.com/download (macOS: `brew install ollama`), then:
```bash
ollama serve                        # not needed if the desktop app or `brew services start ollama` is running
ollama pull gemma2:2b
ollama list                         # gemma2:2b should be listed
```

### 4. Environment variables
```bash
cp .env.example .env
```
`.env` is git-ignored. Every URL, model name, timeout and threshold is read from it (`config/settings.py`). Nothing environment-specific is hardcoded. `APP_BASE_URL` is required.

### 5. DeepEval judge configuration
The framework sets up the judge **in code**:

```python
OllamaModel(model=JUDGE_MODEL, base_url=OLLAMA_BASE_URL, temperature=0)
```

It passes that model to every metric (`integrations/deepeval_evaluator.py`). No OpenAI key is needed or read, and DeepEval telemetry is turned off (`DEEPEVAL_TELEMETRY_OPT_OUT=YES`). To make Ollama DeepEval's default judge for ad-hoc use as well, run:
```bash
deepeval set-ollama --model=gemma2:2b --base-url="http://localhost:11434"
```
Check the judge on its own:
```bash
python tests/test_deepeval_setup.py
```

## Running the tests

The app starts automatically when `APP_BASE_URL` points at localhost and `AUTO_START_APP=true`. To run it by hand instead:
```bash
uvicorn resume_agent_app.main:app --host 127.0.0.1 --port 8765
```

| Goal | Command |
|---|---|
| Full regression: 5 UI scenarios, then DeepEval, then reports | `pytest` |
| UI scenarios only | `pytest tests/test_resume_agent.py` |
| One scenario | `pytest tests/test_resume_agent.py -k tc004` |
| By category | `pytest tests/test_resume_agent.py -m negative` |
| Evaluate existing transcripts only | `pytest tests/test_deepeval.py` |
| Same, via the DeepEval CLI | `deepeval test run tests/test_deepeval.py` |
| Supplementary Ollama-generated inputs | `pytest tests/test_dynamic_inputs.py -m dynamic` |
| Judge smoke test | `pytest tests/test_deepeval_setup.py -m smoke` |
| Watch the browser | `HEADLESS=false SLOW_MO_MS=300 pytest tests/test_resume_agent.py` |
| Rebuild reports from the artefacts | `python -m reporting.report_generator` |

### Outputs
| Artefact | Location |
|---|---|
| Conversation transcript per scenario | `transcripts/TC_00x.json` |
| DeepEval scores and reasons | `reports/evaluation_results.json` |
| Combined machine-readable report | `reports/execution_report.json` |
| Human-readable report | `reports/execution_report.html`, `reports/execution_summary.md` |
| Screenshots (pass **and** fail) | `screenshots/TC_00x_<PASS/FAIL>_<timestamp>.png` |
| Framework log | `logs/framework.log` (app server log: `logs/app_server.log`) |

## How scenarios work

* **Traceability.** Each UI test carries `@pytest.mark.test_id("TC_00x")`. That ID links the manual case (`docs/manual_test_cases.md`), the test data entry, the transcript file, the DeepEval test, and the report row.
* **Data-driven checks.** Each turn in `test_scenarios.json` has an `expect` block: action, sections that must change or contain text, reply wording, "resume unchanged", and duplicate-content limits. `utils/checks.py` turns each rule into a named, recorded check. Checks are soft-collected and asserted together, so one failure doesn't hide the others.
* **Steps.** Workflow actions run inside `run.step(...)`. The first failing step is recorded as `failed_step` and appears in the report under "Failed UI automation steps".
* **Synchronisation.** Only explicit waits are used: wait for the reply bubble at index *n*, the typing indicator hidden, Send re-enabled, and the upload result. There are no `sleep` calls. Agent replies get a separate, longer timeout (`AGENT_TIMEOUT_MS`).
* **Transcripts** hold the real UI conversation: test ID, model name/version/quantization/digest, every user message and agent reply, the agent's action, changed sections, the resume before and after, UI validation events, functional checks, steps, screenshots, status, and timestamps.

## Ollama integration

`integrations/ollama_client.py`:
* retries connection errors, timeouts, 5xx responses, empty output and invalid JSON, with backoff; reports a missing model with a clear `ollama pull` hint
* records the model name, tag/version, parameter size, quantization, digest and Ollama server version (written into every transcript)
* `generate_request_variations()` paraphrases a request while keeping the key terms
* `generate_clarification_answer()` answers the agent's **actual** clarification question using only the given facts, and checks that none were dropped

Core regression tests use only deterministic data. Generated inputs are confined to `tests/test_dynamic_inputs.py` (`-m dynamic`), so LLM randomness never changes the regression verdict.

## Evaluation methodology (DeepEval, LLM-as-a-Judge)

For **every agent turn** marked `evaluate: true`:

| LLMTestCase field | Value |
|---|---|
| `input` | The user's request for that turn (with earlier user turns, for multi-turn cases) |
| `actual_output` | The agent's **real** reply captured from the UI, plus the text of each section it actually changed |
| `expected_output` | The documented expected behaviour. It is recorded for reviewers and **never** used as the output |
| `context` / `retrieval_context` | The original resume plus everything the user said, i.e. all the facts the agent was allowed to use |

| Metric | Question | Threshold | Why this threshold |
|---|---|---|---|
| Answer Relevancy | Does the reply address the request? | ≥ 0.70 | Most statements should be on-topic. A little slack allows short confirmations and questions. |
| Faithfulness | Is every claim supported by the resume or the user's words? | ≥ 0.70 | Resume content must be grounded. Below 0.7 means more than about 1 in 3 claims is unsupported, which is unacceptable for a CV. |
| Hallucination | Does the output contradict or add to the context? | ≤ 0.30 | The mirror of the faithfulness tolerance. Anything higher means fabricated career details. |

* Scores, thresholds, pass/fail and the judge's **reason** are saved for every metric.
* A judge error (for example malformed JSON from the judge) is recorded as a **failed** metric with the error text. It is never skipped or treated as a pass.
* **TC_003** applies only Faithfulness and Hallucination. A correct refusal deliberately does *not* answer an off-topic question, so Answer Relevancy would penalise the behaviour we want. This is documented in `test_scenarios.json`.
* **Pass rule:** a scenario is PASS only if its functional checks **and** all its metric thresholds pass. A missing transcript or evaluation is reported as NOT_EXECUTED or INCOMPLETE, never as PASS.

## Model selection and hardware

`gemma2:2b` (2.6B parameters, Q4_0, about 1.6 GB) was chosen because it fits comfortably on 8 GB laptops alongside a browser, and Ollama's schema-constrained JSON output makes a 2B model usable for structured agent decisions and DeepEval verdicts. On an Apple M1 with 8 GB, an agent turn takes about 3–20 s and a metric about 5–30 s. A full run takes roughly 10–20 minutes, mostly judge time. Metrics run sequentially (`async_mode=False`) to avoid memory pressure.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Ollama is not reachable at http://localhost:11434` | Start Ollama (`ollama serve` or `brew services start ollama`), then `curl localhost:11434/api/version` |
| `Model 'gemma2:2b' is not installed` | `ollama pull gemma2:2b`, then `ollama list` |
| `OllamaModel requires the ollama package` | `pip install ollama` (it's in `requirements.txt`) |
| DeepEval tries to use OpenAI | Make sure metrics get `model=OllamaModel(...)` (they do here), or run `deepeval set-ollama …`. Do not set `OPENAI_API_KEY`. |
| `Application is not reachable` | Check `APP_BASE_URL`, and that the port is free (the default 8765 avoids the commonly used 8000). See `logs/app_server.log`. |
| `Missing required setting APP_BASE_URL` | `cp .env.example .env` |
| Playwright "Executable doesn't exist" | `playwright install chromium` |
| Agent reply timeouts | Raise `AGENT_TIMEOUT_MS` / `OLLAMA_TIMEOUT_S`. A cold model load can take 30 s or more. |
| Judge metric errors (`ValidationError`, JSON) | A small model sometimes emits invalid JSON. DeepEval retries, and remaining failures are reported, not hidden. A larger judge (for example `JUDGE_MODEL=llama3.1:8b`) is more reliable if your hardware allows. |
| Slow evaluation or high memory | Close other apps. Document your machine specs. Don't silently switch to a different judge model. |

## Latest results (clean run, 13 Nov 2026, Apple M1 8 GB, 5 min 38 s)

| Test | Functional | DeepEval | Final |
|---|---|---|---|
| TC_001 Add skills | PASS | FAIL (Hallucination 0.50) | FAIL |
| TC_002 Incomplete info | FAIL: an achievement was duplicated into the new role (open defect EXP-05) | FAIL (Hallucination 0.67) | FAIL |
| TC_003 Invalid input | PASS | FAIL (Faithfulness 0.67) | FAIL |
| TC_004 Multiple edits | PASS (EXP-04 fixed) | FAIL (Hallucination 0.50) | FAIL |
| TC_005 Empty / conflicting | PASS | PASS | PASS |

Functional checks: 4/5 scenarios. Metrics: 10/14 passed. Overall: **FAIL** under the strict pass rule. These are real results; thresholds were not loosened to make them pass.
Analysis: the TC_002 hallucination score matches a real defect. The TC_001 and TC_004 hallucination failures look like **judge false positives**: the judge's own reasons misread the request (for example, it calls adding Docker to Skills a contradiction). This is the known weakness of a 2B judge. Re-run with a stronger local judge (`JUDGE_MODEL=llama3.1:8b`) to separate agent defects from judge noise.
To keep a run within an 8 GB machine's limits, only the final agent turn of each scenario is judged. TC_002's clarification turn is covered by the functional checks.

## Known limitations

* **Self-judging bias:** by default the agent and the judge are the same model (`gemma2:2b`), so the judge may share the agent's blind spots. Set `JUDGE_MODEL` to a different local model to reduce this.
* **Small-judge variance:** 2B-parameter judges are noisy. Scores near a threshold can flip between runs even at `temperature=0`, because the agent's output itself can vary slightly.
* **Non-determinism:** the agent uses `temperature=0` and a fixed seed, but output can still change with Ollama or model updates. Expectations check behaviour (action, sections, key facts), not exact wording.
* **Grounding guard scope:** the agent's guard catches invented names and numbers. It cannot catch paraphrased or misattributed claims (see EXP-05); that is what the DeepEval metrics and the duplicate-content checks are for.
* The application has no authentication, so login flows are out of scope.
* Chromium only. Firefox and WebKit would need a `browser` fixture parameter.

## Security

* No credentials, tokens or API keys are used or committed. `.env` is git-ignored, and `.env.example` holds only local defaults.
* All test data is fictional (the `example.com` domain, fictional companies).
* No external LLM API is called. DeepEval telemetry is turned off.

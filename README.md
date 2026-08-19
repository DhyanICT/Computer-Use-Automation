# Computer-Use Automation System

Author: Dhyan Patel

This project is my implementation of a small computer-use automation system. The main idea is:

1. Use an agent-style discovery run to figure out how to complete a task in a UI.
2. Save that successful run as a structured capability artifact.
3. Replay the artifact later without asking an LLM to decide every step again.

I built a local legacy-style banking console as the target app so the project can be run safely without real bank data, real credentials, or a public website. The app uses table-based layouts and visible labels instead of clean test IDs, because that is closer to the type of old back-office software described in the assignment.

## What this project does

The demo flow looks up a member and reads their savings balance.

The system can:

- start a local target app,
- launch Chrome,
- observe the visible UI,
- click and type into the page,
- record the discovered flow as a typed JSON artifact,
- replay that artifact deterministically,
- return extracted outputs,
- detect a known business outcome like `member_not_found`,
- enforce a local allowlist and redaction policy,
- pause for human handoff on a risky action.

## Project structure

- `src/target_app/`: the local legacy-style banking app.
- `src/cua_sys/cdp.py`: small Chrome DevTools Protocol browser controller.
- `src/cua_sys/llm.py`: planner interface with two providers:
  - `scripted`: local/offline planner for verification without paid API credits.
  - `openai`: OpenAI-compatible planner for a real LLM-backed discovery run.
- `src/cua_sys/engine.py`: discovery and replay logic.
- `src/cua_sys/handoff.py`: human handoff request/resume flow.
- `config/policy.json`: allowed origins, allowed actions, risky-action behavior, and redaction rules.
- `artifacts/member_balance_lookup.json`: generated reusable capability artifact.
- `evidence/`: logs, screenshots, DOM snapshots, results, and handoff evidence.
- `REPORT.md`: design write-up using the assignment's required headings.

## Requirements

- Python 3.11 or newer.
- Chrome or Edge installed.
- No Python package install is needed for the offline demo.
- Optional for real LLM discovery: `OPENAI_API_KEY`.

If Chrome is not found automatically, set:

```powershell
$env:CHROME_PATH = "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

## Run the local demo

From the project root:

```powershell
python run.py demo --llm scripted
```

This runs the full local version:

1. Discovery for member `12345`.
2. Artifact generation at `artifacts/member_balance_lookup.json`.
3. Deterministic replay for member `12345`.
4. Replay for member `99999`, which returns `member_not_found`.
5. Human handoff demo for a risky `Freeze Card` action.

The scripted planner is included so the project can be verified without paying for API credits. It uses the same action contract as the OpenAI planner, so the replay/artifact/safety path is the same.

## Run with an OpenAI-compatible LLM

For the strongest assignment submission, the discovery evidence should be regenerated with a real LLM call:

```powershell
$env:OPENAI_API_KEY = "your_key_here"
python run.py demo --llm openai
```

Optional settings:

```powershell
$env:OPENAI_MODEL = "gpt-4.1-mini"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1/chat/completions"
```

I did not use paid OpenAI API credits for my local evidence run because I did not want to add billing. The OpenAI-compatible path is still implemented in `src/cua_sys/llm.py`, so the same project can be rerun with `--llm openai` if API credits are available.

## Manual two-terminal demo

Terminal 1:

```powershell
python run.py serve-target --port 8088
```

Terminal 2:

```powershell
python run.py discover --llm scripted --target http://127.0.0.1:8088/ --member-id 12345 --goal "Look up member 12345 and read their current savings balance."
python run.py replay --target http://127.0.0.1:8088/ --member-id 12345
python run.py replay --target http://127.0.0.1:8088/ --member-id 99999
python run.py demo-handoff --target http://127.0.0.1:8088/ --member-id 12345
```

The replay commands do not use an LLM. They load the saved artifact, fill in `{{member_id}}`, execute the recorded steps, verify the checkpoint, and return a structured result.

## Evidence

After running the demo, `/evidence/` contains:

- `capability.member_balance_lookup.json`: copy of the generated artifact.
- `discovery.*.jsonl`: discovery observations, planner decisions, and actions.
- `discovery.*.success.png`: final discovery screenshot.
- `replay.*.jsonl`: deterministic replay logs.
- `result.replay.*.json`: structured replay result.
- `replay.*.success.png`: successful replay screenshot.
- `replay.*.member_not_found.*`: evidence for the not-found business outcome.
- `handoff.*.jsonl`: handoff log.
- `result.handoff.*.json`: structured handoff result.
- `interventions/*`: human intervention request, screenshot, DOM snapshot, resume file, and recorded human actions.

Run IDs are generated each time, so the filenames will not exactly match between runs.

## Tests

```powershell
python -m unittest discover -s tests
```

The tests cover policy allowlisting, redaction behavior, parameter materialization, and loading the generated artifact schema.

## Safety notes

I do not store the literal member id in the artifact. The artifact stores `{{member_id}}` as a parameter. Logs and results go through the redactor, which masks member-number-like values and currency values. Risky actions are escalated instead of executed automatically.

## Notice on GenAI usage

I used GenAI tools while building this project, mainly for boilerplate, code structure, documentation structure, and improvement suggestions. I did not use GenAI as a replacement for understanding the assignment or the design decisions. I reviewed the code, ran the tests, generated the evidence, and made the final implementation choices myself.

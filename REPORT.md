# REPORT

## 1. Architecture

I implemented this as a small end-to-end vertical slice instead of trying to build a large platform. The project has four main parts: a target app, a surface controller, a planner, and a deterministic replay engine.

The target app is a local legacy-style banking console. I built it locally because I did not want to automate a real financial system, use real credentials, or depend on a public website. The UI is intentionally simple and old-looking: table layouts, visible row labels, regular buttons/links, and no test IDs. That lets the project exercise the kind of surface the assignment describes without using real sensitive data.

For browser control, I used Chrome DevTools Protocol directly from Python. The controller observes visible text and controls, resolves targets, clicks by coordinates, types into fields, extracts text, and captures screenshots/DOM snapshots. I chose this because the important design point is the surface abstraction, not a specific browser automation framework. A Playwright implementation would also be reasonable, but I wanted the project to run without installing extra packages.

The discovery path accepts a goal, a target URL, and typed parameters. A planner decides one step at a time from the current observation. There are two planner implementations: `scripted`, which makes the project verifiable without paid API credits, and `openai`, which uses an OpenAI-compatible chat completions endpoint. Both planners return the same action format, so the rest of the system does not care where the decision came from.

The replay path loads the saved artifact and does not call an LLM. It follows the recorded steps, checks policy, fills in parameters, detects known outcomes, extracts outputs, and verifies the final checkpoint.

## 2. Artifact schema

The artifact schema is `cua.capability.v1`. I designed it as a reusable capability contract, not just a transcript of what happened during discovery.

The artifact includes:

- capability id, name, description, and target app,
- target URL and policy reference,
- typed input parameters,
- typed outputs,
- ordered steps,
- target identification strategy for each step,
- success condition,
- known business outcomes,
- error contract,
- tenant/generalization notes.

Each step has an action type, description, target, optional parameter template, optional output key, risk class, and timeout. For example, the member id is stored as `{{member_id}}`, not as the literal value used during discovery. This matters because the artifact should be safe and reusable.

For targeting, I kept semantic strategies like `visible_text`, `row_label`, and `near_text` as the main locators. I also store a resolved CSS path as a replay hint, but I do not treat it as the only source of truth. In legacy software, labels near fields are often more stable than generated DOM paths.

## 3. Determinism & error handling

Replay is deterministic because the planner is not used during replay. The replay engine executes the artifact steps in order. It waits for page readiness, resolves targets, performs actions, extracts outputs, and checks the final success condition.

I separated outcomes into three categories.

First, success means the flow reached the expected checkpoint and returned the declared outputs. In the demo, that means the member profile was reached and the member name and savings balance were extracted.

Second, a business outcome is something the caller needs to know, but it is not a crash. The example is `member_not_found`. If the app says no member exists for the supplied member id, replay returns `status: business_outcome` and `member_found: false` instead of failing blindly.

Third, failures are actual execution problems: missing targets, validation errors, permission denial, checkpoint failure, or unexpected runtime errors. When these happen, the result includes the step id, message, screenshot, and DOM snapshot so the failure can be debugged.

I also included a recoverable-condition hook. The app can show a known transient notice, and replay can dismiss it and continue. That is the type of runtime issue I would expect in a real back-office system.

## 4. Heterogeneity & multi-tenant

Even though I implemented only one browser target, I tried to keep the core artifact independent from the specific browser implementation. The seam is the surface adapter. Today the adapter is CDP/browser-based, but another adapter could expose the same basic operations for a legacy web app or desktop app: observe, click, type, extract near a label, capture evidence, and report state.

For a legacy web app, I would extend the adapter with frame handling, accessibility-tree lookup, OCR/image matching, and more locator fallbacks. For a desktop app, the adapter could use OS accessibility APIs or coordinate-based automation while keeping the same artifact concepts.

For multi-tenant reuse, I would treat this artifact as a vendor-base capability. Tenants running the same vendor product should reuse the same base artifact and only override tenant-specific parts like entry URL, branding text, label synonyms, interstitials, or locator fallbacks. I would track artifact versions and tenant overrides separately so a change for one institution does not silently affect everyone else.

To manage drift, I would run replay smoke tests per tenant/version, record which locator strategy was used, and capture screenshots/DOM snapshots on failure. If a tenant starts failing, the system should mark that override or artifact version as needing review instead of silently re-recording everything.

## 5. Escalation & handoff

I implemented handoff as a control-transfer mechanism. When automation reaches a risky or blocked state, it writes an intervention request with the reason, current step, screenshot, DOM snapshot, debug URL, and session context. The request records that control belongs to the human.

The demo handoff uses a risky `Freeze Card` action. Automation pauses before performing it. The human/operator path uses the same browser session, inspects the risky screen, clicks into the freeze-card page, and then cancels. The resume file records the human decision and the actions taken, then control returns to automation.

This is not a full operator console, and I intentionally did not build one. The important part I wanted to show is the seam: automation can pause, give a human the same live session, preserve evidence, and resume with a clear record of what happened.

## 6. Safety

The policy file defines what the automation is allowed to do. In this project, the allowed origins are local demo URLs only, and the allowed action types are limited. Risky actions are escalated instead of executed automatically.

I also avoided storing sensitive literals in artifacts. The member id is represented as a parameter, and logs/results go through a redactor that masks member-number-like values and currency values. In a real bank environment, screenshots and DOM snapshots would also need stronger controls because visual evidence can contain regulated data.

The main safety limits are authentication, secrets management, audit signing, operator roles, and retention policy. I did not implement those because they are beyond the small vertical slice, but the design leaves clear places where they would belong.

## 7. Cuts

The biggest cut is that I could not include paid OpenAI evidence in this local run because I did not want to add billing credits. The OpenAI-compatible planner is implemented, and it uses the same action contract as the scripted planner. To generate the strongest final evidence, run:

```powershell
python run.py demo --llm openai
```

with `OPENAI_API_KEY` set.

I also kept the target app local and browser-based, did not implement a real-time operator console, and did not build a full tenant/version management service. With more time, I would add a small capability catalog API, approval states from draft to approved, multi-run stability scoring, a second tenant variant with overrides, and an accessibility/OCR adapter for a more hostile UI surface.

## Notice on GenAI usage

I used GenAI tools while building this project, mainly for boilerplate, structure, documentation organization, and improvement suggestions. I did not use GenAI as a substitute for understanding the assignment or the system. I reviewed the assignment, made the design decisions, ran the demo and tests, and verified the evidence myself.

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from .cdp import BrowserSession
from .evidence import EvidenceLogger
from .handoff import HandoffCoordinator
from .llm import Planner, PlannerDecision
from .policy import Policy, PolicyViolation
from .types import CapabilityArtifact, FlowStep, TargetSpec, utc_now_iso


class ExecutionFailure(RuntimeError):
    def __init__(self, code: str, message: str, step_id: str | None = None):
        super().__init__(message)
        self.code = code
        self.step_id = step_id
        self.message = message


def materialize(template: str | None, parameters: dict[str, Any]) -> str:
    if template is None:
        return ""
    value = template
    for key, item in parameters.items():
        value = value.replace("{{" + key + "}}", str(item))
    return value


def target_from_raw(raw: dict[str, Any] | None) -> TargetSpec | None:
    return TargetSpec(**(raw or {})) if raw else None


class DiscoveryEngine:
    def __init__(self, browser: BrowserSession, policy: Policy, logger: EvidenceLogger):
        self.browser = browser
        self.policy = policy
        self.logger = logger

    def run(
        self,
        goal: str,
        target_url: str,
        parameters: dict[str, Any],
        planner: Planner,
        artifact_path: Path,
        max_steps: int = 12,
    ) -> CapabilityArtifact:
        self.policy.check_url(target_url)
        self.logger.event("run_started", {"goal": goal, "target_url": target_url, "planner": planner.provider_name})
        self.browser.navigate(target_url)
        history: list[dict[str, Any]] = []
        outputs: dict[str, Any] = {}
        steps: list[FlowStep] = []
        for index in range(max_steps):
            observation = self.browser.observe()
            self.logger.event("observation", {"step_index": index, "observation": observation})
            decision = planner.next_action(goal, observation, parameters, history, outputs)
            self.logger.event("planner_decision", decision.to_dict())
            if decision.action == "finish":
                break
            step = self._step_from_decision(decision)
            result = execute_step(self.browser, self.policy, step, parameters)
            if step.action == "extract" and step.output_key:
                outputs[step.output_key] = result["value"]
            if step.target and result.get("resolved") and result["resolved"].get("css"):
                step.target.css = result["resolved"]["css"]
            steps.append(step)
            history.append({"step_id": step.id, "action": step.action, "result": result})
        else:
            raise ExecutionFailure("discovery_max_steps", f"Discovery exceeded max_steps={max_steps}")

        if "savings_balance" not in outputs:
            raise ExecutionFailure("goal_not_met", "Discovery finished without extracting the savings balance")

        artifact = build_member_balance_artifact(
            target_url=target_url,
            run_id=self.logger.run_id,
            planner_name=planner.provider_name,
            steps=steps,
        )
        artifact.write(artifact_path)
        evidence_copy = self.logger.evidence_dir / "capability.member_balance_lookup.json"
        artifact.write(evidence_copy)
        self.browser.screenshot(self.logger.artifact_path("success.png"))
        self.logger.event("artifact_saved", {"artifact_path": str(artifact_path), "outputs": outputs})
        return artifact

    def _step_from_decision(self, decision: PlannerDecision) -> FlowStep:
        target = target_from_raw(decision.target)
        action = decision.action
        if action not in {"click", "fill", "extract", "checkpoint", "wait"}:
            raise ExecutionFailure("unsupported_planner_action", f"Planner returned unsupported action: {action}")
        return FlowStep(
            id=decision.step_id,
            action=action,  # type: ignore[arg-type]
            description=decision.reason,
            target=target,
            value_template=decision.value_template,
            output_key=decision.output_key,
            risk="risky" if decision.risk == "risky" else "safe",
        )


class ReplayEngine:
    def __init__(self, browser: BrowserSession, policy: Policy, logger: EvidenceLogger):
        self.browser = browser
        self.policy = policy
        self.logger = logger

    def run(self, artifact: CapabilityArtifact, parameters: dict[str, Any]) -> dict[str, Any]:
        self.policy.check_url(artifact.target_url)
        self._validate_parameters(artifact, parameters)
        self.logger.event("run_started", {"capability_id": artifact.capability_id, "target_url": artifact.target_url})
        self.browser.navigate(artifact.target_url)
        outputs: dict[str, Any] = {"member_found": True}
        for index, step in enumerate(artifact.steps):
            self._recover_known_interstitial(step.id)
            outcome = self._detect_business_outcome(artifact)
            if outcome:
                return self._finish_business_outcome(outcome, step.id, outputs)
            if self.policy.should_escalate_risky(step.risk):
                coordinator = HandoffCoordinator(self.logger.evidence_dir, self.logger)
                request = coordinator.request(
                    self.browser,
                    reason="Risky action requires human approval",
                    context={"step": step.id, "description": step.description},
                    auto_operator=True,
                )
                return self._finish_escalated(request, step.id)
            try:
                self.logger.event("step_started", {"index": index, "step": step})
                result = execute_step(self.browser, self.policy, step, parameters)
                if step.action == "extract" and step.output_key:
                    outputs[step.output_key] = result["value"]
                self.logger.event("step_completed", {"index": index, "step_id": step.id, "result": result})
            except Exception as exc:  # noqa: BLE001 - convert all execution errors into structured result
                return self._finish_failure("step_failed", str(exc), step.id)
        self._recover_known_interstitial("final")
        outcome = self._detect_business_outcome(artifact)
        if outcome:
            return self._finish_business_outcome(outcome, "final", outputs)
        if not self._success_condition_met(artifact):
            return self._finish_failure("checkpoint_failed", "Final success condition was not observed", "final")
        result = {"status": "success", "outputs": outputs, "run_id": self.logger.run_id}
        self._write_result(result)
        self.browser.screenshot(self.logger.artifact_path("success.png"))
        self.logger.event("run_completed", result)
        return result

    def _validate_parameters(self, artifact: CapabilityArtifact, parameters: dict[str, Any]) -> None:
        missing = [key for key, spec in artifact.parameters.items() if spec.get("required", True) and key not in parameters]
        if missing:
            raise ExecutionFailure("missing_parameter", f"Missing required parameters: {missing}")

    def _detect_business_outcome(self, artifact: CapabilityArtifact) -> dict[str, Any] | None:
        page_text = self.browser.page_text()
        for outcome in artifact.business_outcomes:
            text = outcome.get("when", {}).get("text_present")
            if text and text.lower() in page_text.lower():
                return outcome
        return None

    def _recover_known_interstitial(self, step_id: str) -> None:
        if self.browser.is_text_present("Transient host notice") and self.browser.is_text_present("Continue"):
            self.logger.event("recoverable_condition", {"step_id": step_id, "condition": "transient_host_notice"})
            self.browser.click(TargetSpec(strategy="visible_text", text="Continue", role="button"))
            self.logger.event("recoverable_condition_resolved", {"step_id": step_id, "condition": "transient_host_notice"})

    def _success_condition_met(self, artifact: CapabilityArtifact) -> bool:
        expected_text = artifact.success_condition.get("text_present")
        return bool(expected_text and self.browser.is_text_present(expected_text))

    def _finish_business_outcome(self, outcome: dict[str, Any], step_id: str, outputs: dict[str, Any]) -> dict[str, Any]:
        screenshot = self.browser.screenshot(self.logger.artifact_path(f"{outcome['code']}.png"))
        dom = self.browser.dom_snapshot(self.logger.artifact_path(f"{outcome['code']}.html"))
        result = {
            "status": "business_outcome",
            "code": outcome["code"],
            "step_id": step_id,
            "message": outcome["message"],
            "outputs": {**outputs, **outcome.get("outputs", {})},
            "evidence": {"screenshot": str(screenshot), "dom_snapshot": str(dom)},
            "run_id": self.logger.run_id,
        }
        self._write_result(result)
        self.logger.event("run_completed", result)
        return result

    def _finish_failure(self, code: str, message: str, step_id: str) -> dict[str, Any]:
        screenshot = self.browser.screenshot(self.logger.artifact_path(f"{code}.png"))
        dom = self.browser.dom_snapshot(self.logger.artifact_path(f"{code}.html"))
        result = {
            "status": "failure",
            "code": code,
            "step_id": step_id,
            "message": message,
            "evidence": {"screenshot": str(screenshot), "dom_snapshot": str(dom)},
            "run_id": self.logger.run_id,
        }
        self._write_result(result)
        self.logger.event("run_failed", result)
        return result

    def _finish_escalated(self, request: dict[str, Any], step_id: str) -> dict[str, Any]:
        result = {
            "status": "escalated",
            "code": "human_intervention_required",
            "step_id": step_id,
            "intervention": request,
            "run_id": self.logger.run_id,
        }
        self._write_result(result)
        self.logger.event("run_escalated", result)
        return result

    def _write_result(self, result: dict[str, Any]) -> None:
        path = self.logger.evidence_dir / f"result.{self.logger.run_id}.json"
        path.write_text(json.dumps(self.policy.redact(result), indent=2), encoding="utf-8")


def execute_step(browser: BrowserSession, policy: Policy, step: FlowStep, parameters: dict[str, Any]) -> dict[str, Any]:
    policy.check_action(step.action, step.risk)
    if step.action == "click":
        if not step.target:
            raise ExecutionFailure("missing_target", "Click step has no target", step.id)
        resolved = browser.click(step.target)
        return {"ok": True, "resolved": resolved}
    if step.action == "fill":
        if not step.target:
            raise ExecutionFailure("missing_target", "Fill step has no target", step.id)
        value = materialize(step.value_template, parameters)
        resolved = browser.fill(step.target, value)
        return {"ok": True, "resolved": resolved, "value": "<redacted_parameter>"}
    if step.action == "extract":
        if not step.target:
            raise ExecutionFailure("missing_target", "Extract step has no target", step.id)
        value = browser.extract(step.target)
        if value == "":
            raise ExecutionFailure("empty_extract", "Extracted value was empty", step.id)
        return {"ok": True, "value": value}
    if step.action == "checkpoint":
        if not step.target or not step.target.text:
            raise ExecutionFailure("missing_checkpoint_text", "Checkpoint requires target.text", step.id)
        if not browser.is_text_present(step.target.text):
            raise ExecutionFailure("checkpoint_not_found", f"Checkpoint text not present: {step.target.text}", step.id)
        return {"ok": True, "checkpoint": step.target.text}
    if step.action == "wait":
        return {"ok": True}
    raise ExecutionFailure("unsupported_action", f"Unsupported replay action: {step.action}", step.id)


def build_member_balance_artifact(target_url: str, run_id: str, planner_name: str, steps: list[FlowStep]) -> CapabilityArtifact:
    return CapabilityArtifact(
        schema_version="cua.capability.v1",
        capability_id="member_balance_lookup",
        name="Lookup member savings balance",
        description="Looks up a member in the local legacy core console and returns the member name and current savings balance.",
        target_app="fiserv-classic-console-demo",
        target_url=target_url,
        created_at=utc_now_iso(),
        created_by=planner_name,
        discovery_run_id=run_id,
        policy_ref="config/policy.json",
        parameters={
            "member_id": {
                "type": "string",
                "required": True,
                "description": "Member number supplied by the calling agent. Stored as a parameter, not as a literal artifact value.",
                "sensitive": True,
                "pattern": "^\\d{5,12}$",
            }
        },
        outputs={
            "member_found": {"type": "boolean", "description": "False when the member lookup returns a known not-found outcome."},
            "member_name": {"type": "string", "description": "Visible member name from the profile screen."},
            "savings_balance": {"type": "currency_string", "description": "Visible savings balance exactly as displayed by the application."},
        },
        steps=steps,
        success_condition={"text_present": "Member Profile"},
        business_outcomes=[
            {
                "code": "member_not_found",
                "status": "business_outcome",
                "when": {"text_present": "No member found"},
                "message": "The target application completed the lookup and reported no matching member.",
                "outputs": {"member_found": False},
            }
        ],
        error_contract={
            "success": {"status": "success", "outputs": ["member_found", "member_name", "savings_balance"]},
            "business_outcome": {"status": "business_outcome", "codes": ["member_not_found"]},
            "recoverable": {"codes": ["transient_host_notice"], "behavior": "dismiss known interstitial and continue"},
            "failure": {
                "codes": ["validation_error", "permission_denied", "checkpoint_failed", "step_failed"],
                "includes": ["step_id", "message", "screenshot", "dom_snapshot"],
            },
            "escalated": {"codes": ["human_intervention_required"], "behavior": "pause same session and transfer control"},
        },
        tenant_model={
            "vendor_product": "fiserv-classic-console-demo",
            "artifact_scope": "vendor_base",
            "tenant_overrides": {
                "allowed": True,
                "examples": ["target_url", "branding text", "alternate label synonyms", "known interstitial handlers"],
            },
            "drift_detection": "Replay records locator fallback used, checkpoint result, screenshots on failure, and can be replayed as a tenant smoke test.",
        },
    )


def parse_member_id(value: str) -> str:
    if not re.fullmatch(r"\d{5,12}", value):
        raise ValueError("member_id must be 5-12 digits")
    return value

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class PlannerDecision:
    action: str
    step_id: str
    reason: str
    target: dict[str, Any] | None = None
    value_template: str | None = None
    output_key: str | None = None
    risk: str = "safe"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PlannerDecision":
        return cls(
            action=raw["action"],
            step_id=raw.get("step_id") or raw.get("id") or raw["action"],
            reason=raw.get("reason", ""),
            target=raw.get("target"),
            value_template=raw.get("value_template"),
            output_key=raw.get("output_key"),
            risk=raw.get("risk", "safe"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "step_id": self.step_id,
            "reason": self.reason,
            "target": self.target,
            "value_template": self.value_template,
            "output_key": self.output_key,
            "risk": self.risk,
        }


class Planner(Protocol):
    provider_name: str

    def next_action(
        self,
        goal: str,
        observation: dict[str, Any],
        parameters: dict[str, Any],
        history: list[dict[str, Any]],
        outputs: dict[str, Any],
    ) -> PlannerDecision:
        ...


class ScriptedPlanner:
    provider_name = "scripted-local-planner"

    def next_action(
        self,
        goal: str,
        observation: dict[str, Any],
        parameters: dict[str, Any],
        history: list[dict[str, Any]],
        outputs: dict[str, Any],
    ) -> PlannerDecision:
        done = {item["step_id"] for item in history}
        text = observation.get("text", "")
        if "open_member_servicing" not in done:
            return PlannerDecision(
                action="click",
                step_id="open_member_servicing",
                reason="The goal requires member data, so open the member servicing module from the operator home screen.",
                target={
                    "strategy": "visible_text",
                    "text": "Member Servicing",
                    "role": "link",
                    "rationale": "Visible link text is stable across branded tenant variants even when DOM ids are absent.",
                },
            )
        if "fill_member_id" not in done:
            return PlannerDecision(
                action="fill",
                step_id="fill_member_id",
                reason="The member lookup screen has a row-labeled member/account input. Use a parameter instead of recording the literal value.",
                target={
                    "strategy": "row_label",
                    "label": "Member / Account Number",
                    "role": "input",
                    "rationale": "Legacy table row labels are more robust here than generated CSS paths.",
                },
                value_template="{{member_id}}",
            )
        if "submit_member_search" not in done:
            return PlannerDecision(
                action="click",
                step_id="submit_member_search",
                reason="Submit the lookup form after the parameterized member id has been entered.",
                target={
                    "strategy": "visible_text",
                    "text": "Search",
                    "role": "button",
                    "rationale": "Button text is short, visible, and stable for replay.",
                },
            )
        if "No member found" in text:
            return PlannerDecision(
                action="finish",
                step_id="finish_member_not_found",
                reason="The app returned a known business outcome: the searched member does not exist.",
            )
        if "Permission denied" in text:
            return PlannerDecision(
                action="finish",
                step_id="finish_permission_denied",
                reason="The app returned a permission denial, which should not be bypassed by automation.",
            )
        if "open_member_profile" not in done:
            return PlannerDecision(
                action="click",
                step_id="open_member_profile",
                reason="The search results contain a matching member row; open the profile detail page.",
                target={
                    "strategy": "visible_text",
                    "text": "Open Profile",
                    "role": "link",
                    "rationale": "The profile link is identified by its visible action text rather than a brittle table coordinate alone.",
                },
            )
        if "extract_member_name" not in done:
            return PlannerDecision(
                action="extract",
                step_id="extract_member_name",
                reason="Capture the member name output from the profile table row.",
                target={
                    "strategy": "near_text",
                    "label": "Member Name",
                    "extractor": {"type": "table_row_value", "label": "Member Name"},
                    "rationale": "The value is the sibling cell next to a stable row label.",
                },
                output_key="member_name",
            )
        if "extract_savings_balance" not in done:
            return PlannerDecision(
                action="extract",
                step_id="extract_savings_balance",
                reason="Read the current savings balance from the detail page.",
                target={
                    "strategy": "near_text",
                    "label": "Savings Balance",
                    "extractor": {"type": "table_row_value", "label": "Savings Balance"},
                    "rationale": "The balance is stored as a visible table value adjacent to its label, matching many legacy back-office screens.",
                },
                output_key="savings_balance",
            )
        if "verify_profile_checkpoint" not in done:
            return PlannerDecision(
                action="checkpoint",
                step_id="verify_profile_checkpoint",
                reason="Verify the final state before saving the capability.",
                target={
                    "strategy": "visible_text",
                    "text": "Member Profile",
                    "rationale": "This checkpoint confirms the run reached the expected profile page.",
                },
            )
        return PlannerDecision(action="finish", step_id="finish_success", reason="The requested outputs were extracted and checkpointed.")


class OpenAIPlanner:
    provider_name = "openai-compatible-chat"

    def __init__(self, model: str | None = None):
        self.api_key = os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is required for --llm openai")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
        self.base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1/chat/completions")

    def next_action(
        self,
        goal: str,
        observation: dict[str, Any],
        parameters: dict[str, Any],
        history: list[dict[str, Any]],
        outputs: dict[str, Any],
    ) -> PlannerDecision:
        system = (
            "You are controlling a browser-based legacy banking UI. "
            "Return exactly one JSON object with keys action, step_id, reason, target, value_template, output_key, risk. "
            "Allowed actions: click, fill, extract, checkpoint, finish. "
            "Use parameter templates like {{member_id}} instead of literal sensitive values. "
            "Targets should use visible_text, row_label, or near_text strategies with text or label fields. "
            "Never choose risky actions unless the user goal explicitly requires them."
        )
        user = {
            "goal": goal,
            "parameters": list(parameters.keys()),
            "observation": observation,
            "history": history,
            "outputs_so_far": list(outputs.keys()),
        }
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code == 429:
                raise RuntimeError(
                    "OpenAI returned HTTP 429. The key is valid enough to reach the API, "
                    "but the account is rate-limited or has no available billing/credits. "
                    f"Response body: {body}"
                ) from exc
            raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {body}") from exc
        content = raw["choices"][0]["message"]["content"]
        return PlannerDecision.from_dict(json.loads(content))

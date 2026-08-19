from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


ActionType = Literal["navigate", "click", "fill", "extract", "checkpoint", "wait", "handoff"]
RiskClass = Literal["safe", "risky"]


def utc_now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class TargetSpec:
    strategy: str
    label: str | None = None
    text: str | None = None
    role: str | None = None
    css: str | None = None
    frame: str = "main"
    rationale: str = ""
    extractor: dict[str, Any] = field(default_factory=dict)


@dataclass
class FlowStep:
    id: str
    action: ActionType
    description: str
    target: TargetSpec | None = None
    value_template: str | None = None
    output_key: str | None = None
    risk: RiskClass = "safe"
    timeout_ms: int = 5000


@dataclass
class CapabilityArtifact:
    schema_version: str
    capability_id: str
    name: str
    description: str
    target_app: str
    target_url: str
    created_at: str
    created_by: str
    discovery_run_id: str
    policy_ref: str
    parameters: dict[str, dict[str, Any]]
    outputs: dict[str, dict[str, Any]]
    steps: list[FlowStep]
    success_condition: dict[str, Any]
    business_outcomes: list[dict[str, Any]]
    error_contract: dict[str, Any]
    tenant_model: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CapabilityArtifact":
        steps: list[FlowStep] = []
        for step_raw in raw["steps"]:
            target_raw = step_raw.get("target")
            target = TargetSpec(**target_raw) if target_raw else None
            steps.append(FlowStep(**{**step_raw, "target": target}))
        return cls(**{**raw, "steps": steps})

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "CapabilityArtifact":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

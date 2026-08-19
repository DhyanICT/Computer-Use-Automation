from __future__ import annotations

import json
import uuid
import dataclasses
from pathlib import Path
from typing import Any

from .types import utc_now_iso


class EvidenceLogger:
    def __init__(self, evidence_dir: Path, phase: str, policy, run_id: str | None = None):
        self.evidence_dir = evidence_dir
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.phase = phase
        self.run_id = run_id or f"{phase}-{utc_now_iso().replace(':', '').replace('+', 'Z')}-{uuid.uuid4().hex[:8]}"
        self.path = self.evidence_dir / f"{self.phase}.{self.run_id}.jsonl"
        self.policy = policy

    def event(self, event_type: str, payload: dict[str, Any]) -> None:
        payload = to_jsonable(payload)
        record = {
            "ts": utc_now_iso(),
            "run_id": self.run_id,
            "phase": self.phase,
            "event_type": event_type,
            "payload": self.policy.redact(payload),
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def artifact_path(self, suffix: str) -> Path:
        safe_suffix = suffix.replace("/", "_").replace("\\", "_")
        return self.evidence_dir / f"{self.phase}.{self.run_id}.{safe_suffix}"


def to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    return value

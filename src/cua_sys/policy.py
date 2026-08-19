from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .redaction import Redactor, SensitivePattern


class PolicyViolation(RuntimeError):
    pass


@dataclass(frozen=True)
class Policy:
    allowed_origins: list[str]
    allowed_actions: set[str]
    risky_action_policy: str
    redactor: Redactor

    @classmethod
    def from_file(cls, path: Path) -> "Policy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        patterns = [SensitivePattern(**item) for item in raw.get("sensitive_patterns", [])]
        return cls(
            allowed_origins=list(raw["allowed_origins"]),
            allowed_actions=set(raw["allowed_actions"]),
            risky_action_policy=raw.get("risky_action_policy", "escalate"),
            redactor=Redactor(patterns),
        )

    def check_url(self, url: str) -> None:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            raise PolicyViolation(f"URL is not absolute: {url}")
        origin = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or self._default_port(parsed.scheme)}"
        if not any(fnmatch.fnmatch(origin, allowed) for allowed in self.allowed_origins):
            raise PolicyViolation(f"Origin is outside allowlist: {origin}")

    def check_action(self, action_type: str, risk: str = "safe") -> None:
        if action_type not in self.allowed_actions:
            raise PolicyViolation(f"Action is not allowed: {action_type}")
        if risk == "risky" and self.risky_action_policy == "block":
            raise PolicyViolation("Risky action blocked by policy")

    def should_escalate_risky(self, risk: str) -> bool:
        return risk == "risky" and self.risky_action_policy == "escalate"

    def redact(self, value: Any) -> Any:
        return self.redactor.data(value)

    @staticmethod
    def _default_port(scheme: str) -> int:
        return 443 if scheme == "https" else 80

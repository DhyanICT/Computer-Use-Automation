from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class SensitivePattern:
    name: str
    regex: str
    replacement: str


class Redactor:
    _SKIP_KEYS = {
        "artifact_path",
        "debug_url",
        "dom_snapshot",
        "evidence_dir",
        "id",
        "resume_file",
        "run_id",
        "screenshot",
    }

    def __init__(self, patterns: Iterable[SensitivePattern]):
        self._patterns = [(p.name, re.compile(p.regex), p.replacement) for p in patterns]

    def text(self, value: str) -> str:
        redacted = value
        for _name, pattern, replacement in self._patterns:
            redacted = pattern.sub(replacement, redacted)
        return redacted

    def data(self, value: Any) -> Any:
        value = copy.deepcopy(value)
        return self._redact_any(value, key=None)

    def _redact_any(self, value: Any, key: str | None) -> Any:
        if key in self._SKIP_KEYS:
            return value
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self._redact_any(item, key=None) for item in value]
        if isinstance(value, dict):
            return {item_key: self._redact_any(item, key=item_key) for item_key, item in value.items()}
        return value

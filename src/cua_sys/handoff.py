from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from .cdp import BrowserSession
from .evidence import EvidenceLogger
from .types import TargetSpec, utc_now_iso


class HandoffCoordinator:
    def __init__(self, evidence_dir: Path, logger: EvidenceLogger):
        self.evidence_dir = evidence_dir
        self.logger = logger
        self.intervention_dir = evidence_dir / "interventions"
        self.intervention_dir.mkdir(parents=True, exist_ok=True)

    def request(
        self,
        browser: BrowserSession,
        reason: str,
        context: dict[str, Any],
        auto_operator: bool = False,
    ) -> dict[str, Any]:
        intervention_id = f"hitl-{uuid.uuid4().hex[:10]}"
        screenshot_path = self.intervention_dir / f"{intervention_id}.png"
        dom_path = self.intervention_dir / f"{intervention_id}.html"
        browser.screenshot(screenshot_path)
        browser.dom_snapshot(dom_path)
        request = {
            "id": intervention_id,
            "created_at": utc_now_iso(),
            "status": "waiting_for_human",
            "control_owner": "human",
            "reason": reason,
            "context": context,
            "debug_url": browser.debug_url,
            "same_session": True,
            "screenshot": str(screenshot_path),
            "dom_snapshot": str(dom_path),
            "resume_file": str(self.intervention_dir / f"{intervention_id}.resume.json"),
        }
        request_path = self.intervention_dir / f"{intervention_id}.json"
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.logger.event("handoff_requested", request)
        if auto_operator:
            return self._auto_operator(browser, request, request_path)
        return self._wait_for_resume(request, request_path)

    def _wait_for_resume(self, request: dict[str, Any], request_path: Path) -> dict[str, Any]:
        resume_path = Path(request["resume_file"])
        deadline = time.time() + 300
        while time.time() < deadline:
            if resume_path.exists():
                resume = json.loads(resume_path.read_text(encoding="utf-8"))
                request.update({"status": "resumed", "control_owner": "automation", "resume": resume})
                request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
                self.logger.event("handoff_resumed", request)
                return request
            time.sleep(1)
        request.update({"status": "timed_out"})
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.logger.event("handoff_timeout", request)
        return request

    def _auto_operator(self, browser: BrowserSession, request: dict[str, Any], request_path: Path) -> dict[str, Any]:
        human_actions: list[dict[str, Any]] = []
        if browser.is_text_present("Member Profile") and browser.is_text_present("Freeze Card"):
            browser.click(TargetSpec(strategy="visible_text", text="Freeze Card", role="link", rationale="Human reviews the risky page."))
            human_actions.append({"actor": "human_operator", "action": "click", "target": "Freeze Card"})
        if browser.is_text_present("Risky operation") and browser.is_text_present("Cancel"):
            browser.click(TargetSpec(strategy="visible_text", text="Cancel", role="link", rationale="Human declines the irreversible action."))
            human_actions.append({"actor": "human_operator", "action": "click", "target": "Cancel"})
        resume = {
            "resumed_at": utc_now_iso(),
            "decision": "declined_risky_action",
            "summary": "Operator inspected the live session and declined to freeze the card.",
            "human_actions": human_actions,
        }
        Path(request["resume_file"]).write_text(json.dumps(resume, indent=2), encoding="utf-8")
        request.update({"status": "resumed", "control_owner": "automation", "resume": resume})
        request_path.write_text(json.dumps(request, indent=2), encoding="utf-8")
        self.logger.event("handoff_human_actions", resume)
        self.logger.event("handoff_resumed", request)
        return request

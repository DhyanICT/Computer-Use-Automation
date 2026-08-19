from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from target_app.server import TargetServer

from .cdp import BrowserSession
from .engine import DiscoveryEngine, ReplayEngine, parse_member_id
from .evidence import EvidenceLogger
from .handoff import HandoffCoordinator
from .llm import OpenAIPlanner, ScriptedPlanner
from .policy import Policy
from .types import CapabilityArtifact, FlowStep, TargetSpec


ROOT = Path(__file__).resolve().parents[2]


def load_policy(path: Path | None = None) -> Policy:
    return Policy.from_file(path or ROOT / "config" / "policy.json")


def browser(work_dir: Path, headless: bool) -> BrowserSession:
    work_dir.mkdir(parents=True, exist_ok=True)
    return BrowserSession(work_dir=work_dir, headless=headless).start()


def build_planner(kind: str):
    if kind == "scripted":
        return ScriptedPlanner()
    if kind == "openai":
        return OpenAIPlanner()
    raise ValueError(f"Unknown planner: {kind}")


def cmd_serve_target(args: argparse.Namespace) -> int:
    server = TargetServer(host=args.host, port=args.port).start()
    print(server.base_url)
    print("Press Ctrl+C to stop.")
    try:
        while True:
            import time

            time.sleep(3600)
    except KeyboardInterrupt:
        server.stop()
        return 0


def cmd_discover(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    planner = build_planner(args.llm)
    params = {"member_id": parse_member_id(args.member_id)}
    logger = EvidenceLogger(args.evidence, "discovery", policy)
    session = browser(ROOT / "work", headless=not args.headful)
    try:
        engine = DiscoveryEngine(session, policy, logger)
        artifact = engine.run(args.goal, args.target, params, planner, args.artifact)
        print(json.dumps({"artifact": str(args.artifact), "run_id": logger.run_id, "capability": artifact.capability_id}, indent=2))
    finally:
        session.close()
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    params = {"member_id": parse_member_id(args.member_id)}
    artifact = CapabilityArtifact.read(args.artifact)
    if args.target:
        artifact.target_url = args.target
    logger = EvidenceLogger(args.evidence, "replay", policy)
    session = browser(ROOT / "work", headless=not args.headful)
    try:
        result = ReplayEngine(session, policy, logger).run(artifact, params)
        print(json.dumps(policy.redact(result), indent=2))
        return 0 if result["status"] in {"success", "business_outcome", "escalated"} else 2
    finally:
        session.close()


def cmd_demo(args: argparse.Namespace) -> int:
    evidence = args.evidence
    artifact_path = args.artifact
    evidence.mkdir(parents=True, exist_ok=True)
    server = TargetServer().start()
    try:
        target = server.base_url + "/"
        goal = "Look up member 12345 and read their current savings balance."
        print(f"target={target}")
        discover_args = argparse.Namespace(
            policy=args.policy,
            llm=args.llm,
            member_id="12345",
            evidence=evidence,
            artifact=artifact_path,
            goal=goal,
            target=target,
            headful=args.headful,
        )
        cmd_discover(discover_args)
        replay_success = argparse.Namespace(
            policy=args.policy,
            member_id="12345",
            evidence=evidence,
            artifact=artifact_path,
            target=target,
            headful=args.headful,
        )
        cmd_replay(replay_success)
        replay_not_found = argparse.Namespace(
            policy=args.policy,
            member_id="99999",
            evidence=evidence,
            artifact=artifact_path,
            target=target,
            headful=args.headful,
        )
        cmd_replay(replay_not_found)
        cmd_demo_handoff(
            argparse.Namespace(policy=args.policy, member_id="12345", evidence=evidence, target=target, headful=args.headful)
        )
        print(json.dumps({"artifact": str(artifact_path), "evidence_dir": str(evidence)}, indent=2))
    finally:
        server.stop()
    return 0


def cmd_demo_handoff(args: argparse.Namespace) -> int:
    policy = load_policy(args.policy)
    logger = EvidenceLogger(args.evidence, "handoff", policy)
    session = browser(ROOT / "work", headless=not args.headful)
    try:
        session.navigate(args.target)
        session.click(TargetSpec(strategy="visible_text", text="Member Servicing", role="link"))
        session.fill(TargetSpec(strategy="row_label", label="Member / Account Number", role="input"), parse_member_id(args.member_id))
        session.click(TargetSpec(strategy="visible_text", text="Search", role="button"))
        session.click(TargetSpec(strategy="visible_text", text="Open Profile", role="link"))
        risky_step = FlowStep(
            id="freeze_card",
            action="click",
            description="Freeze the member debit card. This is deliberately risky and must be escalated.",
            target=TargetSpec(strategy="visible_text", text="Freeze Card", role="link"),
            risk="risky",
        )
        logger.event("risky_step_detected", {"step": risky_step})
        if policy.should_escalate_risky(risky_step.risk):
            coordinator = HandoffCoordinator(args.evidence, logger)
            result = coordinator.request(
                session,
                reason="Risky irreversible operation requires a human operator",
                context={"step": risky_step.id, "target": risky_step.target.__dict__ if risky_step.target else None},
                auto_operator=True,
            )
            output = {"status": "escalated", "run_id": logger.run_id, "intervention": result}
            result_path = args.evidence / f"result.{logger.run_id}.json"
            result_path.write_text(json.dumps(policy.redact(output), indent=2), encoding="utf-8")
            print(json.dumps(policy.redact(output), indent=2))
        return 0
    finally:
        session.close()


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Computer-use automation take-home project")
    parser.add_argument("--policy", type=Path, default=ROOT / "config" / "policy.json")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve-target", help="Run the local legacy target app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8088)
    serve.set_defaults(func=cmd_serve_target)

    discover = sub.add_parser("discover", help="Run LLM-driven discovery and save a capability artifact")
    discover.add_argument("--goal", required=True)
    discover.add_argument("--target", required=True)
    discover.add_argument("--member-id", required=True)
    discover.add_argument("--llm", choices=["scripted", "openai"], default="scripted")
    discover.add_argument("--artifact", type=Path, default=ROOT / "artifacts" / "member_balance_lookup.json")
    discover.add_argument("--evidence", type=Path, default=ROOT / "evidence")
    discover.add_argument("--headful", action="store_true", help="Show Chrome for manual inspection")
    discover.set_defaults(func=cmd_discover)

    replay = sub.add_parser("replay", help="Replay a saved capability without LLM decisions")
    replay.add_argument("--artifact", type=Path, default=ROOT / "artifacts" / "member_balance_lookup.json")
    replay.add_argument("--target")
    replay.add_argument("--member-id", required=True)
    replay.add_argument("--evidence", type=Path, default=ROOT / "evidence")
    replay.add_argument("--headful", action="store_true")
    replay.set_defaults(func=cmd_replay)

    demo = sub.add_parser("demo", help="Run discovery, replay success, replay business outcome, and handoff demos")
    demo.add_argument("--llm", choices=["scripted", "openai"], default="scripted")
    demo.add_argument("--artifact", type=Path, default=ROOT / "artifacts" / "member_balance_lookup.json")
    demo.add_argument("--evidence", type=Path, default=ROOT / "evidence")
    demo.add_argument("--headful", action="store_true")
    demo.set_defaults(func=cmd_demo)

    handoff = sub.add_parser("demo-handoff", help="Demonstrate same-session human handoff on a risky action")
    handoff.add_argument("--target", required=True)
    handoff.add_argument("--member-id", default="12345")
    handoff.add_argument("--evidence", type=Path, default=ROOT / "evidence")
    handoff.add_argument("--headful", action="store_true")
    handoff.set_defaults(func=cmd_demo_handoff)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

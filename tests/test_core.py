from __future__ import annotations

import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cua_sys.engine import materialize
from cua_sys.policy import Policy, PolicyViolation
from cua_sys.types import CapabilityArtifact


class CoreTests(unittest.TestCase):
    def test_policy_allows_local_target_and_blocks_external_origin(self) -> None:
        policy = Policy.from_file(ROOT / "config" / "policy.json")
        policy.check_url("http://127.0.0.1:8088/")
        with self.assertRaises(PolicyViolation):
            policy.check_url("https://example.com/")

    def test_redaction_masks_member_ids_without_masking_debug_ports(self) -> None:
        policy = Policy.from_file(ROOT / "config" / "policy.json")
        redacted = policy.redact({"text": "member 12345 not found", "debug_url": "http://127.0.0.1:65199"})
        self.assertEqual(redacted["text"], "member <member_id> not found")
        self.assertEqual(redacted["debug_url"], "http://127.0.0.1:65199")

    def test_template_materialization(self) -> None:
        self.assertEqual(materialize("{{member_id}}", {"member_id": "12345"}), "12345")

    def test_generated_artifact_schema_loads(self) -> None:
        artifact_path = ROOT / "artifacts" / "member_balance_lookup.json"
        if not artifact_path.exists():
            self.skipTest("Demo artifact has not been generated yet")
        artifact = CapabilityArtifact.read(artifact_path)
        self.assertEqual(artifact.schema_version, "cua.capability.v1")
        self.assertIn("member_id", artifact.parameters)
        self.assertTrue(any(step.output_key == "savings_balance" for step in artifact.steps))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

from pipeline.rules import validate_rule_package  # noqa: E402


class RulePackageTests(unittest.TestCase):
    def test_offline_rule_package_has_exactly_fifty_published_rules(self) -> None:
        rules = SKILL_ROOT / "references" / "rules"
        report = validate_rule_package(rules)
        self.assertEqual("ok", report["status"], report)
        self.assertEqual(50, report["rule_count"])
        manifest = json.loads((rules / "release-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual([], manifest["coverage_gaps"])

    def test_rule_package_contains_no_history_or_learning_files(self) -> None:
        rules = SKILL_ROOT / "references" / "rules"
        names = {path.name.lower() for path in rules.iterdir()}
        self.assertFalse(any(name.endswith(".jsonl") for name in names))
        self.assertFalse(any("candidate" in name or "evidence-index" in name for name in names))
        self.assertFalse((SKILL_ROOT / "references" / "rules.previous").exists())

    def test_rule_evidence_refs_are_local_or_user_confirmations(self) -> None:
        rules = SKILL_ROOT / "references" / "rules"
        architecture = (SKILL_ROOT / "references" / "rule-architecture.md").read_text(encoding="utf-8")
        invalid: list[str] = []
        for path in rules.glob("*.json"):
            if path.name in {"rule-index.json", "release-manifest.json"}:
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            for rule in payload.get("rules", []):
                for reference in rule.get("evidence_refs", []):
                    if reference.startswith("user-confirmation:"):
                        continue
                    if reference.startswith("rule-architecture.md#"):
                        heading = reference.split("#", 1)[1]
                        if f"# {heading}" not in architecture:
                            invalid.append(reference)
                        continue
                    invalid.append(reference)
        self.assertEqual([], invalid)


if __name__ == "__main__":
    unittest.main()

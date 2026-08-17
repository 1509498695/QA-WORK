from __future__ import annotations

import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


class SkillStructureTests(unittest.TestCase):
    def test_standalone_structure_has_no_online_delivery_or_history_files(self) -> None:
        forbidden_names = {
            "create_feishu_case_sheet.py",
            "evidence-index.jsonl",
            "rules.previous",
        }
        existing = {path.name for path in SKILL_ROOT.rglob("*")}
        self.assertTrue(forbidden_names.isdisjoint(existing), forbidden_names & existing)
        self.assertFalse(any(path.suffix == ".jsonl" for path in SKILL_ROOT.rglob("*")))
        self.assertTrue((SKILL_ROOT / "assets" / "local-case-template.xlsx").exists())
        self.assertTrue((SKILL_ROOT / "SKILL.md").exists())
        self.assertTrue(
            (SKILL_ROOT / "references" / "schemas" / "pending-boundary-confirmations.schema.json").exists()
        )

    def test_skill_frontmatter_declares_exact_name(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(content.startswith("---\nname: qa-case-xlsx-local\n"))
        self.assertIn("不读取或写入飞书", content)
        frontmatter = content.split("---", 2)[1]
        self.assertNotIn("<", frontmatter)
        self.assertNotIn(">", frontmatter)

    def test_openai_interface_is_utf8_and_mentions_skill(self) -> None:
        content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "qa-case-xlsx-local"', content)
        self.assertIn('brand_color: "#86D3E5"', content)
        self.assertIn("$qa-case-xlsx-local", content)


if __name__ == "__main__":
    unittest.main()

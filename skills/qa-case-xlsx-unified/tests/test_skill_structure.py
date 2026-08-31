from __future__ import annotations

import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


class SkillStructureTests(unittest.TestCase):
    def test_frontmatter_and_provider_boundary(self) -> None:
        content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertTrue(content.startswith("---\nname: qa-case-xlsx-unified\n"))
        self.assertIn("workspace_feishu", content)
        self.assertIn("不调用 `lg-feishu`", content)
        self.assertIn("不修改 `qa-case-xlsx-local`", content)
        self.assertIn("同对象回读", content)
        self.assertNotIn("TODO", content)

    def test_required_resources_exist(self) -> None:
        expected = {
            SKILL_ROOT / "references" / "unified-source-v2.md",
            SKILL_ROOT / "references" / "generation-contract.md",
            SKILL_ROOT / "references" / "feishu-delivery.md",
            SKILL_ROOT / "references" / "project-modules.json",
            SKILL_ROOT / "references" / "provisional-modules.json",
            SKILL_ROOT / "scripts" / "build_feishu_case_spec.py",
            SKILL_ROOT / "agents" / "openai.yaml",
        }

        self.assertFalse(expected - {path for path in expected if path.is_file()})

    def test_openai_metadata_declares_workspace_feishu_dependency(self) -> None:
        content = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

        self.assertIn('display_name: "qa-case-xlsx-unified"', content)
        self.assertIn("$qa-case-xlsx-unified", content)
        self.assertIn('value: "workspace_feishu"', content)


if __name__ == "__main__":
    unittest.main()

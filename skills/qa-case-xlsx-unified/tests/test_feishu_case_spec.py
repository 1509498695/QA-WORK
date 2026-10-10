from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "build_feishu_case_spec.py"
SPEC = importlib.util.spec_from_file_location("build_feishu_case_spec", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def final_cases() -> dict:
    return {
        "schema_version": "2.0",
        "run_id": "RUN-001",
        "input_sha256": "0" * 64,
        "rule_release_version": "2026.08",
        "cases": [
            {
                "用例编号": "1",
                "一级模块": "飞书写入验证",
                "二级模块": "文本回读",
                "检查点": "中文文本写入后保持完整",
                "前置条件": "目标工作表已创建",
                "操作步骤": "1. 写入中文文本\n2. 重新读取目标单元格",
                "预期结果": "回读文本与写入文本逐字一致。",
                "优先级": "P1",
                "测试结果": "",
                "备注": "",
            },
            {
                "用例编号": "2",
                "一级模块": "飞书写入验证",
                "二级模块": "数值回读",
                "检查点": "数值写入后保持数值类型",
                "前置条件": "目标工作表已创建",
                "操作步骤": "1. 写入数值 20260828\n2. 重新读取目标单元格",
                "预期结果": "回读值为数值 20260828。",
                "优先级": "P1",
                "测试结果": "",
                "备注": "",
            },
        ],
    }


class FeishuCaseSpecTests(unittest.TestCase):
    def test_default_template_registration_is_loaded(self) -> None:
        config = MODULE.load_default_template()

        self.assertEqual(config["role"], "read_only_layout_reference")
        self.assertEqual(
            config["locator"],
            "https://t6bdpf8yjg.feishu.cn/wiki/Y5F6wI4tRixB84kzTHhcIYo2nFg",
        )
        self.assertEqual(config["layout_contract"]["case_body_start_row"], 11)
        self.assertEqual(config["layout_contract"]["frozen_row_count"], 10)

    def test_builds_default_template_a_to_j_spec(self) -> None:
        spec = MODULE.build_spec(
            final_cases(),
            "飞书用例生成验证",
            source_urls=["https://example.feishu.cn/wiki/source"],
        )

        self.assertEqual(spec["schema_version"], "workspace-feishu/sheet-delivery/v1")
        self.assertEqual(spec["row_count"], 12)
        self.assertEqual(spec["column_count"], 10)
        self.assertEqual(spec["values"][0], ["飞书用例生成验证", *([None] * 9)])
        self.assertEqual(spec["values"][1][0], "DRI")
        self.assertEqual(spec["values"][8][0], "用例编号")
        self.assertEqual(
            spec["values"][9][1:4],
            ["一级模块", "二级模块", "检查点（必要项）"],
        )
        self.assertEqual(spec["values"][10][0], {"formula": "=ROW()-10"})
        self.assertEqual(spec["values"][11][0], {"formula": "=ROW()-10"})
        self.assertEqual(spec["values"][1][3], {"formula": "=COUNTA($A$11:$A$12)"})
        self.assertEqual(
            spec["values"][2][8],
            {"formula": '=COUNTIF($I$11:$I$12,"P")'},
        )
        self.assertIn(
            "策划来源 1: https://example.feishu.cn/wiki/source",
            spec["values"][5][3],
        )
        self.assertIn("用例模板: https://t6bdpf8yjg.feishu.cn/wiki/", spec["values"][5][3])
        self.assertEqual(spec["frozen_row_count"], 10)
        self.assertIn(
            {"row_start": 10, "row_end": 12, "column_start": 1, "column_end": 2},
            spec["merges"],
        )
        self.assertFalse(any("wrap_text" in item["style"] for item in spec["style_ranges"]))

    def test_module_merges_preserve_case_semantics(self) -> None:
        payload = final_cases()
        payload["cases"][1]["二级模块"] = "文本回读"

        spec = MODULE.build_spec(payload, "飞书用例生成验证")
        rows = MODULE.case_rows(payload)
        MODULE.validate_spec_semantics(spec, rows)

        self.assertIn(
            {"row_start": 10, "row_end": 12, "column_start": 2, "column_end": 3},
            spec["merges"],
        )
        self.assertEqual(spec["values"][10][2], "文本回读")
        self.assertIsNone(spec["values"][11][2])

    def test_build_is_deterministic(self) -> None:
        source_urls = ["https://example.feishu.cn/wiki/source"]
        first = MODULE.build_spec(final_cases(), "飞书用例生成验证", source_urls)
        second = MODULE.build_spec(final_cases(), "飞书用例生成验证", source_urls)

        self.assertEqual(MODULE.canonical_bytes(first), MODULE.canonical_bytes(second))
        self.assertEqual(MODULE.spec_sha256(first), MODULE.spec_sha256(second))

    def test_rejects_non_contiguous_case_numbers(self) -> None:
        payload = final_cases()
        payload["cases"][1]["用例编号"] = "3"

        with self.assertRaisesRegex(MODULE.SpecBuildError, "必须为 2"):
            MODULE.build_spec(payload, "飞书用例生成验证")

    def test_rejects_unnumbered_steps(self) -> None:
        payload = final_cases()
        payload["cases"][0]["操作步骤"] = "写入中文文本"

        with self.assertRaisesRegex(MODULE.SpecBuildError, "每行必须使用"):
            MODULE.build_spec(payload, "飞书用例生成验证")

    def test_rejects_non_https_template_override(self) -> None:
        with self.assertRaisesRegex(MODULE.SpecBuildError, "完整 HTTPS URL"):
            MODULE.build_spec(
                final_cases(),
                "飞书用例生成验证",
                template_url="http://example.test/template",
            )


if __name__ == "__main__":
    unittest.main()

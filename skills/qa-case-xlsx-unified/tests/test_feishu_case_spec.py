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
    def test_builds_provider_compatible_a_to_j_spec(self) -> None:
        spec = MODULE.build_spec(final_cases(), "飞书用例生成验证")

        self.assertEqual(spec["schema_version"], "workspace-feishu/sheet-delivery/v1")
        self.assertEqual(spec["row_count"], 4)
        self.assertEqual(spec["column_count"], 10)
        self.assertEqual(spec["values"][0], ["飞书用例生成验证", *([None] * 9)])
        self.assertEqual(tuple(spec["values"][1]), MODULE.CASE_HEADERS)
        self.assertEqual(spec["values"][2][0], 1)
        self.assertEqual(spec["values"][3][0], 2)
        self.assertEqual(spec["frozen_row_count"], 2)
        self.assertEqual(
            spec["merges"],
            [{"row_start": 0, "row_end": 1, "column_start": 0, "column_end": 10}],
        )
        self.assertFalse(any("wrap_text" in item["style"] for item in spec["style_ranges"]))

    def test_build_is_deterministic(self) -> None:
        first = MODULE.build_spec(final_cases(), "飞书用例生成验证")
        second = MODULE.build_spec(final_cases(), "飞书用例生成验证")

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


if __name__ == "__main__":
    unittest.main()

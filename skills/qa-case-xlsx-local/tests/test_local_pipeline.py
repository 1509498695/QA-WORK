from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from helpers import MODULES, build_valid_run, read_json, write_payload
from pipeline.contracts import sha256_json
from pipeline.local import build_local_readiness
from pipeline.stages import validate_module_snapshot


class LocalPipelineTests(unittest.TestCase):
    def test_module_snapshot_supports_a_second_project_namespace(self) -> None:
        snapshot = {
            "schema_version": "1.0",
            "project": "WORKSPACE",
            "module_count": 1,
            "modules": [
                {
                    "module_key": "WORKSPACE::飞书写入验证",
                    "standard_name": "飞书写入验证",
                    "display_name": "飞书写入验证",
                    "official_aliases": [],
                    "status": "active",
                }
            ],
        }
        snapshot["content_sha256"] = sha256_json(snapshot)

        report = validate_module_snapshot(snapshot)

        self.assertEqual("ok", report["status"], report)
        self.assertEqual("project_modules", report["artifact"])

    def test_complete_source_generates_formal_filename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            readiness = build_valid_run(run_dir)
            self.assertEqual("ok", readiness["status"], readiness)
            self.assertEqual("formal", readiness["delivery_mode"])
            self.assertEqual("周年庆宝箱-测试用例.xlsx", readiness["output_filename"])
            self.assertEqual(0, readiness["pending_count"])
            self.assertEqual(0, readiness["boundary_confirmation_count"])

    def test_partial_source_generates_draft_filename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            readiness = build_valid_run(run_dir, source_pending=True)
            self.assertEqual("ok", readiness["status"], readiness)
            self.assertEqual("draft", readiness["delivery_mode"])
            self.assertTrue(readiness["output_filename"].endswith("-待确认草稿.xlsx"))
            self.assertGreater(readiness["pending_count"], 0)
            self.assertEqual(1, readiness["boundary_confirmation_count"])

    def test_boundary_question_alone_generates_draft_filename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            build_valid_run(run_dir)
            facts = read_json(run_dir / "source_facts.json")
            boundary = read_json(run_dir / "pending_boundary_confirmations.json")
            boundary["status"] = "awaiting_user_confirmation"
            boundary["items"] = [
                {
                    "boundary_id": "BOUNDARY-0001",
                    "module": "开放条件",
                    "question": "玩家等级恰好为 10 时是否允许开启宝箱？",
                    "recommendation": "按“达到 10 级”解释为等级大于等于 10。",
                    "source_refs": [facts["facts"][0]["source_refs"][0]],
                }
            ]
            write_payload(run_dir / "pending_boundary_confirmations.json", boundary)
            readiness = build_local_readiness(run_dir, MODULES)
            self.assertEqual("ok", readiness["status"], readiness)
            self.assertEqual("draft", readiness["delivery_mode"])
            self.assertEqual(1, readiness["boundary_confirmation_count"])
            self.assertTrue(any("边界待确认" in item for item in readiness["pending_reasons"]))

    def test_no_confirmed_fact_blocks_workbook(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            build_valid_run(run_dir)
            facts = read_json(run_dir / "source_facts.json")
            facts["facts"][0]["status"] = "pending"
            write_payload(run_dir / "source_facts.json", facts)
            readiness = build_local_readiness(run_dir, MODULES)
            self.assertEqual("invalid", readiness["status"])
            self.assertEqual("blocked", readiness["delivery_mode"])
            self.assertEqual("", readiness["output_filename"])
            self.assertTrue(any("没有 confirmed 业务事实" in item for item in readiness["blocking_errors"]))

    def test_source_conflict_forces_draft_without_auto_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            build_valid_run(run_dir)
            facts = read_json(run_dir / "source_facts.json")
            facts["conflicts"] = [
                {
                    "conflict_id": "CONFLICT-0001",
                    "topic": "奖励数量",
                    "values": [100, 200],
                    "source_refs": [facts["facts"][0]["source_refs"][0]],
                    "resolution": "pending",
                }
            ]
            write_payload(run_dir / "source_facts.json", facts)
            boundary = read_json(run_dir / "pending_boundary_confirmations.json")
            boundary["status"] = "awaiting_user_confirmation"
            boundary["items"] = [
                {
                    "boundary_id": "BOUNDARY-0001",
                    "module": "奖励结算",
                    "question": "奖励数量按 100 还是 200 结算？",
                    "recommendation": "确认唯一配置值后再转为正式用例。",
                    "source_refs": [facts["facts"][0]["source_refs"][0]],
                }
            ]
            write_payload(run_dir / "pending_boundary_confirmations.json", boundary)
            readiness = build_local_readiness(run_dir, MODULES)
            self.assertEqual("ok", readiness["status"], readiness)
            self.assertEqual("draft", readiness["delivery_mode"])
            self.assertTrue(any("来源冲突" in item for item in readiness["pending_reasons"]))

    def test_hidden_target_count_is_blocked_in_natural_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            build_valid_run(run_dir)
            facts = read_json(run_dir / "source_facts.json")
            facts["target_case_count"] = 20
            write_payload(run_dir / "source_facts.json", facts)
            readiness = build_local_readiness(run_dir, MODULES)
            self.assertEqual("invalid", readiness["status"])
            self.assertTrue(any("固定或目标数量" in item for item in readiness["blocking_errors"]))

    def test_missing_boundary_confirmation_artifact_blocks_delivery(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-run-") as temporary:
            run_dir = Path(temporary) / "audit"
            build_valid_run(run_dir)
            (run_dir / "pending_boundary_confirmations.json").unlink()
            readiness = build_local_readiness(run_dir, MODULES)
            self.assertEqual("invalid", readiness["status"])
            self.assertEqual("blocked", readiness["delivery_mode"])
            self.assertTrue(
                any("pending_boundary_confirmations.json 不可读取" in item for item in readiness["blocking_errors"])
            )


if __name__ == "__main__":
    unittest.main()

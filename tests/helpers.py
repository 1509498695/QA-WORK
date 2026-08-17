from __future__ import annotations

import json
import os
import shutil
import sys
from argparse import Namespace
from pathlib import Path
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_source_packet import build_packet  # noqa: E402
from pipeline.contracts import (  # noqa: E402
    candidate_semantic_signature,
    final_semantic_signature,
    sha256_json,
    write_json,
)
from pipeline.local import build_local_readiness  # noqa: E402


FIXTURE_SOURCES = SKILL_ROOT / "tests" / "fixtures" / "sources"
MODULES = SKILL_ROOT / "references" / "samo-project-modules.json"
RULES = SKILL_ROOT / "references" / "rules"


def pdftoppm_path() -> Path | None:
    configured = os.environ.get("QA_CASE_XLSX_PDFTOPPM")
    if configured:
        return Path(configured)
    discovered = shutil.which("pdftoppm")
    return Path(discovered) if discovered else None


def fixture_paths() -> list[Path]:
    return [
        FIXTURE_SOURCES / "anniversary-chest.docx",
        FIXTURE_SOURCES / "anniversary-rules.pdf",
        FIXTURE_SOURCES / "anniversary-config.xlsx",
        FIXTURE_SOURCES / "anniversary-notes.md",
        FIXTURE_SOURCES / "anniversary-copy.txt",
        FIXTURE_SOURCES / "reward-state.png",
    ]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_payload(path: Path, payload: dict[str, Any]) -> None:
    write_json(path, payload)


def build_source_layer(run_dir: Path, *, reviewed: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
    result = build_packet(
        Namespace(
            source=[str(path) for path in fixture_paths()],
            output_dir=run_dir,
            pdftoppm=pdftoppm_path(),
            include_hidden_sheets=False,
            include_review_sheets=False,
        )
    )
    if result["status"] != "ok":
        raise AssertionError(result)
    packet_path = run_dir / "source_packet.json"
    ledger_path = run_dir / "source_evidence_ledger.json"
    packet = read_json(packet_path)
    ledger = read_json(ledger_path)
    if reviewed:
        for file_record in packet["files"]:
            if file_record["status"] == "partial":
                file_record["status"] = "readable"
        for item in ledger["evidence"]:
            item["review_status"] = "reviewed"
            item["observations"] = ["夹具视觉内容已复核，文字与结构清晰可辨。"]
        write_payload(packet_path, packet)
        write_payload(ledger_path, ledger)
    return packet, ledger


def find_source_ref(packet: dict[str, Any], text: str) -> str:
    for file_record in packet["files"]:
        for unit in file_record["content_units"]:
            if text in str(unit.get("text") or ""):
                return str(unit["source_ref"])
    raise AssertionError(f"fixture text not found: {text}")


def build_valid_run(run_dir: Path, *, source_pending: bool = False) -> dict[str, Any]:
    run_dir.mkdir(parents=True, exist_ok=True)
    packet, ledger = build_source_layer(run_dir, reviewed=not source_pending)
    source_ref = find_source_ref(packet, "达到 10 级")
    visual_source_ref = next(
        str(item["source_ref"])
        for item in ledger["evidence"]
        if item.get("visual_review_required")
    )
    release = read_json(RULES / "release-manifest.json")["release_version"]
    meta = {
        "schema_version": "2.0",
        "run_id": "run-local-e2e",
        "input_sha256": packet["package_sha256"],
        "rule_release_version": release,
    }
    facts = {
        "schema_version": "1.0",
        "package_sha256": packet["package_sha256"],
        "requirement_name": "周年庆宝箱",
        "project_code": "cod",
        "read_status": "partial" if source_pending else "complete",
        "facts": [
            {
                "fact_id": "FACT-0001",
                "topic": "入口与资格",
                "statement": "活动开启且玩家达到 10 级后可以开启周年庆宝箱。",
                "status": "confirmed",
                "source_refs": [source_ref],
            }
        ],
        "conflicts": [],
        "pending_items": ["独立图片尚待视觉复核"] if source_pending else [],
        "count_policy": {"mode": "natural", "request_ref": ""},
    }
    blueprint = {
        **meta,
        "nodes": [
            {
                "blueprint_node_id": "NODE-0001",
                "title": "周年庆宝箱首次开启",
                "business_goal": "验证满足资格的玩家首次开启宝箱后获得奖励",
                "actors": ["达到 10 级的玩家"],
                "entry_conditions": ["周年庆活动已开启", "玩家达到 10 级"],
                "main_flow": ["进入活动中心", "打开周年庆宝箱", "确认奖励到账"],
                "observable_results": ["宝箱开启", "获得 100 金币"],
                "data_boundaries": ["等级 10"],
                "failure_recovery": [],
                "regression_baseline": [],
                "source_refs": [source_ref],
                "status": "confirmed",
            }
        ],
        "pending_items": [],
    }
    matrix = {
        **meta,
        "dimensions": [
            {
                "dimension_id": f"GR-{index:02d}",
                "status": "covered" if index in {1, 2, 4, 6} else "not_applicable",
                "reason": "夹具已提供对应事实" if index in {1, 2, 4, 6} else "本最小夹具未涉及该维度",
                "evidence_refs": [source_ref] if index in {1, 2, 4, 6} else [],
            }
            for index in range(1, 9)
        ],
    }
    boundary_confirmations = {
        "schema_version": "1.0",
        "run_id": meta["run_id"],
        "input_sha256": meta["input_sha256"],
        "rule_release_version": meta["rule_release_version"],
        "requirement_name": "周年庆宝箱",
        "status": "awaiting_user_confirmation" if source_pending else "clear",
        "items": [
            {
                "boundary_id": "BOUNDARY-0001",
                "module": "来源完整性",
                "question": "独立图片中的状态信息是否已完整纳入需求事实？",
                "recommendation": "完成视觉复核后再确认正式交付；当前保留为待确认草稿。",
                "source_refs": [visual_source_ref],
            }
        ] if source_pending else [],
    }

    base_case = {
        "base_case_id": "BASE-0001",
        "primary_blueprint_node_id": "NODE-0001",
        "blueprint_node_ids": ["NODE-0001"],
        "source_refs": [source_ref],
        "checkpoint": "周年庆宝箱首次开启奖励到账",
        "preconditions": ["周年庆活动已开启", "玩家达到 10 级"],
        "steps": ["进入活动中心", "开启周年庆宝箱"],
        "expected_results": ["宝箱成功开启", "获得 100 金币"],
        "priority": "P0",
    }
    base_case["content_sha256"] = sha256_json(base_case)
    base = {**meta, "cases": [base_case]}
    classification = {
        **meta,
        "provisional_module_ids": [],
        "records": [
            {
                "blueprint_node_id": "NODE-0001",
                "primary_business_category": "activity",
                "business_object_domains": [],
                "horizontal_tags": ["ui_display"],
                "primary_project_module_key": "SAMO::周年庆活动",
                "related_project_module_keys": [],
                "classification_status": "explicit",
                "reason": "源包明确为周年庆活动入口",
                "evidence_refs": [source_ref],
            }
        ],
    }
    candidate = {
        "candidate_case_id": "CAND-0001",
        "semantic_signature": "",
        "source_base_case_ids": ["BASE-0001"],
        "source_rule_ids": ["CZ-RULE-000009"],
        "business_object": "周年庆宝箱",
        "precondition_state": "活动开启且玩家达到 10 级",
        "trigger_action": "首次开启宝箱",
        "target_state": "宝箱已开启",
        "core_expected": "获得 100 金币",
    }
    candidate["semantic_signature"] = candidate_semantic_signature(candidate)
    candidates = {**meta, "cases": [candidate], "base_dispositions": []}
    horizontal = {
        **meta,
        "evaluations": [
            {
                "rule_id": "CZ-RULE-000009",
                "status": "applicable",
                "evidence_refs": [source_ref],
                "reason": "源包存在玩家可见的活动入口和宝箱状态",
                "candidate_case_ids": ["CAND-0001"],
            }
        ],
    }
    project = {**meta, "evaluations": []}
    final_case = {
        "final_case_id": "FINAL-0001",
        "semantic_signature": "",
        "semantic_components": {
            key: candidate[key]
            for key in (
                "business_object",
                "precondition_state",
                "trigger_action",
                "target_state",
                "core_expected",
            )
        },
        "用例编号": "1",
        "一级模块": "周年庆宝箱",
        "二级模块": "开启与奖励",
        "检查点": "周年庆宝箱-首次开启-奖励到账",
        "前置条件": "周年庆活动已开启\n玩家达到 10 级",
        "操作步骤": "1. 进入活动中心\n2. 打开周年庆宝箱\n3. 查看奖励结果",
        "预期结果": "周年庆宝箱成功开启，玩家获得 100 金币。",
        "优先级": "P0",
        "测试结果": "",
        "备注": "",
    }
    final_case["semantic_signature"] = final_semantic_signature(final_case)
    final = {**meta, "cases": [final_case]}
    mapping = {
        **meta,
        "module_catalog_version": "2026-07-24",
        "mappings": [
            {
                "mapping_id": "MAP-0001",
                "decision": "rewritten",
                "candidate_case_ids": ["CAND-0001"],
                "final_case_ids": ["FINAL-0001"],
                "rule_ids": ["CZ-RULE-000009", "CZ-RULE-000025"],
                "reason": "保持业务语义并按个人字段职责改写",
                "evidence_refs": [source_ref],
            }
        ],
    }
    payloads = {
        "source_facts.json": facts,
        "generation_blueprint.json": blueprint,
        "completeness_matrix.json": matrix,
        "pending_boundary_confirmations.json": boundary_confirmations,
        "base_cases.json": base,
        "classification.json": classification,
        "candidate_cases.json": candidates,
        "horizontal_rule_evaluation.json": horizontal,
        "project_rule_evaluation.json": project,
        "final_cases.json": final,
        "case_mapping_ledger.json": mapping,
    }
    for name, payload in payloads.items():
        write_payload(run_dir / name, payload)
    return build_local_readiness(run_dir, MODULES)

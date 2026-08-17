from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

from .contracts import CASE_HEADERS, ARTIFACT_SCHEMA_VERSION, duplicate_values, read_json, sha256_file, write_json
from .stages import validate_run


LOCAL_SCHEMA_VERSION = "1.0"
SOURCE_STATES = {"readable", "partial", "unreadable", "excluded"}
FACT_STATES = {"confirmed", "pending"}
READ_STATES = {"complete", "partial"}
BLUEPRINT_STATES = {"confirmed", "pending"}
MATRIX_STATES = {"covered", "not_applicable", "pending"}
PROJECT_CODES = {"rok", "cod", "beagle", "dobe", "generic", "pending"}
EXPECTED_DIMENSIONS = {f"GR-{index:02d}" for index in range(1, 9)}
INVALID_FILENAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
BOUNDARY_ID_RE = re.compile(r"^BOUNDARY-[0-9]{4}$")
FIXED_COUNT_KEY_RE = re.compile(
    r"^(?:expected|target|required|fixed|desired)_(?:case_)?count$|"
    r"^(?:expected_group_counts|group_quotas?|fixed_group_counts?)$",
    re.IGNORECASE,
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _source_refs_from_packet(packet: dict[str, Any], ledger: dict[str, Any]) -> set[str]:
    refs = {
        str(unit.get("source_ref") or "")
        for file_record in _as_list(packet.get("files"))
        if isinstance(file_record, dict)
        for unit in _as_list(file_record.get("content_units"))
        if isinstance(unit, dict)
    }
    refs.update(
        str(item.get("source_ref") or "")
        for item in _as_list(ledger.get("evidence"))
        if isinstance(item, dict)
    )
    return {item for item in refs if item}


def _validate_refs(values: Iterable[Any], known_refs: set[str], label: str) -> list[str]:
    refs = [str(item or "") for item in values]
    errors: list[str] = []
    if not refs or any(not item for item in refs):
        errors.append(f"{label}.source_refs 必须至少包含一个非空定位")
    unknown = sorted(set(refs) - known_refs)
    if unknown:
        errors.append(f"{label}.source_refs 含未知定位：{', '.join(unknown)}")
    return errors


def _resolve_audit_relative(run_dir: Path, value: str) -> Path | None:
    candidate = (run_dir / Path(value)).resolve()
    try:
        candidate.relative_to(run_dir.resolve())
    except ValueError:
        return None
    return candidate


def validate_source_layer(
    run_dir: Path,
    packet: dict[str, Any],
    ledger: dict[str, Any],
    facts: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    pending_reasons: list[str] = []

    if packet.get("schema_version") != LOCAL_SCHEMA_VERSION:
        errors.append("source_packet.schema_version 必须为 1.0")
    if ledger.get("schema_version") != LOCAL_SCHEMA_VERSION:
        errors.append("source_evidence_ledger.schema_version 必须为 1.0")
    if facts.get("schema_version") != LOCAL_SCHEMA_VERSION:
        errors.append("source_facts.schema_version 必须为 1.0")
    package_hashes = {
        str(packet.get("package_sha256") or ""),
        str(ledger.get("package_sha256") or ""),
        str(facts.get("package_sha256") or ""),
    }
    if len(package_hashes) != 1 or not next(iter(package_hashes), ""):
        errors.append("来源层 package_sha256 不一致")

    files = _as_list(packet.get("files"))
    if not files:
        errors.append("source_packet.files 必须为非空数组")
    source_ids: list[str] = []
    for index, file_record in enumerate(files):
        label = f"source_packet.files[{index}]"
        if not isinstance(file_record, dict):
            errors.append(f"{label} 必须是 object")
            continue
        source_id = str(file_record.get("source_id") or "")
        source_ids.append(source_id)
        status = file_record.get("status")
        if status not in SOURCE_STATES:
            errors.append(f"{label}.status 无效")
        if status in {"partial", "unreadable"}:
            pending_reasons.append(f"{source_id or label} 状态为 {status}")
        source_path = Path(str(file_record.get("path") or ""))
        if not source_path.exists() or not source_path.is_file():
            errors.append(f"{label}.path 当前不可回查：{source_path}")
        else:
            expected_hash = str(file_record.get("sha256") or "")
            if sha256_file(source_path) != expected_hash:
                errors.append(f"{label}.sha256 与当前源文件不一致")
    duplicates = duplicate_values(source_ids)
    if duplicates:
        errors.append("source_id 重复：" + ", ".join(duplicates))

    evidence_ids: list[str] = []
    for index, item in enumerate(_as_list(ledger.get("evidence"))):
        label = f"source_evidence_ledger.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是 object")
            continue
        evidence_id = str(item.get("evidence_id") or "")
        evidence_ids.append(evidence_id)
        if str(item.get("source_id") or "") not in set(source_ids):
            errors.append(f"{label}.source_id 未在源包登记")
        evidence_path = _resolve_audit_relative(run_dir, str(item.get("path") or ""))
        if evidence_path is None:
            errors.append(f"{label}.path 越过 audit 目录")
        elif not evidence_path.exists() or not evidence_path.is_file():
            errors.append(f"{label}.path 不存在：{item.get('path')}")
        elif sha256_file(evidence_path) != str(item.get("sha256") or ""):
            errors.append(f"{label}.sha256 与证据文件不一致")
        if item.get("visual_review_required"):
            if item.get("review_status") != "reviewed":
                pending_reasons.append(f"{evidence_id or label} 尚未完成视觉复核")
            elif not _as_list(item.get("observations")):
                pending_reasons.append(f"{evidence_id or label} 已标记复核但缺少 observations")
    duplicates = duplicate_values(evidence_ids)
    if duplicates:
        errors.append("evidence_id 重复：" + ", ".join(duplicates))

    known_refs = _source_refs_from_packet(packet, ledger)
    facts_list = _as_list(facts.get("facts"))
    fact_ids: list[str] = []
    confirmed_count = 0
    for index, fact in enumerate(facts_list):
        label = f"source_facts.facts[{index}]"
        if not isinstance(fact, dict):
            errors.append(f"{label} 必须是 object")
            continue
        fact_id = str(fact.get("fact_id") or "")
        fact_ids.append(fact_id)
        if not str(fact.get("topic") or "").strip() or not str(fact.get("statement") or "").strip():
            errors.append(f"{label} 缺少 topic 或 statement")
        status = fact.get("status")
        if status not in FACT_STATES:
            errors.append(f"{label}.status 无效")
        elif status == "confirmed":
            confirmed_count += 1
        else:
            pending_reasons.append(f"{fact_id or label} 状态为 pending")
        errors.extend(_validate_refs(_as_list(fact.get("source_refs")), known_refs, label))
    duplicates = duplicate_values(fact_ids)
    if duplicates:
        errors.append("fact_id 重复：" + ", ".join(duplicates))
    if confirmed_count == 0:
        errors.append("没有 confirmed 业务事实，禁止生成工作簿")

    if facts.get("read_status") not in READ_STATES:
        errors.append("source_facts.read_status 必须为 complete 或 partial")
    elif facts.get("read_status") == "partial":
        pending_reasons.append("source_facts.read_status 为 partial")
    if facts.get("project_code") not in PROJECT_CODES:
        errors.append("source_facts.project_code 无效")
    elif facts.get("project_code") == "pending":
        pending_reasons.append("项目分类待确认")
    conflicts = _as_list(facts.get("conflicts"))
    if conflicts:
        pending_reasons.extend(
            f"来源冲突：{str(item.get('topic') or item.get('conflict_id') or index)}"
            for index, item in enumerate(conflicts, 1)
            if isinstance(item, dict)
        )
    pending_items = _as_list(facts.get("pending_items"))
    pending_reasons.extend(f"事实待确认：{str(item)}" for item in pending_items)

    return {
        "status": "invalid" if errors else "ok",
        "errors": errors,
        "warnings": warnings,
        "pending_reasons": pending_reasons,
        "confirmed_fact_count": confirmed_count,
        "known_source_refs": known_refs,
    }


def validate_blueprint_layer(
    blueprint: dict[str, Any],
    matrix: dict[str, Any],
    expected_metadata: dict[str, Any],
    known_refs: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    pending_reasons: list[str] = []
    for label, payload in (("generation_blueprint", blueprint), ("completeness_matrix", matrix)):
        if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            errors.append(f"{label}.schema_version 必须为 {ARTIFACT_SCHEMA_VERSION}")
        for field in ("run_id", "input_sha256", "rule_release_version"):
            if payload.get(field) != expected_metadata.get(field):
                errors.append(f"{label}.{field} 与基础流水线不一致")

    nodes = _as_list(blueprint.get("nodes"))
    if not nodes:
        errors.append("generation_blueprint.nodes 必须为非空数组")
    node_ids: list[str] = []
    confirmed_nodes = 0
    for index, node in enumerate(nodes):
        label = f"generation_blueprint.nodes[{index}]"
        if not isinstance(node, dict):
            errors.append(f"{label} 必须是 object")
            continue
        node_id = str(node.get("blueprint_node_id") or "")
        node_ids.append(node_id)
        for field in ("title", "business_goal", "main_flow", "observable_results"):
            value = node.get(field)
            if value in (None, "", []):
                errors.append(f"{label}.{field} 不能为空")
        status = node.get("status")
        if status not in BLUEPRINT_STATES:
            errors.append(f"{label}.status 无效")
        elif status == "confirmed":
            confirmed_nodes += 1
        else:
            pending_reasons.append(f"{node_id or label} 状态为 pending")
        errors.extend(_validate_refs(_as_list(node.get("source_refs")), known_refs, label))
    duplicates = duplicate_values(node_ids)
    if duplicates:
        errors.append("blueprint_node_id 重复：" + ", ".join(duplicates))
    if confirmed_nodes == 0:
        errors.append("没有 confirmed 蓝图节点，禁止生成工作簿")
    pending_reasons.extend(f"蓝图待确认：{str(item)}" for item in _as_list(blueprint.get("pending_items")))

    dimensions = _as_list(matrix.get("dimensions"))
    dimension_ids: list[str] = []
    for index, dimension in enumerate(dimensions):
        label = f"completeness_matrix.dimensions[{index}]"
        if not isinstance(dimension, dict):
            errors.append(f"{label} 必须是 object")
            continue
        dimension_id = str(dimension.get("dimension_id") or "")
        dimension_ids.append(dimension_id)
        status = dimension.get("status")
        if status not in MATRIX_STATES:
            errors.append(f"{label}.status 无效")
        if status == "pending":
            pending_reasons.append(f"{dimension_id or label} 状态为 pending")
        if status == "not_applicable" and not str(dimension.get("reason") or "").strip():
            errors.append(f"{label}.reason 在 not_applicable 时不能为空")
        refs = _as_list(dimension.get("evidence_refs"))
        if status == "covered":
            errors.extend(_validate_refs(refs, known_refs, label))
        else:
            unknown = sorted(set(str(item or "") for item in refs if item) - known_refs)
            if unknown:
                errors.append(f"{label}.evidence_refs 含未知定位：{', '.join(unknown)}")
    if set(dimension_ids) != EXPECTED_DIMENSIONS or len(dimension_ids) != 8:
        errors.append("completeness_matrix 必须且只能包含 GR-01 至 GR-08")

    return {
        "status": "invalid" if errors else "ok",
        "errors": errors,
        "pending_reasons": pending_reasons,
        "confirmed_blueprint_node_count": confirmed_nodes,
    }


def validate_boundary_confirmations(
    payload: dict[str, Any],
    expected_metadata: dict[str, Any],
    requirement_name: str,
    known_refs: set[str],
    upstream_pending_reasons: list[str],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return {
            "status": "invalid",
            "errors": ["pending_boundary_confirmations 必须是 object"],
            "pending_reasons": [],
            "item_count": 0,
        }
    if payload.get("schema_version") != LOCAL_SCHEMA_VERSION:
        errors.append("pending_boundary_confirmations.schema_version 必须为 1.0")
    for field in ("run_id", "input_sha256", "rule_release_version"):
        if payload.get(field) != expected_metadata.get(field):
            errors.append(f"pending_boundary_confirmations.{field} 与基础流水线不一致")
    if payload.get("requirement_name") != requirement_name:
        errors.append("pending_boundary_confirmations.requirement_name 与需求名称不一致")

    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        errors.append("pending_boundary_confirmations.items 必须为数组")
    items = _as_list(raw_items)
    boundary_ids: list[str] = []
    pending_reasons: list[str] = []
    for index, item in enumerate(items):
        label = f"pending_boundary_confirmations.items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是 object")
            continue
        boundary_id = str(item.get("boundary_id") or "")
        boundary_ids.append(boundary_id)
        if not BOUNDARY_ID_RE.fullmatch(boundary_id):
            errors.append(f"{label}.boundary_id 必须符合 BOUNDARY-0001 格式")
        elif boundary_id != f"BOUNDARY-{index + 1:04d}":
            errors.append(f"{label}.boundary_id 必须按出现顺序连续编号")
        for field in ("module", "question", "recommendation"):
            if not str(item.get(field) or "").strip():
                errors.append(f"{label}.{field} 不能为空")
        errors.extend(_validate_refs(_as_list(item.get("source_refs")), known_refs, label))
        if boundary_id and str(item.get("question") or "").strip():
            pending_reasons.append(
                f"边界待确认：{boundary_id} {str(item.get('module') or '').strip()}："
                f"{str(item.get('question') or '').strip()}"
            )
    duplicates = duplicate_values(boundary_ids)
    if duplicates:
        errors.append("boundary_id 重复：" + ", ".join(duplicates))

    expected_status = "awaiting_user_confirmation" if items else "clear"
    if payload.get("status") != expected_status:
        errors.append(f"pending_boundary_confirmations.status 应为 {expected_status}")
    if upstream_pending_reasons and not items:
        errors.append("存在待确认来源或蓝图事项，但 pending_boundary_confirmations.items 为空")

    return {
        "status": "invalid" if errors else "ok",
        "errors": errors,
        "pending_reasons": pending_reasons,
        "item_count": len(items),
    }


def _walk_fixed_count_fields(value: Any, location: str = "$") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if FIXED_COUNT_KEY_RE.fullmatch(str(key)):
                found.append((child_location, child))
            found.extend(_walk_fixed_count_fields(child, child_location))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_fixed_count_fields(child, f"{location}[{index}]"))
    return found


def validate_traceability_and_count_policy(
    artifacts: dict[str, dict[str, Any]],
    blueprint: dict[str, Any],
    facts: dict[str, Any],
    known_refs: set[str],
) -> dict[str, Any]:
    errors: list[str] = []
    node_ids = {
        str(item.get("blueprint_node_id") or "")
        for item in _as_list(blueprint.get("nodes"))
        if isinstance(item, dict)
    }
    for index, base_case in enumerate(_as_list(artifacts["base"].get("cases"))):
        if not isinstance(base_case, dict):
            continue
        label = f"base_cases.cases[{index}]"
        referenced_nodes = set(str(item or "") for item in _as_list(base_case.get("blueprint_node_ids")))
        referenced_nodes.add(str(base_case.get("primary_blueprint_node_id") or ""))
        unknown_nodes = sorted(referenced_nodes - node_ids)
        if unknown_nodes:
            errors.append(f"{label} 引用了未知蓝图节点：{', '.join(unknown_nodes)}")

    for artifact_name, payload in artifacts.items():
        stack: list[tuple[str, Any]] = [(artifact_name, payload)]
        while stack:
            location, value = stack.pop()
            if isinstance(value, dict):
                for key, child in value.items():
                    child_location = f"{location}.{key}"
                    if key in {"source_refs", "evidence_refs"}:
                        refs = _as_list(child)
                        if not refs:
                            errors.append(f"{child_location} 不能为空")
                        unknown = sorted(set(str(item or "") for item in refs if item) - known_refs)
                        if unknown:
                            errors.append(f"{child_location} 含未知来源定位：{', '.join(unknown)}")
                    else:
                        stack.append((child_location, child))
            elif isinstance(value, list):
                stack.extend((f"{location}[{index}]", child) for index, child in enumerate(value))

    count_policy = facts.get("count_policy") if isinstance(facts.get("count_policy"), dict) else {"mode": "natural"}
    mode = str(count_policy.get("mode") or "natural")
    if mode not in {"natural", "user_fixed", "sample", "condensed"}:
        errors.append("source_facts.count_policy.mode 无效")
    fixed_fields = [
        (artifact_name, location, value)
        for artifact_name, payload in {"source_facts": facts, "generation_blueprint": blueprint, **artifacts}.items()
        for location, value in _walk_fixed_count_fields(payload)
    ]
    if fixed_fields and mode == "natural":
        errors.append("检测到固定或目标数量字段，但 count_policy.mode 仍为 natural")
    if mode in {"user_fixed", "sample", "condensed"} and not str(count_policy.get("request_ref") or "").strip():
        errors.append("固定、抽样或精简数量策略缺少用户要求来源 request_ref")
    if mode == "user_fixed":
        requested = count_policy.get("requested_count")
        if not isinstance(requested, int) or requested < 1:
            errors.append("user_fixed 模式必须提供正整数 requested_count")
        elif requested != len(_as_list(artifacts["final"].get("cases"))):
            errors.append(
                f"用户固定数量与最终用例数不一致：requested={requested} "
                f"actual={len(_as_list(artifacts['final'].get('cases')))}"
            )
    return {"status": "invalid" if errors else "ok", "errors": errors}


def sanitize_requirement_name(value: str) -> str:
    result = INVALID_FILENAME_RE.sub("_", str(value or "")).strip().rstrip(". ")
    result = re.sub(r"\s+", " ", result)
    return result[:80] or "未命名需求"


def build_local_readiness(run_dir: Path, module_snapshot_path: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    packet = read_json(run_dir / "source_packet.json")
    ledger = read_json(run_dir / "source_evidence_ledger.json")
    facts = read_json(run_dir / "source_facts.json")
    blueprint = read_json(run_dir / "generation_blueprint.json")
    matrix = read_json(run_dir / "completeness_matrix.json")
    final = read_json(run_dir / "final_cases.json")
    boundary_file_errors: list[str] = []
    try:
        boundary_confirmations = read_json(run_dir / "pending_boundary_confirmations.json")
    except (OSError, ValueError) as error:
        boundary_confirmations = {}
        boundary_file_errors.append(f"pending_boundary_confirmations.json 不可读取：{error}")
    artifacts = {
        "base": read_json(run_dir / "base_cases.json"),
        "classification": read_json(run_dir / "classification.json"),
        "candidates": read_json(run_dir / "candidate_cases.json"),
        "horizontal": read_json(run_dir / "horizontal_rule_evaluation.json"),
        "project": read_json(run_dir / "project_rule_evaluation.json"),
        "final": final,
        "mapping": read_json(run_dir / "case_mapping_ledger.json"),
    }

    pipeline_report = validate_run(run_dir, module_snapshot_path)
    write_json(run_dir / "pipeline_validation.json", pipeline_report)
    metadata = {
        "run_id": final.get("run_id"),
        "input_sha256": final.get("input_sha256"),
        "rule_release_version": final.get("rule_release_version"),
    }
    source_report = validate_source_layer(run_dir, packet, ledger, facts)
    blueprint_report = validate_blueprint_layer(
        blueprint,
        matrix,
        metadata,
        source_report["known_source_refs"],
    )
    upstream_pending_reasons = [
        *source_report["pending_reasons"],
        *blueprint_report["pending_reasons"],
    ]
    requirement_name = sanitize_requirement_name(str(facts.get("requirement_name") or ""))
    boundary_report = validate_boundary_confirmations(
        boundary_confirmations,
        metadata,
        requirement_name,
        source_report["known_source_refs"],
        upstream_pending_reasons,
    )
    traceability_report = validate_traceability_and_count_policy(
        artifacts,
        blueprint,
        facts,
        source_report["known_source_refs"],
    )
    blocking_errors = [
        *boundary_file_errors,
        *pipeline_report.get("errors", []),
        *source_report["errors"],
        *blueprint_report["errors"],
        *boundary_report["errors"],
        *traceability_report["errors"],
    ]
    if metadata["input_sha256"] != packet.get("package_sha256"):
        blocking_errors.append("基础流水线 input_sha256 与 source_packet.package_sha256 不一致")
    pending_reasons = [
        *upstream_pending_reasons,
        *boundary_report["pending_reasons"],
    ]
    pending_reasons = list(dict.fromkeys(pending_reasons))
    if not str(facts.get("requirement_name") or "").strip():
        blocking_errors.append("source_facts.requirement_name 不能为空")

    if blocking_errors:
        delivery_mode = "blocked"
        output_filename = ""
    elif pending_reasons:
        delivery_mode = "draft"
        output_filename = f"{requirement_name}-测试用例-待确认草稿.xlsx"
    else:
        delivery_mode = "formal"
        output_filename = f"{requirement_name}-测试用例.xlsx"

    payload = {
        "schema_version": LOCAL_SCHEMA_VERSION,
        **metadata,
        "status": "invalid" if blocking_errors else "ok",
        "delivery_mode": delivery_mode,
        "requirement_name": requirement_name,
        "project_code": facts.get("project_code"),
        "output_filename": output_filename,
        "pending_count": len(pending_reasons),
        "boundary_confirmation_count": boundary_report["item_count"],
        "pending_reasons": pending_reasons,
        "blocking_errors": blocking_errors,
        "warnings": source_report["warnings"],
        "counts": {
            "source_files": len(_as_list(packet.get("files"))),
            "confirmed_facts": source_report["confirmed_fact_count"],
            "confirmed_blueprint_nodes": blueprint_report["confirmed_blueprint_node_count"],
            "base_cases": pipeline_report.get("counts", {}).get("base_cases", 0),
            "candidate_cases": pipeline_report.get("counts", {}).get("candidate_cases", 0),
            "final_cases": pipeline_report.get("counts", {}).get("final_cases", 0),
            "boundary_confirmations": boundary_report["item_count"],
        },
        "case_headers": list(CASE_HEADERS),
        "external_links_followed": False,
        "network_required": False,
    }
    write_json(run_dir / "delivery_readiness.json", payload)
    return payload

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .contracts import (
    ARTIFACT_SCHEMA_VERSION,
    BUSINESS_CATEGORIES,
    CLASSIFICATION_STATES,
    EVALUATION_STATES,
    HORIZONTAL_TAGS,
    MAPPING_DECISIONS,
    OBJECT_DOMAINS,
    artifact_report,
    candidate_semantic_signature,
    duplicate_values,
    final_semantic_signature,
    read_json,
    sha256_json,
    validate_common_metadata,
    validate_final_case_row,
    validate_final_module_order,
    validate_same_run,
)
from .rules import published_rules, validate_rule_package


def validate_base_cases(payload: dict[str, Any]) -> dict[str, Any]:
    errors = validate_common_metadata(payload, "base_cases")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("base_cases.cases 必须是非空数组")
        cases = []
    ids: list[str] = []
    for index, case in enumerate(cases):
        label = f"base_cases.cases[{index}]"
        required = ("base_case_id", "primary_blueprint_node_id", "blueprint_node_ids", "source_refs", "checkpoint", "preconditions", "steps", "expected_results", "priority", "content_sha256")
        if not isinstance(case, dict):
            errors.append(f"{label} 必须是 object")
            continue
        for field in required:
            if field not in case:
                errors.append(f"{label} 缺少字段：{field}")
        case_id = str(case.get("base_case_id") or "")
        ids.append(case_id)
        for field in ("base_case_id", "primary_blueprint_node_id", "checkpoint", "priority"):
            if not str(case.get(field) or "").strip():
                errors.append(f"{label}.{field} 不能为空")
        for field in ("blueprint_node_ids", "steps", "expected_results"):
            value = case.get(field)
            if not isinstance(value, list) or not value or any(not str(item).strip() for item in value):
                errors.append(f"{label}.{field} 必须是非空内容数组")
        if case.get("priority") not in {"P0", "P1", "P2"}:
            errors.append(f"{label}.priority 必须是 P0/P1/P2")
        material = {key: value for key, value in case.items() if key != "content_sha256"}
        if case.get("content_sha256") != sha256_json(material):
            errors.append(f"{label}.content_sha256 不一致")
        if not case.get("source_refs"):
            errors.append(f"{label}.source_refs 不能为空")
    duplicates = duplicate_values(ids)
    if duplicates:
        errors.append("base_case_id 重复：" + ", ".join(duplicates))
    return artifact_report("base_cases", errors)


def validate_module_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if payload.get("schema_version") != "1.0" or payload.get("project") != "SAMO":
        errors.append("SAMO 模块快照 schema_version 或 project 无效")
    modules = payload.get("modules")
    if not isinstance(modules, list):
        errors.append("SAMO 模块快照 modules 必须是数组")
        modules = []
    if payload.get("module_count") != len(modules):
        errors.append("SAMO 模块快照 module_count 与实际数量不一致")
    keys: list[str] = []
    for index, item in enumerate(modules):
        if not isinstance(item, dict):
            errors.append(f"SAMO 模块快照 modules[{index}] 必须是 object")
            continue
        key = str(item.get("module_key") or "")
        keys.append(key)
        if not key.startswith("SAMO::") or not item.get("standard_name") or item.get("status") != "active":
            errors.append(f"SAMO 模块快照 modules[{index}] 字段无效")
    if duplicate_values(keys):
        errors.append("SAMO 模块快照 module_key 重复")
    material = {key: value for key, value in payload.items() if key != "content_sha256"}
    if payload.get("content_sha256") != sha256_json(material):
        errors.append("SAMO 模块快照 content_sha256 不一致")
    return artifact_report("samo_project_modules", errors)


def validate_classification(
    payload: dict[str, Any],
    base: dict[str, Any],
    module_snapshot: dict[str, Any],
    provisional_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors = validate_common_metadata(payload, "classification")
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        errors.append("classification.records 必须是非空数组")
        records = []
    base_nodes = {str(case.get("primary_blueprint_node_id") or "") for case in base.get("cases", [])}
    formal_keys = {str(item.get("module_key") or "") for item in module_snapshot.get("modules", [])}
    provisional_snapshot = provisional_snapshot or {"modules": []}
    provisional_ids = {
        str(item.get("provisional_module_id") or "")
        for item in provisional_snapshot.get("modules", [])
        if isinstance(item, dict)
    }
    declared_provisional_ids = set(payload.get("provisional_module_ids") or [])
    unknown_declared = sorted(declared_provisional_ids - provisional_ids)
    if unknown_declared:
        errors.append("分类产物声明了未登记的临时模块：" + ", ".join(unknown_declared))
    seen_nodes: list[str] = []
    for index, record in enumerate(records):
        label = f"classification.records[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{label} 必须是 object")
            continue
        node = str(record.get("blueprint_node_id") or "")
        seen_nodes.append(node)
        category = record.get("primary_business_category")
        if category not in BUSINESS_CATEGORIES:
            errors.append(f"{label}.primary_business_category 无效")
        objects = record.get("business_object_domains")
        if not isinstance(objects, list) or not set(objects).issubset(OBJECT_DOMAINS):
            errors.append(f"{label}.business_object_domains 无效")
        tags = record.get("horizontal_tags")
        if not isinstance(tags, list) or not set(tags).issubset(HORIZONTAL_TAGS):
            errors.append(f"{label}.horizontal_tags 无效")
        state = record.get("classification_status")
        if state not in CLASSIFICATION_STATES:
            errors.append(f"{label}.classification_status 无效")
        if state == "pending":
            errors.append(f"{label} 存在 pending 分类")
        primary_module = str(record.get("primary_project_module_key") or "")
        if primary_module not in formal_keys and primary_module not in provisional_ids:
            errors.append(f"{label}.primary_project_module_key 未命中正式或临时模块")
        related = record.get("related_project_module_keys") or []
        if len(related) != len(set(related)) or primary_module in related:
            errors.append(f"{label}.related_project_module_keys 重复或包含主模块")
        unknown_related = sorted(set(related) - formal_keys - provisional_ids)
        if unknown_related:
            errors.append(f"{label}.related_project_module_keys 未命中正式或临时模块：{', '.join(unknown_related)}")
        if not record.get("reason") or not record.get("evidence_refs"):
            errors.append(f"{label} 必须提供理由和证据")
    if set(seen_nodes) != base_nodes:
        errors.append("分类节点与基线主蓝图节点不一致")
    if duplicate_values(seen_nodes):
        errors.append("蓝图节点被重复分类")
    return artifact_report("classification", errors)


def validate_evaluation(
    payload: dict[str, Any],
    candidate_ids: set[str],
    label: str,
    known_rule_ids: set[str] | None = None,
) -> dict[str, Any]:
    errors = validate_common_metadata(payload, label)
    evaluations = payload.get("evaluations")
    if not isinstance(evaluations, list):
        errors.append(f"{label}.evaluations 必须是数组")
        evaluations = []
    rule_ids: list[str] = []
    for index, item in enumerate(evaluations):
        field = f"{label}.evaluations[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{field} 必须是 object")
            continue
        rule_id = str(item.get("rule_id") or "")
        rule_ids.append(rule_id)
        if known_rule_ids is not None and rule_id not in known_rule_ids:
            errors.append(f"{field}.rule_id 引用了未知或未发布规则：{rule_id}")
        status = item.get("status")
        if status not in EVALUATION_STATES:
            errors.append(f"{field}.status 无效")
        linked = set(item.get("candidate_case_ids") or [])
        if status == "applicable" and (not item.get("evidence_refs") or not linked):
            errors.append(f"{field} 适用规则必须提供证据并映射候选")
        if status == "not_applicable" and (linked or not item.get("reason")):
            errors.append(f"{field} 不适用规则必须无候选且有具体原因")
        if status == "pending":
            errors.append(f"{field} 存在 pending")
        unknown = sorted(linked - candidate_ids)
        if unknown:
            errors.append(f"{field} 引用了未知候选：{', '.join(unknown)}")
    if duplicate_values(rule_ids):
        errors.append(f"{label} 规则被重复评估")
    return artifact_report(label, errors)


def validate_candidates(
    payload: dict[str, Any],
    base: dict[str, Any],
    known_rule_ids: set[str] | None = None,
) -> dict[str, Any]:
    errors = validate_common_metadata(payload, "candidate_cases")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("candidate_cases.cases 必须是非空数组")
        cases = []
    base_ids = {str(item.get("base_case_id") or "") for item in base.get("cases", [])}
    ids: list[str] = []
    signatures: list[str] = []
    for index, case in enumerate(cases):
        label = f"candidate_cases.cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{label} 必须是 object")
            continue
        candidate_id = str(case.get("candidate_case_id") or "")
        signature = str(case.get("semantic_signature") or "")
        ids.append(candidate_id)
        signatures.append(signature)
        if not candidate_id:
            errors.append(f"{label}.candidate_case_id 不能为空")
        if not signature:
            errors.append(f"{label}.semantic_signature 不能为空")
        elif signature != candidate_semantic_signature(case):
            errors.append(f"{label}.semantic_signature 与候选语义内容不一致")
        source_base = set(case.get("source_base_case_ids") or [])
        source_rules = set(case.get("source_rule_ids") or [])
        if not source_base and not source_rules:
            errors.append(f"{label} 缺少基线或规则来源")
        unknown = sorted(source_base - base_ids)
        if unknown:
            errors.append(f"{label} 引用了未知基线：{', '.join(unknown)}")
        unknown_rules = sorted(source_rules - known_rule_ids) if known_rule_ids is not None else []
        if unknown_rules:
            errors.append(f"{label} 引用了未知或未发布规则：{', '.join(unknown_rules)}")
        for required in ("business_object", "precondition_state", "trigger_action", "target_state", "core_expected"):
            if not str(case.get(required) or "").strip():
                errors.append(f"{label}.{required} 不能为空")
    dispositions = payload.get("base_dispositions", [])
    if not isinstance(dispositions, list):
        errors.append("candidate_cases.base_dispositions 必须是数组")
        dispositions = []
    disposed_ids: list[str] = []
    for index, item in enumerate(dispositions):
        label = f"candidate_cases.base_dispositions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} 必须是 object")
            continue
        base_id = str(item.get("base_case_id") or "")
        disposed_ids.append(base_id)
        if base_id not in base_ids:
            errors.append(f"{label}.base_case_id 未知")
        if item.get("status") != "not_applicable" or not str(item.get("reason") or "").strip():
            errors.append(f"{label} 必须明确 not_applicable 并提供原因")
    covered_base_ids = {
        str(base_id)
        for case in cases if isinstance(case, dict)
        for base_id in (case.get("source_base_case_ids") or [])
    }
    if duplicate_values(disposed_ids):
        errors.append("candidate_cases.base_dispositions 的 base_case_id 重复")
    overlap = covered_base_ids & set(disposed_ids)
    if overlap:
        errors.append("基线不能同时生成候选并标记不适用：" + ", ".join(sorted(overlap)))
    uncovered = base_ids - covered_base_ids - set(disposed_ids)
    if uncovered:
        errors.append("基线候选覆盖不完整：" + ", ".join(sorted(uncovered)))
    if duplicate_values(ids):
        errors.append("candidate_case_id 重复")
    if duplicate_values(signatures):
        errors.append("候选语义签名重复，必须先合并或拆分")
    return artifact_report("candidate_cases", errors)


def validate_final_and_ledger(
    final: dict[str, Any], ledger: dict[str, Any], candidates: dict[str, Any],
    known_rule_ids: set[str] | None = None,
) -> dict[str, Any]:
    errors = validate_common_metadata(final, "final_cases") + validate_common_metadata(ledger, "case_mapping_ledger")
    final_cases = final.get("cases")
    mappings = ledger.get("mappings")
    if not isinstance(final_cases, list) or not final_cases:
        errors.append("final_cases.cases 必须是非空数组")
        final_cases = []
    if not isinstance(mappings, list):
        errors.append("case_mapping_ledger.mappings 必须是数组")
        mappings = []
    final_ids: list[str] = []
    signatures: list[str] = []
    for index, case in enumerate(final_cases):
        if not isinstance(case, dict):
            errors.append(f"final_cases.cases[{index}] 必须是 object")
            continue
        final_id = str(case.get("final_case_id") or "")
        signature = str(case.get("semantic_signature") or "")
        final_ids.append(final_id)
        signatures.append(signature)
        if not final_id:
            errors.append(f"final_cases.cases[{index}].final_case_id 不能为空")
        components = case.get("semantic_components")
        if not isinstance(components, dict):
            errors.append(f"final_cases.cases[{index}].semantic_components 必须是 object")
        else:
            for field in (
                "business_object", "precondition_state", "trigger_action",
                "target_state", "core_expected",
            ):
                if not str(components.get(field) or "").strip():
                    errors.append(f"final_cases.cases[{index}].semantic_components.{field} 不能为空")
        if not signature:
            errors.append(f"final_cases.cases[{index}].semantic_signature 不能为空")
        elif signature != final_semantic_signature(case):
            errors.append(f"final_cases.cases[{index}].semantic_signature 与最终用例内容不一致")
        errors.extend(validate_final_case_row(case, index))
    errors.extend(validate_final_module_order(final_cases))
    if duplicate_values(final_ids):
        errors.append("final_case_id 重复")
    if duplicate_values(signatures):
        errors.append("最终用例语义重复")
    candidate_ids = {str(case.get("candidate_case_id") or "") for case in candidates.get("cases", [])}
    candidate_by_id = {
        str(case.get("candidate_case_id") or ""): case
        for case in candidates.get("cases", []) if isinstance(case, dict)
    }
    mapped_candidates: set[str] = set()
    mapped_finals: set[str] = set()
    mapping_ids: list[str] = []
    for index, mapping in enumerate(mappings):
        label = f"case_mapping_ledger.mappings[{index}]"
        if not isinstance(mapping, dict):
            errors.append(f"{label} 必须是 object")
            continue
        decision = mapping.get("decision")
        mapping_id = str(mapping.get("mapping_id") or "")
        mapping_ids.append(mapping_id)
        if not mapping_id:
            errors.append(f"{label}.mapping_id 不能为空")
        source_ids = set(mapping.get("candidate_case_ids") or [])
        target_ids = set(mapping.get("final_case_ids") or [])
        if decision not in MAPPING_DECISIONS:
            errors.append(f"{label}.decision 无效")
        if not source_ids:
            errors.append(f"{label}.candidate_case_ids 不能为空")
        if decision == "not_applicable":
            if target_ids or not mapping.get("reason"):
                errors.append(f"{label} not_applicable 必须无最终用例且有原因")
        elif not target_ids:
            errors.append(f"{label} 必须引用最终用例")
        if decision in {"retained", "rewritten"} and (len(source_ids) != 1 or len(target_ids) != 1):
            errors.append(f"{label} {decision} 必须是 1 对 1")
        if decision == "split" and (len(source_ids) != 1 or len(target_ids) < 2):
            errors.append(f"{label} split 必须是 1 对多且至少两个目标")
        if decision == "merged" and (len(source_ids) < 2 or len(target_ids) != 1):
            errors.append(f"{label} merged 必须是多对 1 且至少两个来源")
        unknown_candidates = source_ids - candidate_ids
        unknown_finals = target_ids - set(final_ids)
        if unknown_candidates:
            errors.append(f"{label} 引用了未知候选")
        if unknown_finals:
            errors.append(f"{label} 引用了未知最终用例")
        ledger_rule_ids = set(mapping.get("rule_ids") or [])
        unknown_rules = sorted(ledger_rule_ids - known_rule_ids) if known_rule_ids is not None else []
        if unknown_rules:
            errors.append(f"{label}.rule_ids 引用了未知或未发布规则：{', '.join(unknown_rules)}")
        source_rule_ids = {
            str(rule_id)
            for candidate_id in source_ids
            for rule_id in candidate_by_id.get(str(candidate_id), {}).get("source_rule_ids", [])
        }
        missing_source_rules = sorted(source_rule_ids - ledger_rule_ids)
        if missing_source_rules:
            errors.append(f"{label}.rule_ids 未覆盖候选触发规则：{', '.join(missing_source_rules)}")
        if not str(mapping.get("reason") or "").strip():
            errors.append(f"{label}.reason 不能为空")
        if not mapping.get("evidence_refs"):
            errors.append(f"{label}.evidence_refs 不能为空")
        mapped_candidates.update(source_ids)
        mapped_finals.update(target_ids)
    if mapped_candidates != candidate_ids:
        errors.append("候选映射覆盖不完整")
    if mapped_finals != set(final_ids):
        errors.append("最终用例存在无来源记录")
    if duplicate_values(mapping_ids):
        errors.append("mapping_id 重复")
    return artifact_report("final_cases_and_mapping", errors)


def validate_run(
    run_dir: Path,
    module_snapshot_path: Path,
    rules_dir: Path | None = None,
    provisional_snapshot_path: Path | None = None,
) -> dict[str, Any]:
    names = {
        "base": "base_cases.json",
        "classification": "classification.json",
        "candidates": "candidate_cases.json",
        "horizontal": "horizontal_rule_evaluation.json",
        "project": "project_rule_evaluation.json",
        "final": "final_cases.json",
        "ledger": "case_mapping_ledger.json",
    }
    artifacts = {key: read_json(run_dir / filename) for key, filename in names.items()}
    module_snapshot = read_json(module_snapshot_path)
    rules_dir = rules_dir or module_snapshot_path.parent / "rules"
    rule_report = validate_rule_package(rules_dir)
    rule_records = published_rules(rules_dir) if rule_report["status"] == "ok" else {}
    known_rule_ids = set(rule_records)
    provisional_snapshot_path = provisional_snapshot_path or module_snapshot_path.with_name("samo-provisional-modules.json")
    provisional_snapshot = read_json(provisional_snapshot_path)
    reports = [
        rule_report,
        validate_module_snapshot(module_snapshot),
        validate_base_cases(artifacts["base"]),
        validate_classification(artifacts["classification"], artifacts["base"], module_snapshot, provisional_snapshot),
        validate_candidates(artifacts["candidates"], artifacts["base"], known_rule_ids),
    ]
    candidate_ids = {str(case.get("candidate_case_id") or "") for case in artifacts["candidates"].get("cases", [])}
    reports.extend([
        validate_evaluation(artifacts["horizontal"], candidate_ids, "horizontal_rule_evaluation", known_rule_ids),
        validate_evaluation(artifacts["project"], candidate_ids, "project_rule_evaluation", known_rule_ids),
        validate_final_and_ledger(artifacts["final"], artifacts["ledger"], artifacts["candidates"], known_rule_ids),
    ])
    consistency_errors = validate_same_run(artifacts)
    manifest_version = rule_report.get("release_version")
    if artifacts["base"].get("rule_release_version") != manifest_version:
        consistency_errors.append(
            f"产物 rule_release_version 与当前发布清单不一致："
            f"artifact={artifacts['base'].get('rule_release_version')!r} manifest={manifest_version!r}"
        )
    candidate_by_id = {
        str(case.get("candidate_case_id") or ""): case
        for case in artifacts["candidates"].get("cases", []) if isinstance(case, dict)
    }
    evaluation_errors: list[str] = []
    applicable_pairs: set[tuple[str, str]] = set()
    evaluated_horizontal_owner_keys: set[str] = set()
    for artifact_key in ("horizontal", "project"):
        for evaluation in artifacts[artifact_key].get("evaluations", []):
            if not isinstance(evaluation, dict):
                continue
            rule_id = str(evaluation.get("rule_id") or "")
            rule = rule_records.get(rule_id, {})
            expected_dimension = "horizontal" if artifact_key == "horizontal" else "project"
            if rule and rule.get("owner_dimension") != expected_dimension:
                evaluation_errors.append(
                    f"{artifact_key}_rule_evaluation 的规则 {rule_id} 属于 "
                    f"{rule.get('owner_dimension')} 层，不能跨层评估"
                )
            if artifact_key == "horizontal" and rule.get("owner_dimension") == "horizontal":
                evaluated_horizontal_owner_keys.add(str(rule.get("owner_key") or ""))
            if evaluation.get("status") == "applicable":
                for candidate_id in evaluation.get("candidate_case_ids") or []:
                    applicable_pairs.add((rule_id, str(candidate_id)))
                    candidate_rules = set(candidate_by_id.get(str(candidate_id), {}).get("source_rule_ids") or [])
                    if rule_id not in candidate_rules:
                        evaluation_errors.append(
                            f"适用规则 {rule_id} 映射候选 {candidate_id}，但候选未声明该规则来源"
                        )
    for candidate_id, case in candidate_by_id.items():
        for rule_id in case.get("source_rule_ids") or []:
            rule = rule_records.get(str(rule_id), {})
            if rule.get("owner_dimension") in {"horizontal", "project"} and (str(rule_id), candidate_id) not in applicable_pairs:
                evaluation_errors.append(
                    f"候选 {candidate_id} 声明规则 {rule_id}，但对应规则未评估为 applicable"
                )
    required_horizontal_keys = {
        str(tag)
        for record in artifacts["classification"].get("records", []) if isinstance(record, dict)
        for tag in (record.get("horizontal_tags") or [])
    }
    missing_evaluations = sorted(required_horizontal_keys - evaluated_horizontal_owner_keys)
    if missing_evaluations:
        evaluation_errors.append("分类横向标签缺少规则评估：" + ", ".join(missing_evaluations))
    errors = consistency_errors + evaluation_errors + [error for report in reports for error in report["errors"]]
    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "run_id": artifacts["base"].get("run_id"),
        "input_sha256": artifacts["base"].get("input_sha256"),
        "rule_release_version": artifacts["base"].get("rule_release_version"),
        "status": "invalid" if errors else "ok",
        "reports": reports,
        "errors": errors,
        "counts": {
            "base_cases": len(artifacts["base"].get("cases", [])),
            "candidate_cases": len(artifacts["candidates"].get("cases", [])),
            "final_cases": len(artifacts["final"].get("cases", [])),
            "pending": sum("pending" in error for error in errors),
        },
    }

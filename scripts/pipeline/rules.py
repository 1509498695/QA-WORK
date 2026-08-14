from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import ContractError, RULE_ID_RE, duplicate_values, read_json, sha256_file, sha256_json, write_json


RULE_SCHEMA_VERSION = "1.0"
RELEASE_RE = re.compile(r"^\d{8}\.\d+$")
OWNER_DIMENSIONS = {"business", "object", "horizontal", "style", "project"}
RULE_STATUSES = {"published"}
PUBLIC_RULE_FIELDS = {
    "rule_id",
    "title",
    "owner_dimension",
    "owner_key",
    "owner_file",
    "rule_revision",
    "status",
    "change_reason",
    "trigger_conditions",
    "required_checks",
    "optional_checks",
    "exclusions",
    "not_applicable_examples",
    "related_rule_ids",
    "project_scope",
    "version_scope",
    "evidence_refs",
    "confirmed_by",
    "confirmed_at",
}


def _iter_formal_rule_files(rules_dir: Path, index: dict[str, Any]) -> list[Path]:
    files: set[str] = set()
    for dimension in ("business", "object", "horizontal", "style", "project"):
        routes = index.get("routes", {}).get(dimension, {})
        if isinstance(routes, dict):
            files.update(str(item) for item in routes.values())
    return [rules_dir / name for name in sorted(files)]


def validate_rule_package(rules_dir: Path, *, staged: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    index = read_json(rules_dir / "rule-index.json", "规则索引")
    manifest_name = "staged-release-manifest.json" if staged else "release-manifest.json"
    manifest = read_json(rules_dir / manifest_name, "规则发布清单")
    if index.get("schema_version") != RULE_SCHEMA_VERSION:
        errors.append("rule-index.schema_version 无效")
    release_version = str(manifest.get("release_version") or "")
    if not RELEASE_RE.fullmatch(release_version):
        errors.append("release_version 必须使用 YYYYMMDD.N")

    required_route_sets = {
        "business": {"system", "commercialization", "growth", "skill", "instance", "gameplay", "activity", "season"},
        "object": {"hero", "boss_monster", "pet_companion", "city_building", "alliance_organization", "map_march"},
        "horizontal": {"ui_display", "interaction_control", "notification_badge", "time_refresh", "state_transition", "data_calculation", "resource_reward", "boundary_limit", "storage_persistence", "sync_consistency", "exception_recovery", "repeat_idempotency", "permission_eligibility", "localization_compatibility", "onboarding_guidance"},
        "style": {"atomicity", "fields", "ordering_priority", "human_language_remarks"},
        "project": {"cod"},
    }
    for dimension, expected in required_route_sets.items():
        actual = set(index.get("routes", {}).get(dimension, {}))
        missing = sorted(expected - actual)
        if missing:
            errors.append(f"规则索引缺少 {dimension} 路由：{', '.join(missing)}")

    all_rules: list[dict[str, Any]] = []
    formal_files = _iter_formal_rule_files(rules_dir, index)
    for path in formal_files:
        try:
            payload = read_json(path, path.name)
        except ContractError as exc:
            errors.append(str(exc))
            continue
        if payload.get("schema_version") != RULE_SCHEMA_VERSION:
            errors.append(f"{path.name}.schema_version 无效")
        rules = payload.get("rules")
        if not isinstance(rules, list):
            errors.append(f"{path.name}.rules 必须是数组")
            continue
        for position, rule in enumerate(rules):
            label = f"{path.name}.rules[{position}]"
            if not isinstance(rule, dict):
                errors.append(f"{label} 必须是 object")
                continue
            missing_fields = sorted(PUBLIC_RULE_FIELDS - set(rule))
            if missing_fields:
                errors.append(f"{label} 缺少字段：{', '.join(missing_fields)}")
                continue
            if not RULE_ID_RE.fullmatch(str(rule.get("rule_id") or "")):
                errors.append(f"{label}.rule_id 无效")
            if rule.get("owner_dimension") not in OWNER_DIMENSIONS:
                errors.append(f"{label}.owner_dimension 无效")
            if rule.get("owner_file") != path.name:
                errors.append(f"{label}.owner_file 与实际文件不一致")
            expected_owner_file = (
                index.get("routes", {})
                .get(str(rule.get("owner_dimension") or ""), {})
                .get(str(rule.get("owner_key") or ""))
            )
            if expected_owner_file != path.name:
                errors.append(f"{label}.owner_key 未路由到实际文件")
            if rule.get("status") not in RULE_STATUSES:
                errors.append(f"{label}.status 必须为 published")
            if not isinstance(rule.get("rule_revision"), int) or rule["rule_revision"] < 1:
                errors.append(f"{label}.rule_revision 必须是正整数")
            if not rule.get("evidence_refs"):
                errors.append(f"{label}.evidence_refs 不能为空")
            all_rules.append(rule)

    ids = [str(rule.get("rule_id") or "") for rule in all_rules]
    duplicates = duplicate_values(ids)
    if duplicates:
        errors.append("规则 ID 重复：" + ", ".join(duplicates))
    known_ids = set(ids)
    semantic_owners: dict[str, str] = {}
    for rule in all_rules:
        unknown = sorted(set(rule.get("related_rule_ids") or []) - known_ids)
        if unknown:
            errors.append(f"{rule['rule_id']} 引用了未知规则：{', '.join(unknown)}")
        conflicts = list(rule.get("conflicts_with") or [])
        extensions = rule.get("extensions") if isinstance(rule.get("extensions"), dict) else {}
        conflicts.extend(extensions.get("conflicts_with") or [])
        if conflicts:
            errors.append(f"{rule['rule_id']} 仍有未解决冲突：{', '.join(map(str, conflicts))}")
        semantic_signature = sha256_json({
            key: rule.get(key)
            for key in (
                "trigger_conditions", "required_checks", "optional_checks", "exclusions",
                "not_applicable_examples", "project_scope", "version_scope",
            )
        })
        previous = semantic_owners.get(semantic_signature)
        if previous:
            errors.append(f"规则语义重复：{previous} 与 {rule['rule_id']}")
        else:
            semantic_owners[semantic_signature] = str(rule["rule_id"])

    expected_hashes = manifest.get("file_sha256")
    if not isinstance(expected_hashes, dict):
        errors.append("发布清单 file_sha256 必须是 object")
        expected_hashes = {}
    paths_for_hash = [rules_dir / "rule-index.json", *formal_files]
    actual_hashes = {path.name: sha256_file(path) for path in paths_for_hash if path.exists()}
    if expected_hashes != actual_hashes:
        errors.append("发布清单文件哈希与当前规则包不一致")
    if manifest.get("rule_count") != len(all_rules):
        errors.append("发布清单 rule_count 与实际规则数不一致")
    if manifest.get("index_sha256") != actual_hashes.get("rule-index.json"):
        errors.append("发布清单 index_sha256 不一致")

    return {
        "status": "invalid" if errors else "ok",
        "schema_version": RULE_SCHEMA_VERSION,
        "release_version": release_version,
        "rule_count": len(all_rules),
        "files": [path.name for path in formal_files],
        "errors": errors,
    }


def published_rule_ids(rules_dir: Path) -> set[str]:
    validation = validate_rule_package(rules_dir)
    if validation["status"] != "ok":
        raise ContractError(
            "当前规则发布包未通过发布清单门禁："
            + json.dumps(validation["errors"], ensure_ascii=False)
        )
    index = read_json(rules_dir / "rule-index.json", "规则索引")
    return {
        str(rule.get("rule_id") or "")
        for path in _iter_formal_rule_files(rules_dir, index)
        for rule in read_json(path, path.name).get("rules", [])
        if isinstance(rule, dict) and rule.get("status") == "published"
    }


def published_rules(rules_dir: Path) -> dict[str, dict[str, Any]]:
    validation = validate_rule_package(rules_dir)
    if validation["status"] != "ok":
        raise ContractError(
            "当前规则发布包未通过发布清单门禁："
            + json.dumps(validation["errors"], ensure_ascii=False)
        )
    index = read_json(rules_dir / "rule-index.json", "规则索引")
    return {
        str(rule["rule_id"]): rule
        for path in _iter_formal_rule_files(rules_dir, index)
        for rule in read_json(path, path.name).get("rules", [])
        if isinstance(rule, dict) and rule.get("status") == "published"
    }


def build_manifest(rules_dir: Path, release_version: str, *, staged: bool = True) -> dict[str, Any]:
    index = read_json(rules_dir / "rule-index.json", "规则索引")
    formal_files = _iter_formal_rule_files(rules_dir, index)
    hashes = {path.name: sha256_file(path) for path in [rules_dir / "rule-index.json", *formal_files]}
    rule_count = sum(len(read_json(path).get("rules", [])) for path in formal_files)
    manifest = {
        "schema_version": RULE_SCHEMA_VERSION,
        "release_version": release_version,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "rule_count": rule_count,
        "index_sha256": hashes["rule-index.json"],
        "file_sha256": hashes,
        "coverage_gaps": [],
    }
    write_json(rules_dir / ("staged-release-manifest.json" if staged else "release-manifest.json"), manifest)
    return manifest


def publish_staged_package(staged_dir: Path, active_dir: Path) -> dict[str, Any]:
    validation = validate_rule_package(staged_dir, staged=True)
    if validation["status"] != "ok":
        raise ContractError("待发布规则包校验失败：" + json.dumps(validation["errors"], ensure_ascii=False))
    active_dir.parent.mkdir(parents=True, exist_ok=True)
    candidate = active_dir.with_name(active_dir.name + ".next")
    backup = active_dir.with_name(active_dir.name + ".previous")
    for sibling in (candidate, backup):
        if sibling.parent.resolve() != active_dir.parent.resolve():
            raise ContractError("规则发布交换目录越过目标父目录")
    if candidate.exists():
        shutil.rmtree(candidate)
    shutil.copytree(staged_dir, candidate)
    staged_manifest = candidate / "staged-release-manifest.json"
    staged_manifest.replace(candidate / "release-manifest.json")
    check = validate_rule_package(candidate)
    if check["status"] != "ok":
        shutil.rmtree(candidate)
        raise ContractError("原子切换前复核失败")
    if backup.exists():
        shutil.rmtree(backup)
    if active_dir.exists():
        active_dir.replace(backup)
    try:
        candidate.replace(active_dir)
    except Exception:
        if backup.exists() and not active_dir.exists():
            backup.replace(active_dir)
        raise
    final_validation = validate_rule_package(active_dir)
    if final_validation["status"] != "ok":
        failed = active_dir.with_name(active_dir.name + ".failed")
        if failed.exists():
            shutil.rmtree(failed)
        active_dir.replace(failed)
        if backup.exists():
            backup.replace(active_dir)
        raise ContractError(
            "原子切换后复核失败，已恢复旧规则包；失败候选保留在："
            + str(failed)
        )
    return final_validation

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


ARTIFACT_SCHEMA_VERSION = "2.0"
BUSINESS_CATEGORIES = {
    "system",
    "commercialization",
    "growth",
    "skill",
    "instance",
    "gameplay",
    "activity",
    "season",
}
OBJECT_DOMAINS = {
    "hero",
    "boss_monster",
    "pet_companion",
    "city_building",
    "alliance_organization",
    "map_march",
}
HORIZONTAL_TAGS = {
    "ui_display",
    "interaction_control",
    "notification_badge",
    "time_refresh",
    "state_transition",
    "data_calculation",
    "resource_reward",
    "boundary_limit",
    "storage_persistence",
    "sync_consistency",
    "exception_recovery",
    "repeat_idempotency",
    "permission_eligibility",
    "localization_compatibility",
    "onboarding_guidance",
}
CLASSIFICATION_STATES = {"explicit", "derived", "pending"}
EVALUATION_STATES = {"applicable", "not_applicable", "pending"}
MAPPING_DECISIONS = {
    "retained",
    "rewritten",
    "split",
    "merged",
    "not_applicable",
}
STAGE_STATES = {"ok", "needs_review", "invalid"}
PRIORITIES = {"P0", "P1", "P2"}
RESULT_CODES = {"", "P", "F", "D", "N/A"}
REMARK_PREFIXES = ("待确认", "环境限制", "跨部门协助", "复测说明")
CASE_HEADERS = (
    "用例编号",
    "一级模块",
    "二级模块",
    "检查点",
    "前置条件",
    "操作步骤",
    "预期结果",
    "优先级",
    "测试结果",
    "备注",
)
REQUIRED_CASE_HEADERS = ("一级模块", "检查点", "操作步骤", "预期结果", "优先级")
RULE_ID_RE = re.compile(r"^CZ-RULE-\d{6}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NUMBERED_STEP_RE = re.compile(r"^(\d+)\.\s+\S")
TECHNICAL_TERM_RE = re.compile(
    r"(?:Code\s*Ask|客户端判定|服务端判定|接口校验|协议验证|状态机|缓存一致性|"
    r"持久化|幂等|回调|落库|服务端下发|[A-Za-z_][A-Za-z0-9_]*(?:Handler|Manager|Server|Client|Key|ID))",
    re.IGNORECASE,
)
CANONICAL_GENERIC_PRIMARY_MODULE = "通用检查"
GENERIC_MODULE_ALIASES = {
    "功能测试",
    "通用模块",
    "通用检查",
    "通用测试",
    "检查通用用例",
    "常规测试",
    "常规测试点",
    "常规测试用例",
}
TECHNICAL_MODULE_RE = re.compile(
    r"^(?:(?:客户端|服务端|服务器|网络)[-_ /]*)?(?:协议|接口)(?:模块|测试|检查|校验|验证)?$",
    re.IGNORECASE,
)
TRACKING_OBSERVATION_RE = re.compile(
    r"(?:数据打点|埋点|数据上报|日志(?:字段|记录|内容|上报)?|参与人数统计口径|"
    r"记录(?:包含|标明|能够还原|中的玩家选择|对应计票原因)|产生变更记录)"
)


class ContractError(ValueError):
    """Raised when a deterministic pipeline contract is violated."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def candidate_semantic_signature(case: dict[str, Any]) -> str:
    return sha256_json(
        {
            key: case.get(key)
            for key in (
                "business_object",
                "precondition_state",
                "trigger_action",
                "target_state",
                "core_expected",
            )
        }
    )


def final_semantic_signature(case: dict[str, Any]) -> str:
    components = case.get("semantic_components")
    if not isinstance(components, dict):
        components = {}
    return sha256_json({
        key: components.get(key)
        for key in (
            "business_object", "precondition_state", "trigger_action",
            "target_state", "core_expected",
        )
    })


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path, label: str | None = None) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"{label or path.name}不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"{label or path.name}不是有效 JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ContractError(f"{label or path.name}顶层必须是 object")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.replace(path)


def require_fields(payload: dict[str, Any], fields: Iterable[str], label: str) -> list[str]:
    return [f"{label}缺少字段：{field}" for field in fields if field not in payload]


def duplicate_values(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def validate_common_metadata(payload: dict[str, Any], label: str) -> list[str]:
    errors = require_fields(
        payload,
        ("schema_version", "run_id", "input_sha256", "rule_release_version"),
        label,
    )
    if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        errors.append(f"{label}.schema_version 必须为 {ARTIFACT_SCHEMA_VERSION}")
    if not str(payload.get("run_id") or "").strip():
        errors.append(f"{label}.run_id 不能为空")
    digest = str(payload.get("input_sha256") or "")
    if not SHA256_RE.fullmatch(digest):
        errors.append(f"{label}.input_sha256 必须是小写 SHA-256")
    if not str(payload.get("rule_release_version") or "").strip():
        errors.append(f"{label}.rule_release_version 不能为空")
    return errors


def validate_same_run(artifacts: dict[str, dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for field in ("run_id", "input_sha256", "rule_release_version"):
        values = {str(payload.get(field) or "") for payload in artifacts.values()}
        if len(values) != 1:
            details = ", ".join(f"{name}={payload.get(field)!r}" for name, payload in artifacts.items())
            errors.append(f"跨产物 {field} 不一致：{details}")
    return errors


def artifact_report(label: str, errors: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    warnings = warnings or []
    return {
        "artifact": label,
        "status": "invalid" if errors else "ok",
        "errors": errors,
        "warnings": warnings,
    }


def validate_case_steps(value: str, label: str) -> list[str]:
    lines = [line.strip() for line in str(value or "").replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines:
        return [f"{label}不能为空"]
    numbers: list[int] = []
    for line in lines:
        match = NUMBERED_STEP_RE.match(line)
        if not match:
            return [f"{label}必须逐行使用 `1. 动作` 连续编号"]
        numbers.append(int(match.group(1)))
    if numbers != list(range(1, len(lines) + 1)):
        return [f"{label}编号必须从 1 连续递增"]
    return []


def validate_remark(value: str, label: str) -> list[str]:
    errors: list[str] = []
    for line in [item.strip() for item in str(value or "").splitlines() if item.strip()]:
        if not line.startswith(REMARK_PREFIXES):
            errors.append(f"{label}只能使用备注白名单前缀")
        if TECHNICAL_TERM_RE.search(line) or re.search(r"来源\s*[:：]|代码\s*[:：]|风险\s*[:：]", line):
            errors.append(f"{label}不得包含来源、代码判定或风险分析")
    return errors


def validate_case_module_names(case: dict[str, Any], label: str) -> list[str]:
    errors: list[str] = []
    level1 = re.sub(r"\s+", "", str(case.get("一级模块") or ""))
    level2 = re.sub(r"\s+", "", str(case.get("二级模块") or ""))
    for field, value in (("一级模块", level1), ("二级模块", level2)):
        if value and TECHNICAL_MODULE_RE.fullmatch(value):
            errors.append(
                f"{label}.{field} 不得使用协议或接口等技术模块名；"
                "改写为玩家可执行且结果可观察的业务检查"
            )
    if level1 in GENERIC_MODULE_ALIASES and level1 != CANONICAL_GENERIC_PRIMARY_MODULE:
        errors.append(
            f"{label}.一级模块 的历史常规分组必须统一为 `{CANONICAL_GENERIC_PRIMARY_MODULE}`，"
            f"不得使用：{level1}"
        )
    if level2 in GENERIC_MODULE_ALIASES:
        errors.append(
            f"{label}.二级模块 不得使用通用分组；通用用例必须归入独立一级模块 "
            f"`{CANONICAL_GENERIC_PRIMARY_MODULE}`，不得使用：{level2}"
        )
    if level1 == CANONICAL_GENERIC_PRIMARY_MODULE and not level2:
        errors.append(
            f"{label}.二级模块 不能为空；`{CANONICAL_GENERIC_PRIMARY_MODULE}` 下必须使用实际检查分类"
        )
    module_text = f"{level1} {level2}"
    observation_text = " ".join(
        str(case.get(field) or "")
        for field in ("检查点", "操作步骤", "预期结果")
    )
    if "数据与协议" in module_text or TRACKING_OBSERVATION_RE.search(observation_text):
        errors.append(
            f"{label} 以埋点、日志、上报或后台记录为主要观察对象；"
            "普通业务用例必须排除该技术观测目标，只保留玩家可观察的界面、背包、邮件、排行、历史或重登状态"
        )
    return errors


def validate_final_module_order(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    generic_positions = [
        index
        for index, case in enumerate(cases)
        if re.sub(r"\s+", "", str(case.get("一级模块") or ""))
        == CANONICAL_GENERIC_PRIMARY_MODULE
    ]
    if generic_positions:
        expected = list(range(generic_positions[0], len(cases)))
        if generic_positions != expected:
            errors.append(
                "final_cases.cases 的 `通用检查` 必须作为全部业务用例之后连续排列的独立一级模块"
            )
    return errors


def validate_final_case_row(case: dict[str, Any], index: int) -> list[str]:
    label = f"final_cases.cases[{index}]"
    errors = require_fields(case, ("final_case_id", *CASE_HEADERS), label)
    for field in REQUIRED_CASE_HEADERS:
        if not str(case.get(field) or "").strip():
            errors.append(f"{label}.{field} 不能为空")
    errors.extend(validate_case_module_names(case, label))
    if str(case.get("用例编号") or "") != str(index + 1):
        errors.append(f"{label}.用例编号 必须按最终顺序连续编号")
    if case.get("优先级") not in PRIORITIES:
        errors.append(f"{label}.优先级 必须是 P0/P1/P2")
    if str(case.get("测试结果") or "") not in RESULT_CODES:
        errors.append(f"{label}.测试结果 无效")
    precondition = str(case.get("前置条件") or "")
    if any(token in precondition for token in ("(", ")", "（", "）")):
        errors.append(f"{label}.前置条件 不得包含括号解释")
    if re.search(r"(?:^|\n)\s*(?:准备|确保|确认)(?:账号|环境|数据|状态|.*已生效)", precondition):
        errors.append(f"{label}.前置条件 不得写准备或确认过程")
    if re.search(r"接上一条|继续上一步结果|接上条用例", precondition):
        errors.append(f"{label}.前置条件 不得引用其他用例")
    errors.extend(validate_case_steps(str(case.get("操作步骤") or ""), f"{label}.操作步骤"))
    errors.extend(validate_remark(str(case.get("备注") or ""), f"{label}.备注"))
    if str(case.get("备注") or "").lstrip().startswith("待确认"):
        errors.append(f"{label}.备注 含待确认，正式最终用例必须阻断")
    vague_pattern = re.compile(r"功能正常|界面检查|显示正常|操作成功|与配置一致|符合配置")
    for field in ("检查点", "操作步骤", "预期结果"):
        if vague_pattern.search(str(case.get(field) or "")):
            errors.append(f"{label}.{field} 包含不可判定的空泛表达")
    for field in CASE_HEADERS[1:]:
        value = str(case.get(field) or "")
        if TECHNICAL_TERM_RE.search(value):
            errors.append(f"{label}.{field} 包含程序术语：{TECHNICAL_TERM_RE.search(value).group(0)}")
    return errors

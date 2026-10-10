from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlparse


SCHEMA_VERSION = "workspace-feishu/sheet-delivery/v1"
TEMPLATE_LAYOUT_VERSION = "qa-case-template/v1"
BODY_START_INDEX = 10
BODY_START_ROW = BODY_START_INDEX + 1
MAX_ROWS = 5_000
MAX_COLUMNS = 100
MAX_CELLS = 200_000
MAX_TEXT_CHARACTERS = 40_000
DEFAULT_TEMPLATE_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "references" / "default-template.json"
)
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
PRIORITIES = {"P0", "P1", "P2"}
RESULT_CODES = {"", "P", "F", "D", "N/A"}
REMARK_PREFIXES = ("待确认", "环境限制", "跨部门协助", "复测说明")
STEP_RE = re.compile(r"^(\d+)\.\s+\S")


class SpecBuildError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def spec_sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise SpecBuildError(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SpecBuildError(f"JSON 无效：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SpecBuildError(f"顶层必须是 object：{path}")
    return value


def write_object(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        stream.write(content)
        temporary = Path(stream.name)
    temporary.replace(path)


def _text(value: Any, label: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        raise SpecBuildError(f"{label} 必须是标量")
    text = str(value)
    if len(text) > MAX_TEXT_CHARACTERS:
        raise SpecBuildError(f"{label} 超过 {MAX_TEXT_CHARACTERS} 字符")
    return text


def _validate_steps(value: str, label: str) -> None:
    lines = [line.strip() for line in value.replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines:
        raise SpecBuildError(f"{label} 不能为空")
    numbers: list[int] = []
    for line in lines:
        match = STEP_RE.match(line)
        if not match:
            raise SpecBuildError(f"{label} 每行必须使用 `数字. 动作`：{line}")
        numbers.append(int(match.group(1)))
    if numbers != list(range(1, len(numbers) + 1)):
        raise SpecBuildError(f"{label} 编号必须从 1 连续递增")


def _https_url(value: str, label: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise SpecBuildError(f"{label} 必须是完整 HTTPS URL")
    if len(value) > 2_000:
        raise SpecBuildError(f"{label} 超过 2000 字符")
    return value


def load_default_template() -> dict[str, Any]:
    config = read_object(DEFAULT_TEMPLATE_CONFIG_PATH)
    if config.get("schema_version") != "qa-case-xlsx-unified/default-template/v1":
        raise SpecBuildError("默认模板配置 schema_version 无效")
    if config.get("role") != "read_only_layout_reference":
        raise SpecBuildError("默认模板必须是只读版式参考")
    locator = config.get("locator")
    if not isinstance(locator, str):
        raise SpecBuildError("默认模板配置缺少 locator")
    _https_url(locator, "默认模板 locator")
    layout = config.get("layout_contract")
    if not isinstance(layout, dict):
        raise SpecBuildError("默认模板配置缺少 layout_contract")
    if layout.get("layout_version") != TEMPLATE_LAYOUT_VERSION:
        raise SpecBuildError("默认模板 layout_version 无效")
    if layout.get("column_count") != len(CASE_HEADERS):
        raise SpecBuildError("默认模板列数与构建器不一致")
    if layout.get("case_body_start_row") != BODY_START_ROW:
        raise SpecBuildError("默认模板用例起始行与构建器不一致")
    if layout.get("frozen_row_count") != BODY_START_INDEX:
        raise SpecBuildError("默认模板冻结行数与构建器不一致")
    if tuple(layout.get("case_columns", ())) != CASE_HEADERS:
        raise SpecBuildError("默认模板 A:J 字段与构建器不一致")
    return config


def case_rows(final_cases: dict[str, Any]) -> list[list[str]]:
    cases = final_cases.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SpecBuildError("final_cases.cases 必须是非空数组")
    if len(cases) + BODY_START_INDEX > MAX_ROWS:
        raise SpecBuildError(f"交付行数超过 {MAX_ROWS}")

    rows: list[list[str]] = []
    for index, item in enumerate(cases, start=1):
        if not isinstance(item, dict):
            raise SpecBuildError(f"final_cases.cases[{index - 1}] 必须是 object")
        missing = [field for field in CASE_HEADERS if field not in item]
        if missing:
            raise SpecBuildError(
                f"final_cases.cases[{index - 1}] 缺少字段：{', '.join(missing)}"
            )
        number = _text(item["用例编号"], f"用例[{index}].用例编号")
        if number != str(index):
            raise SpecBuildError(f"用例[{index}].用例编号 必须为 {index}")
        row = [_text(item[field], f"用例[{index}].{field}") for field in CASE_HEADERS]
        for field_index in (1, 3, 5, 6, 7):
            if not row[field_index].strip():
                raise SpecBuildError(f"用例[{index}].{CASE_HEADERS[field_index]} 不能为空")
        if row[7] not in PRIORITIES:
            raise SpecBuildError(f"用例[{index}].优先级 必须是 P0/P1/P2")
        if row[8] not in RESULT_CODES:
            raise SpecBuildError(f"用例[{index}].测试结果 无效")
        if row[9] and not row[9].startswith(REMARK_PREFIXES):
            raise SpecBuildError(f"用例[{index}].备注 前缀无效")
        _validate_steps(row[5], f"用例[{index}].操作步骤")
        rows.append(row)
    return rows


def _merge(
    row_start: int,
    row_end: int,
    column_start: int,
    column_end: int,
) -> dict[str, int]:
    return {
        "row_start": row_start,
        "row_end": row_end,
        "column_start": column_start,
        "column_end": column_end,
    }


def _groups(
    rows: Sequence[Sequence[str]],
    field_index: int,
    parent_index: int | None = None,
) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(rows) + 1):
        boundary = index == len(rows)
        if not boundary:
            boundary = rows[index][field_index] != rows[start][field_index]
            if parent_index is not None:
                boundary = boundary or rows[index][parent_index] != rows[start][parent_index]
        if boundary:
            groups.append((start, index - 1))
            start = index
    return groups


def _module_merges(
    values: list[list[Any]],
    rows: Sequence[Sequence[str]],
) -> list[dict[str, int]]:
    merges: list[dict[str, int]] = []
    for field_index, parent_index in ((1, None), (2, 1)):
        for start, end in _groups(rows, field_index, parent_index):
            if end <= start:
                continue
            merges.append(
                _merge(
                    BODY_START_INDEX + start,
                    BODY_START_INDEX + end + 1,
                    field_index,
                    field_index + 1,
                )
            )
            for row_index in range(start + 1, end + 1):
                values[BODY_START_INDEX + row_index][field_index] = None
    return merges


def _static_merges() -> list[dict[str, int]]:
    return [
        _merge(0, 1, 0, 10),
        _merge(1, 5, 2, 3),
        _merge(1, 5, 3, 7),
        _merge(1, 2, 8, 10),
        _merge(2, 3, 8, 10),
        _merge(3, 4, 8, 10),
        _merge(4, 5, 8, 10),
        _merge(5, 6, 8, 10),
        _merge(6, 7, 8, 10),
        _merge(7, 8, 8, 10),
        _merge(5, 8, 2, 3),
        _merge(5, 8, 3, 7),
        _merge(8, 10, 0, 1),
        _merge(8, 9, 1, 4),
        _merge(8, 10, 4, 5),
        _merge(8, 10, 5, 6),
        _merge(8, 10, 6, 7),
        _merge(8, 10, 7, 8),
        _merge(8, 10, 8, 9),
        _merge(8, 10, 9, 10),
    ]


def _reference_text(source_urls: Sequence[str] | None, template_url: str) -> str:
    normalized_sources: list[str] = []
    for index, source_url in enumerate(source_urls or (), start=1):
        normalized = _https_url(source_url, f"source_url[{index}]")
        if normalized not in normalized_sources:
            normalized_sources.append(normalized)
    lines = [
        f"策划来源 {index}: {source_url}"
        for index, source_url in enumerate(normalized_sources, start=1)
    ]
    lines.append(f"用例模板: {_https_url(template_url, 'template_url')}")
    reference = "\n".join(lines)
    if len(reference) > MAX_TEXT_CHARACTERS:
        raise SpecBuildError(f"参考文档文本超过 {MAX_TEXT_CHARACTERS} 字符")
    return reference


def _style_ranges(row_count: int) -> list[dict[str, Any]]:
    return [
        {
            "range": _merge(0, 1, 0, 10),
            "style": {
                "bold": True,
                "font_size_pt": 18,
                "text_color": "#000000",
                "fill_color": "#7EDAFB",
                "horizontal_alignment": "center",
                "vertical_alignment": "middle",
            },
        },
        {
            "range": _merge(1, 8, 0, 1),
            "style": {
                "bold": True,
                "font_size_pt": 14,
                "text_color": "#000000",
                "fill_color": "#7EDAFB",
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(1, 2, 1, 2),
            "style": {
                "bold": True,
                "font_size_pt": 16,
                "fill_color": "#FFC60A",
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(2, 8, 1, 2),
            "style": {
                "bold": True,
                "font_size_pt": 16,
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(1, 8, 2, 3),
            "style": {
                "bold": True,
                "font_size_pt": 14,
                "fill_color": "#7EDAFB",
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(1, 5, 3, 7),
            "style": {
                "bold": True,
                "font_size_pt": 16,
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(5, 8, 3, 7),
            "style": {
                "bold": True,
                "font_size_pt": 11,
                "text_color": "#0000FF",
                "underline": True,
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(1, 2, 7, 10),
            "style": {
                "bold": True,
                "font_size_pt": 14,
                "horizontal_alignment": "center",
            },
        },
        {
            "range": _merge(2, 3, 7, 8),
            "style": {"fill_color": "#34C724", "horizontal_alignment": "center"},
        },
        {
            "range": _merge(3, 4, 7, 8),
            "style": {"fill_color": "#F54A45", "horizontal_alignment": "center"},
        },
        {
            "range": _merge(4, 5, 7, 8),
            "style": {"fill_color": "#AD82F7", "horizontal_alignment": "center"},
        },
        {
            "range": _merge(5, 6, 7, 8),
            "style": {"fill_color": "#8F959E", "horizontal_alignment": "center"},
        },
        {
            "range": _merge(6, 8, 7, 8),
            "style": {"bold": True, "horizontal_alignment": "center"},
        },
        {
            "range": _merge(2, 8, 8, 10),
            "style": {"bold": True, "horizontal_alignment": "center"},
        },
        {
            "range": _merge(8, 10, 0, 10),
            "style": {
                "bold": True,
                "font_size_pt": 14,
                "text_color": "#000000",
                "fill_color": "#7EDAFB",
                "horizontal_alignment": "center",
                "vertical_alignment": "middle",
            },
        },
        {
            "range": _merge(BODY_START_INDEX, row_count, 0, 1),
            "style": {"horizontal_alignment": "center"},
        },
        {
            "range": _merge(BODY_START_INDEX, row_count, 1, 3),
            "style": {"horizontal_alignment": "center"},
        },
        {
            "range": _merge(BODY_START_INDEX, row_count, 7, 9),
            "style": {"horizontal_alignment": "center"},
        },
    ]


def validate_spec_semantics(spec: dict[str, Any], rows: Sequence[Sequence[str]]) -> None:
    row_count = BODY_START_INDEX + len(rows)
    if spec.get("row_count") != row_count or spec.get("column_count") != len(CASE_HEADERS):
        raise SpecBuildError("模板规范尺寸与用例不一致")
    values = spec.get("values")
    if not isinstance(values, list) or len(values) != row_count:
        raise SpecBuildError("模板规范 values 行数无效")
    if any(not isinstance(row, list) or len(row) != len(CASE_HEADERS) for row in values):
        raise SpecBuildError("模板规范 values 必须是完整 A:J 矩形")

    last_row = BODY_START_INDEX + len(rows)
    expected_formulas = {
        (1, 3): f"=COUNTA($A${BODY_START_ROW}:$A${last_row})",
        (2, 8): f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"P")',
        (3, 8): f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"F")',
        (4, 8): f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"D")',
        (5, 8): f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"N/A")',
        (6, 8): "=IF($D$2=0,0,SUM($I$3:$I$6)/$D$2)",
        (7, 8): "=IF($D$2=0,0,$I$3/$D$2)",
    }
    for (row_index, column_index), formula in expected_formulas.items():
        if values[row_index][column_index] != {"formula": formula}:
            raise SpecBuildError("模板统计公式与动态用例范围不一致")

    primary_value: str | None = None
    secondary_value: str | None = None
    for offset, expected in enumerate(rows):
        actual = values[BODY_START_INDEX + offset]
        if actual[0] != {"formula": "=ROW()-10"}:
            raise SpecBuildError(f"用例[{offset + 1}] 编号公式无效")
        if actual[1] is not None:
            primary_value = actual[1]
        if actual[2] is not None:
            secondary_value = actual[2]
        expanded = [str(offset + 1), primary_value, secondary_value, *actual[3:]]
        if expanded != list(expected):
            raise SpecBuildError(f"用例[{offset + 1}] 模块合并后语义不一致")


def build_spec(
    final_cases: dict[str, Any],
    title: str,
    source_urls: Sequence[str] | None = None,
    template_url: str | None = None,
) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise SpecBuildError("title 不能为空")
    if len(title) > 200:
        raise SpecBuildError("title 超过 200 字符")

    template = load_default_template()
    effective_template_url = template_url or str(template["locator"])
    rows = case_rows(final_cases)
    column_count = len(CASE_HEADERS)
    row_count = len(rows) + BODY_START_INDEX
    if column_count > MAX_COLUMNS or row_count * column_count > MAX_CELLS:
        raise SpecBuildError("交付矩形超过 Provider 安全上限")

    last_row = BODY_START_INDEX + len(rows)
    values: list[list[Any]] = [[None for _ in range(column_count)] for _ in range(row_count)]
    values[0][0] = title
    values[1][0] = "DRI"
    values[1][2] = "用例总数"
    values[1][3] = {"formula": f"=COUNTA($A${BODY_START_ROW}:$A${last_row})"}
    values[1][7] = "测试结果类型"
    values[1][8] = "结果统计"

    role_labels = ("客户端", "服务器", "UI/UE", "系统/运营/战斗策划", "文案/叙事", "数值")
    result_labels = (
        "测试通过（P）",
        "未通过（F）",
        "删除用例（D）",
        "无需测试（N/A）",
        "执行率",
        "通过率",
    )
    result_formulas = (
        f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"P")',
        f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"F")',
        f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"D")',
        f'=COUNTIF($I${BODY_START_ROW}:$I${last_row},"N/A")',
        "=IF($D$2=0,0,SUM($I$3:$I$6)/$D$2)",
        "=IF($D$2=0,0,$I$3/$D$2)",
    )
    for offset, (role, label, formula) in enumerate(
        zip(role_labels, result_labels, result_formulas),
        start=2,
    ):
        values[offset][0] = role
        values[offset][7] = label
        values[offset][8] = {"formula": formula}

    values[5][2] = "SVN路径或者参考文档"
    values[5][3] = _reference_text(source_urls, effective_template_url)
    values[8] = [
        "用例编号",
        "测试标题",
        None,
        None,
        "前置条件",
        "操作步骤（必要项）",
        "预期结果（必要项）",
        "优先级",
        "测试结果",
        "备注",
    ]
    values[9] = [
        None,
        "一级模块",
        "二级模块",
        "检查点（必要项）",
        None,
        None,
        None,
        None,
        None,
        None,
    ]

    for offset, row in enumerate(rows):
        values[BODY_START_INDEX + offset] = [
            {"formula": "=ROW()-10"},
            *row[1:],
        ]

    module_merges = _module_merges(values, rows)
    spec = {
        "schema_version": SCHEMA_VERSION,
        "row_count": row_count,
        "column_count": column_count,
        "values": values,
        "base_style": {
            "font_size_pt": 11,
            "text_color": "#1F2329",
            "fill_color": "#FFFFFF",
            "horizontal_alignment": "left",
            "vertical_alignment": "middle",
            "border_type": "full",
            "border_color": "#1F2329",
        },
        "style_ranges": _style_ranges(row_count),
        "default_row_height_px": 64,
        "row_heights": [
            {"start_index": 0, "end_index": 1, "pixel_size": 50},
            {"start_index": 1, "end_index": 8, "pixel_size": 30},
            {"start_index": 8, "end_index": 10, "pixel_size": 34},
        ],
        "default_column_width_px": 140,
        "column_widths": [
            {"start_index": 0, "end_index": 1, "pixel_size": 105},
            {"start_index": 1, "end_index": 2, "pixel_size": 140},
            {"start_index": 2, "end_index": 3, "pixel_size": 150},
            {"start_index": 3, "end_index": 4, "pixel_size": 240},
            {"start_index": 4, "end_index": 5, "pixel_size": 220},
            {"start_index": 5, "end_index": 6, "pixel_size": 320},
            {"start_index": 6, "end_index": 7, "pixel_size": 320},
            {"start_index": 7, "end_index": 8, "pixel_size": 107},
            {"start_index": 8, "end_index": 9, "pixel_size": 105},
            {"start_index": 9, "end_index": 10, "pixel_size": 160},
        ],
        "frozen_row_count": BODY_START_INDEX,
        "frozen_column_count": 0,
        "merges": _static_merges() + module_merges,
    }
    validate_spec_semantics(spec, rows)
    return spec


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify a template-based Workspace Feishu A:J test-case Sheet spec."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--final-cases", type=Path, required=True)
    build.add_argument("--title", required=True)
    build.add_argument("--source-url", action="append")
    build.add_argument("--template-url")
    build.add_argument("--out", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--final-cases", type=Path, required=True)
    verify.add_argument("--title", required=True)
    verify.add_argument("--source-url", action="append")
    verify.add_argument("--template-url")
    verify.add_argument("--spec", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        final_cases = read_object(args.final_cases)
        expected = build_spec(
            final_cases,
            args.title,
            source_urls=args.source_url,
            template_url=args.template_url,
        )
        template_locator = args.template_url or str(load_default_template()["locator"])
        if args.command == "build":
            write_object(args.out, expected)
            result = {
                "status": "ok",
                "spec_path": str(args.out.resolve()),
                "spec_sha256": spec_sha256(expected),
                "case_count": len(expected["values"]) - BODY_START_INDEX,
                "row_count": expected["row_count"],
                "column_count": expected["column_count"],
                "template_layout_version": TEMPLATE_LAYOUT_VERSION,
                "template_locator": template_locator,
            }
        else:
            actual = read_object(args.spec)
            if canonical_bytes(actual) != canonical_bytes(expected):
                raise SpecBuildError(
                    "sheet spec 与 final_cases/title/source/template 的确定性结果不一致"
                )
            result = {
                "status": "ok",
                "spec_sha256": spec_sha256(actual),
                "case_count": len(actual["values"]) - BODY_START_INDEX,
                "row_count": actual["row_count"],
                "column_count": actual["column_count"],
                "template_layout_version": TEMPLATE_LAYOUT_VERSION,
                "template_locator": template_locator,
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except SpecBuildError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

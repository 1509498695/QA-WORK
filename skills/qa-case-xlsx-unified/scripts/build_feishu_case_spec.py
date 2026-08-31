from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "workspace-feishu/sheet-delivery/v1"
MAX_ROWS = 5_000
MAX_COLUMNS = 100
MAX_CELLS = 200_000
MAX_TEXT_CHARACTERS = 40_000
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
        value = json.loads(path.read_text(encoding="utf-8"))
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


def case_rows(final_cases: dict[str, Any]) -> list[list[Any]]:
    cases = final_cases.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SpecBuildError("final_cases.cases 必须是非空数组")
    if len(cases) + 2 > MAX_ROWS:
        raise SpecBuildError(f"交付行数超过 {MAX_ROWS}")

    rows: list[list[Any]] = []
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
        row[0] = index
        rows.append(row)
    return rows


def build_spec(final_cases: dict[str, Any], title: str) -> dict[str, Any]:
    title = title.strip()
    if not title:
        raise SpecBuildError("title 不能为空")
    if len(title) > 200:
        raise SpecBuildError("title 超过 200 字符")

    rows = case_rows(final_cases)
    column_count = len(CASE_HEADERS)
    row_count = len(rows) + 2
    if column_count > MAX_COLUMNS or row_count * column_count > MAX_CELLS:
        raise SpecBuildError("交付矩形超过 Provider 安全上限")

    values: list[list[Any]] = [
        [title, *([None] * (column_count - 1))],
        list(CASE_HEADERS),
        *rows,
    ]
    return {
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
            "border_color": "#D0D3D6",
        },
        "style_ranges": [
            {
                "range": {
                    "row_start": 0,
                    "row_end": 1,
                    "column_start": 0,
                    "column_end": column_count,
                },
                "style": {
                    "bold": True,
                    "font_size_pt": 14,
                    "text_color": "#FFFFFF",
                    "fill_color": "#3370FF",
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                },
            },
            {
                "range": {
                    "row_start": 1,
                    "row_end": 2,
                    "column_start": 0,
                    "column_end": column_count,
                },
                "style": {
                    "bold": True,
                    "text_color": "#FFFFFF",
                    "fill_color": "#245BDB",
                    "horizontal_alignment": "center",
                    "vertical_alignment": "middle",
                },
            },
            {
                "range": {
                    "row_start": 2,
                    "row_end": row_count,
                    "column_start": 0,
                    "column_end": 1,
                },
                "style": {"horizontal_alignment": "center"},
            },
            {
                "range": {
                    "row_start": 2,
                    "row_end": row_count,
                    "column_start": 7,
                    "column_end": 9,
                },
                "style": {"horizontal_alignment": "center"},
            },
        ],
        "default_row_height_px": 64,
        "row_heights": [
            {"start_index": 0, "end_index": 1, "pixel_size": 36},
            {"start_index": 1, "end_index": 2, "pixel_size": 32},
        ],
        "default_column_width_px": 140,
        "column_widths": [
            {"start_index": 0, "end_index": 1, "pixel_size": 80},
            {"start_index": 1, "end_index": 2, "pixel_size": 140},
            {"start_index": 2, "end_index": 3, "pixel_size": 150},
            {"start_index": 3, "end_index": 4, "pixel_size": 240},
            {"start_index": 4, "end_index": 5, "pixel_size": 220},
            {"start_index": 5, "end_index": 6, "pixel_size": 320},
            {"start_index": 6, "end_index": 7, "pixel_size": 320},
            {"start_index": 7, "end_index": 8, "pixel_size": 80},
            {"start_index": 8, "end_index": 9, "pixel_size": 90},
            {"start_index": 9, "end_index": 10, "pixel_size": 160},
        ],
        "frozen_row_count": 2,
        "frozen_column_count": 0,
        "merges": [
            {
                "row_start": 0,
                "row_end": 1,
                "column_start": 0,
                "column_end": column_count,
            }
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify a Workspace Feishu A:J test-case Sheet spec."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--final-cases", type=Path, required=True)
    build.add_argument("--title", required=True)
    build.add_argument("--out", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--final-cases", type=Path, required=True)
    verify.add_argument("--title", required=True)
    verify.add_argument("--spec", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        final_cases = read_object(args.final_cases)
        expected = build_spec(final_cases, args.title)
        if args.command == "build":
            write_object(args.out, expected)
            result = {
                "status": "ok",
                "spec_path": str(args.out.resolve()),
                "spec_sha256": spec_sha256(expected),
                "case_count": len(expected["values"]) - 2,
                "row_count": expected["row_count"],
                "column_count": expected["column_count"],
            }
        else:
            actual = read_object(args.spec)
            if canonical_bytes(actual) != canonical_bytes(expected):
                raise SpecBuildError("sheet spec 与 final_cases/title 的确定性结果不一致")
            result = {
                "status": "ok",
                "spec_sha256": spec_sha256(actual),
                "case_count": len(actual["values"]) - 2,
                "row_count": actual["row_count"],
                "column_count": actual["column_count"],
            }
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except SpecBuildError as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from helpers import SKILL_ROOT, build_valid_run, read_json, write_payload
from pipeline.contracts import final_semantic_signature


SPREADSHEET_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def cell_fill_rgb(archive: zipfile.ZipFile, worksheet_path: str, address: str) -> str:
    styles = ElementTree.fromstring(archive.read("xl/styles.xml"))
    worksheet = ElementTree.fromstring(archive.read(worksheet_path))
    cell = worksheet.find(f".//x:c[@r='{address}']", SPREADSHEET_NS)
    if cell is None:
        raise AssertionError(f"找不到单元格 {address}")
    style_index = int(cell.attrib.get("s", "0"))
    cell_formats = styles.find("x:cellXfs", SPREADSHEET_NS)
    fills = styles.find("x:fills", SPREADSHEET_NS)
    if cell_formats is None or fills is None:
        raise AssertionError("工作簿缺少 cellXfs 或 fills")
    fill_index = int(list(cell_formats)[style_index].attrib.get("fillId", "0"))
    foreground = list(fills)[fill_index].find("x:patternFill/x:fgColor", SPREADSHEET_NS)
    return "" if foreground is None else str(foreground.attrib.get("rgb") or "").upper()


def create_directory_link(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except OSError:
        pass
    if os.name != "nt":
        raise OSError(f"无法创建 node_modules symlink: {link}")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise OSError(result.stderr or result.stdout)


class WorkbookEndToEndTests(unittest.TestCase):
    def test_local_template_build_exact_readback_and_preview(self) -> None:
        node_value = os.environ.get("QA_CASE_XLSX_NODE")
        modules_value = os.environ.get("QA_CASE_XLSX_NODE_MODULES")
        self.assertTrue(node_value, "QA_CASE_XLSX_NODE 未设置")
        self.assertTrue(modules_value, "QA_CASE_XLSX_NODE_MODULES 未设置")
        node = Path(str(node_value))
        modules = Path(str(modules_value))
        self.assertTrue(node.is_file(), node)
        self.assertTrue(modules.is_dir(), modules)

        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-book-") as temporary:
            output_dir = Path(temporary) / "delivery"
            run_dir = output_dir / "audit"
            readiness = build_valid_run(run_dir)
            final_cases = read_json(run_dir / "final_cases.json")
            case_specs = [
                ("周年庆宝箱", "开启与奖励", "再次查看-状态保持"),
                ("匹配流程", "房间状态", "进入房间-状态正确"),
                ("匹配流程", "房间状态", "退出重进-状态恢复"),
                ("周年庆宝箱", "开启与奖励", "跨组再次查看-状态保持"),
            ]
            for number, (primary, secondary, checkpoint) in enumerate(case_specs, start=2):
                test_case = dict(final_cases["cases"][0])
                test_case["final_case_id"] = f"FINAL-{number:04d}"
                test_case["用例编号"] = str(number)
                test_case["一级模块"] = primary
                test_case["二级模块"] = secondary
                test_case["检查点"] = checkpoint
                test_case["semantic_components"] = {
                    **test_case["semantic_components"],
                    "target_state": f"目标状态-{number}",
                    "core_expected": f"可观察结果-{number}",
                }
                test_case["semantic_signature"] = final_semantic_signature(test_case)
                final_cases["cases"].append(test_case)
            write_payload(run_dir / "final_cases.json", final_cases)
            readiness["counts"]["final_cases"] = 5
            write_payload(run_dir / "delivery_readiness.json", readiness)
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            create_directory_link(runtime / "node_modules", modules)
            builder = runtime / "build_local_case_workbook.mjs"
            shutil.copy2(SKILL_ROOT / "scripts" / "build_local_case_workbook.mjs", builder)
            result = subprocess.run(
                [
                    str(node),
                    str(builder),
                    "build",
                    "--template",
                    str(SKILL_ROOT / "assets" / "local-case-template.xlsx"),
                    "--run-dir",
                    str(run_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                cwd=runtime,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            self.assertEqual(0, result.returncode, result.stderr or result.stdout)
            workbook = output_dir / readiness["output_filename"]
            self.assertTrue(workbook.is_file())
            self.assertTrue((run_dir / "workbook-preview.png").is_file())
            self.assertTrue((run_dir / "workbook-preview-header.png").is_file())
            readback = json.loads((run_dir / "workbook_readback.json").read_text(encoding="utf-8"))
            self.assertEqual("ok", readback["status"], readback)
            self.assertTrue(readback["values_match"])
            self.assertTrue(readback["formulas_present"])
            self.assertTrue(readback["top_layout_match"])
            self.assertTrue(readback["module_merges_match"])
            self.assertTrue(readback["no_excel_tables"])
            self.assertFalse(readback["external_links_present"])
            self.assertEqual(5, readback["case_count"])
            self.assertEqual(0, readback["boundary_confirmation_count"])
            self.assertEqual([], readback["unexpected_merge_ranges"])
            self.assertEqual([], readback["invalid_body_merge_ranges"])
            self.assertEqual(
                {"B11:B12", "C11:C12", "B13:B14", "C13:C14"},
                set(readback["module_merge_ranges"]),
            )
            self.assertEqual(10, len(readback["header_order"]))
            with zipfile.ZipFile(workbook) as archive:
                names = set(archive.namelist())
                self.assertFalse(any("vbaProject" in name for name in names))
                self.assertFalse(any(name.startswith("xl/externalLinks/") for name in names))
                worksheet_xml = "\n".join(
                    archive.read(name).decode("utf-8")
                    for name in names
                    if name.startswith("xl/worksheets/") and name.endswith(".xml")
                )
                self.assertFalse(any(name.startswith("xl/tables/") for name in names))
                worksheet_path = next(
                    name for name in names if name.startswith("xl/worksheets/") and name.endswith(".xml")
                )
                self.assertEqual("FF86D3E5", cell_fill_rgb(archive, worksheet_path, "A1"))
                self.assertEqual("FF86D3E5", cell_fill_rgb(archive, worksheet_path, "A2"))
                self.assertEqual("FFFFF2CC", cell_fill_rgb(archive, worksheet_path, "B2"))
                self.assertEqual("FF86D3E5", cell_fill_rgb(archive, worksheet_path, "A9"))
                self.assertIn('mergeCell ref="A1:J1"', worksheet_xml)
                self.assertIn('mergeCell ref="B9:D9"', worksheet_xml)
                self.assertIn('mergeCell ref="B11:B12"', worksheet_xml)
                self.assertIn('mergeCell ref="C11:C12"', worksheet_xml)
                self.assertIn('mergeCell ref="B13:B14"', worksheet_xml)
                self.assertIn('mergeCell ref="C13:C14"', worksheet_xml)
                self.assertNotIn('mergeCell ref="B11:B15"', worksheet_xml)
                self.assertNotIn('mergeCell ref="C11:C15"', worksheet_xml)
                self.assertNotIn('mergeCell ref="D11:D12"', worksheet_xml)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from helpers import SKILL_ROOT, build_valid_run


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
            readback = json.loads((run_dir / "workbook_readback.json").read_text(encoding="utf-8"))
            self.assertEqual("ok", readback["status"], readback)
            self.assertTrue(readback["values_match"])
            self.assertTrue(readback["formulas_present"])
            self.assertFalse(readback["external_links_present"])
            self.assertEqual(10, len(readback["header_order"]))
            with zipfile.ZipFile(workbook) as archive:
                names = set(archive.namelist())
                self.assertFalse(any("vbaProject" in name for name in names))
                self.assertFalse(any(name.startswith("xl/externalLinks/") for name in names))


if __name__ == "__main__":
    unittest.main()

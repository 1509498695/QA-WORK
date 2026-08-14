from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import SKILL_ROOT


class OfflineSmokeTests(unittest.TestCase):
    def test_validate_rules_runs_outside_qawork_with_unreachable_proxy(self) -> None:
        self.assertNotIn("qawork", str(SKILL_ROOT).lower())
        environment = os.environ.copy()
        environment.update(
            {
                "HTTP_PROXY": "http://127.0.0.1:9",
                "HTTPS_PROXY": "http://127.0.0.1:9",
                "ALL_PROXY": "http://127.0.0.1:9",
                "NO_PROXY": "",
            }
        )
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-outside-") as temporary:
            result = subprocess.run(
                [sys.executable, str(SKILL_ROOT / "scripts" / "run_case_pipeline.py"), "validate-rules"],
                cwd=temporary,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        self.assertIn('"rule_count": 50', result.stdout)

    def test_runtime_scripts_have_no_network_client_imports(self) -> None:
        scripts = list((SKILL_ROOT / "scripts").rglob("*.py")) + list((SKILL_ROOT / "scripts").rglob("*.mjs"))
        joined = "\n".join(path.read_text(encoding="utf-8") for path in scripts)
        for forbidden in ("import requests", "import httpx", "from urllib.request", "fetch("):
            self.assertNotIn(forbidden, joined)


if __name__ == "__main__":
    unittest.main()

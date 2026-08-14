from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from helpers import build_source_layer


class SourcePacketTests(unittest.TestCase):
    def test_all_supported_fixture_types_are_inventoried_without_following_links(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-source-") as temporary:
            run_dir = Path(temporary) / "audit"
            packet, ledger = build_source_layer(run_dir, reviewed=False)
            self.assertEqual(6, packet["counts"]["files"])
            self.assertEqual(0, packet["counts"]["unreadable"])
            self.assertGreaterEqual(packet["counts"]["content_units"], 20)
            self.assertEqual(4, len(ledger["evidence"]))
            self.assertFalse(packet["options"]["external_links_followed"])
            extensions = {item["extension"] for item in packet["files"]}
            self.assertEqual({".docx", ".pdf", ".xlsx", ".md", ".txt", ".png"}, extensions)

    def test_cp_feedback_sheet_is_excluded_and_recorded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="qa-case-xlsx-source-") as temporary:
            packet, _ = build_source_layer(Path(temporary) / "audit", reviewed=False)
            xlsx = next(item for item in packet["files"] if item["extension"] == ".xlsx")
            self.assertTrue(any(item["name"] == "CP反馈" for item in xlsx["excluded_items"]))
            all_text = "\n".join(unit["text"] for unit in xlsx["content_units"])
            self.assertNotIn("不应进入 source_packet", all_text)


if __name__ == "__main__":
    unittest.main()

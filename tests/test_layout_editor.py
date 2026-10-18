import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from app.utils.layout import normalize_template
from app.utils.template_library import get_layout_template, sanitize_template_slots, upsert_layout_template


class LayoutEditorTests(unittest.TestCase):
    def test_precise_geometry_is_preserved_when_saving_template(self):
        slot = {"slot_id": "text", "page": 1, "x": 11, "y": 33, "w": 87, "h": 61, "type": "text"}
        saved = sanitize_template_slots([slot])[0]
        for field in ("x", "y", "w", "h"):
            self.assertEqual(saved[field], slot[field])

    def test_report_and_template_bounds_agree(self):
        oversized = {"id": "chart", "x": 999, "y": 270, "w": 300, "h": 400}
        report = normalize_template([oversized])[0]
        template = sanitize_template_slots([oversized])[0]
        for item in (report, template):
            self.assertGreaterEqual(item["x"], 0)
            self.assertLessEqual(item["x"] + item["w"], 210)
            self.assertGreaterEqual(item["y"], 30)
            self.assertLessEqual(item["y"] + item["h"], 285)

    def test_template_keeps_new_empty_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("app.utils.template_library.TEMPLATE_LIBRARY_PATH", str(Path(directory) / "templates.json")):
                saved = upsert_layout_template({"name": "Two pages", "slots": [], "page_count": 2})
                self.assertEqual(get_layout_template(saved["id"])["page_count"], 2)


if __name__ == "__main__":
    unittest.main()

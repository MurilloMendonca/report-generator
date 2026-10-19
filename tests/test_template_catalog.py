import json
from pathlib import Path
import tempfile
import unittest

from app.utils.layout import normalize_template
from app.utils.template_library import (
    BUNDLED_TEMPLATE_LIBRARY_PATH,
    _default_templates,
    build_layout_from_template,
    load_layout_templates,
    sanitize_template_slots,
    upsert_layout_template,
)
from tests.auth_helpers import make_user
from app.services.legacy_import import _import_templates


class TemplateCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads(BUNDLED_TEMPLATE_LIBRARY_PATH.read_text(encoding="utf-8"))
        self.new_templates = [item for item in self.catalog if item.get("builtin") is True]

    def test_designs_have_unique_slots_valid_bounds_and_no_overlaps(self):
        self.assertEqual(len(self.new_templates), 8)
        self.assertEqual(len({item["id"] for item in self.catalog}), len(self.catalog))
        for template in self.new_templates:
            with self.subTest(template=template["id"]):
                slots = template["slots"]
                self.assertEqual(len({slot["slot_id"] for slot in slots}), len(slots))
                self.assertEqual(template["page_count"], max(slot["page"] for slot in slots))
                self.assertEqual(slots, sanitize_template_slots(slots))
                self.assertEqual({slot["type"] for slot in slots}, {"text", "graphic"})
                for index, slot in enumerate(slots):
                    self.assertTrue(slot["name"] and slot["description"])
                    self.assertGreaterEqual(slot["x"], 10)
                    self.assertLessEqual(slot["x"] + slot["w"], 200)
                    self.assertGreaterEqual(slot["y"], 30)
                    self.assertLessEqual(slot["y"] + slot["h"], 285)
                    for other in slots[index + 1:]:
                        overlaps = (slot["page"] == other["page"]
                                    and slot["x"] < other["x"] + other["w"]
                                    and other["x"] < slot["x"] + slot["w"]
                                    and slot["y"] < other["y"] + other["h"]
                                    and other["y"] < slot["y"] + slot["h"])
                        self.assertFalse(overlaps, (slot["slot_id"], other["slot_id"]))

    def test_existing_saved_layouts_are_not_promoted_to_shared_defaults(self):
        defaults = _default_templates()
        original = {"executive_standard", "data_heavy", "document_text_only"}
        expected = original | {template["id"] for template in self.new_templates}
        self.assertEqual({template["id"] for template in defaults}, expected)
        self.assertEqual([template["id"] for template in defaults[:3]],
                         ["executive_standard", "data_heavy", "document_text_only"])

    def test_shared_designs_appear_for_each_account_and_loading_is_idempotent(self):
        owner, other = make_user(), make_user()
        own = upsert_layout_template({"name": "Private design", "slots": []}, owner.id)
        expected = {template["id"] for template in self.new_templates}
        first = load_layout_templates(owner.id)
        repeated = load_layout_templates(owner.id)
        others = load_layout_templates(other.id)
        self.assertEqual([item["id"] for item in first], [item["id"] for item in repeated])
        for templates in (first, others):
            shared = {item["id"] for item in templates if item["builtin"]}
            self.assertTrue(expected.issubset(shared))
            self.assertNotIn("tpl_d39eec0f99", shared)
            self.assertNotIn("tpl_228b25ef43", shared)
        self.assertIn(own["id"], {item["id"] for item in first})
        self.assertNotIn(own["id"], {item["id"] for item in others})

    def test_importing_the_catalog_does_not_rewrite_seeded_designs(self):
        owner = make_user()
        load_layout_templates(owner.id)
        with tempfile.TemporaryDirectory(prefix="test-layout-catalog-") as directory:
            workspace = Path(directory)
            (workspace / "layout_templates.json").write_text(json.dumps(self.new_templates), encoding="utf-8")
            self.assertEqual(_import_templates(owner, workspace, dry_run=True), 0)
            self.assertEqual(_import_templates(owner, workspace, dry_run=False), 0)

    def test_report_layout_preserves_every_designed_slot_and_page(self):
        for template in self.new_templates:
            with self.subTest(template=template["id"]):
                artifacts = [
                    {"id": slot["slot_id"], "type": slot["type"], "indicator": slot["name"]}
                    for slot in template["slots"]
                ]
                layout = build_layout_from_template(artifacts, template["id"])
                self.assertEqual(len(layout), len(template["slots"]))
                self.assertEqual(len({item["id"] for item in layout}), len(artifacts))
                self.assertEqual(layout, normalize_template(layout))
                self.assertEqual(
                    sorted((item["page"], item["x"], item["y"], item["w"], item["h"]) for item in layout),
                    sorted((slot["page"], slot["x"], slot["y"], slot["w"], slot["h"]) for slot in template["slots"]),
                )


if __name__ == "__main__":
    unittest.main()

import unittest

from app.utils.component_library import (
    COMPONENT_PRESETS,
    VISUAL_THEMES,
    component_library_payload,
    resolved_visual_context,
)


class ComponentLibraryTests(unittest.TestCase):
    def test_catalog_ids_are_unique_and_required_fields_exist(self):
        self.assertEqual(len(COMPONENT_PRESETS), len(set(COMPONENT_PRESETS)))
        self.assertEqual(len(VISUAL_THEMES), len(set(VISUAL_THEMES)))

        for preset_id, preset in COMPONENT_PRESETS.items():
            self.assertEqual(preset["id"], preset_id)
            self.assertIn("family", preset)
            self.assertIn("data_roles", preset)
            self.assertIn("default_options", preset)
            self.assertIn("prompt_contract", preset)

        for theme_id, theme in VISUAL_THEMES.items():
            self.assertEqual(theme["id"], theme_id)
            self.assertTrue(theme.get("palette"))
            self.assertIn("table", theme)

    def test_payload_exposes_presets_themes_and_styles(self):
        payload = component_library_payload()

        self.assertTrue(payload["presets"])
        self.assertTrue(payload["themes"])
        self.assertTrue(payload["chart_styles"])
        self.assertEqual(payload["default_theme_id"], "executive_clean")

    def test_resolved_visual_context_uses_component_overrides(self):
        context = resolved_visual_context(
            {
                "type": "table",
                "component_preset_id": "table_summary",
                "visual_theme_id": "minimal_mono",
                "chart_style_id": "dense_dashboard",
                "visual_options": {"top_n": 3},
            },
            {"visual_theme_id": "executive_clean", "chart_style_id": "modern"},
        )

        self.assertEqual(context["preset"]["id"], "table_summary")
        self.assertEqual(context["theme"]["id"], "minimal_mono")
        self.assertEqual(context["style"]["id"], "dense_dashboard")
        self.assertEqual(context["options"]["top_n"], 3)


if __name__ == "__main__":
    unittest.main()

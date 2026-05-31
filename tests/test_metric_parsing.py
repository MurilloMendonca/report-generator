import unittest

from app.services.llm_service import (
    chart_code_has_layout_risk,
    estimate_text_block_capacity,
    extract_json_objects,
    normalize_metric_value,
    text_layout_guidance,
    visual_slot_guidance,
)


class MetricParsingTests(unittest.TestCase):
    def test_extracts_nested_metric_payload_from_noisy_stdout(self):
        stdout = (
            "debug before\n"
            '{"value": {"Sales": 0.12, "Operations": 0.08}}\n'
            "debug after"
        )

        self.assertEqual(
            extract_json_objects(stdout),
            [{"value": {"Sales": 0.12, "Operations": 0.08}}],
        )

    def test_extracts_last_complete_metric_payload(self):
        stdout = '{"value": "N/A"}\nprogress\n{"value": [{"dept": "Sales", "rate": 0.12}]}'

        self.assertEqual(
            extract_json_objects(stdout)[-1],
            {"value": [{"dept": "Sales", "rate": 0.12}]},
        )

    def test_normalizes_empty_or_error_values_to_na(self):
        self.assertEqual(normalize_metric_value({"Sales": "Error: bad column", "Ops": ""}), "N/A")
        self.assertEqual(normalize_metric_value([None, "", "N/A"]), "N/A")

    def test_keeps_compact_grouped_values(self):
        self.assertEqual(
            normalize_metric_value({"Sales": 0.12, "Ops": None}),
            {"Sales": 0.12},
        )

    def test_detects_risky_chart_annotation_and_inside_legend(self):
        self.assertTrue(chart_code_has_layout_risk(
            "ax.text(0, 10, 'Meta')\nax.legend(loc='upper left')"
        ))
        self.assertTrue(chart_code_has_layout_risk(
            "ax.annotate('104%', xy=(0, 10))\nax.legend(loc='upper right')"
        ))
        self.assertTrue(chart_code_has_layout_risk(
            "ax.set_xlabel('Semana')\nax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18))"
        ))

    def test_allows_outside_legend_or_no_annotations(self):
        self.assertFalse(chart_code_has_layout_risk(
            "ax.text(0, 10, 'Meta')\nax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18))"
        ))
        self.assertFalse(chart_code_has_layout_risk(
            "ax.legend(loc='upper right')"
        ))
        self.assertFalse(chart_code_has_layout_risk(
            "ax.set_xlabel('')\nax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.18))"
        ))

    def test_text_capacity_is_generous_enough_for_medium_blocks(self):
        capacity = estimate_text_block_capacity({"w": 190, "h": 45}, {"font_size": 10, "line_height": 5})

        self.assertGreaterEqual(capacity["target_words"], 110)
        self.assertGreater(capacity["soft_max_words"], capacity["target_words"])

    def test_text_layout_guidance_maps_artifacts_to_slots(self):
        guidance = text_layout_guidance(
            [{"id": "t_intro", "indicator": "Introdução"}],
            [{"id": "t_intro", "page": 1, "x": 10, "y": 30, "w": 190, "h": 45}],
            {"font_size": 10, "line_height": 5},
        )

        self.assertEqual(guidance[0]["id"], "t_intro")
        self.assertEqual(guidance[0]["slot"]["w_mm"], 190)
        self.assertIn("soft_max_words", guidance[0])

    def test_visual_slot_guidance_marks_compact_blocks(self):
        guidance = visual_slot_guidance({"page": 1, "w": 90, "h": 55})

        self.assertTrue(guidance["compact"])
        self.assertEqual(guidance["w_mm"], 90)


if __name__ == "__main__":
    unittest.main()

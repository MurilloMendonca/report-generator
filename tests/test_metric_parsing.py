import unittest

from app.services.llm_service import extract_json_objects, normalize_metric_value


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


if __name__ == "__main__":
    unittest.main()

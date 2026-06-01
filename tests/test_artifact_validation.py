import unittest

from app.utils.artifact_validation import validate_report_plan


class ArtifactValidationTests(unittest.TestCase):
    def metadata(self):
        return [{
            "filename": "sales.csv",
            "columns": [
                {"name": "region", "unique_values": 4},
                {"name": "customer", "unique_values": 120},
                {"name": "revenue", "unique_values": 50},
            ],
        }]

    def test_normalizes_ids_types_and_reports_missing_columns(self):
        artifacts, issues = validate_report_plan([
            {"id": "1 Total", "type": "unknown", "columns": ["sales.csv:missing"]},
            {"id": "1 Total", "type": "metric", "indicator": "Total", "columns": ["sales.csv:revenue"]},
        ], self.metadata())

        self.assertEqual(artifacts[0]["id"], "kpi_1_total")
        self.assertEqual(artifacts[0]["type"], "graphic")
        self.assertEqual(artifacts[1]["id"], "kpi_1_total_2")
        self.assertIn("missing_columns", {issue["code"] for issue in issues})
        self.assertIn("duplicate_id", {issue["code"] for issue in issues})

    def test_changes_high_cardinality_pie_to_bar(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_customers",
                "indicator": "Customers",
                "type": "pie",
                "sub_type": "pie",
                "columns": ["sales.csv:customer"],
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["type"], "bar")
        self.assertEqual(artifacts[0]["sub_type"], "bar")
        self.assertIn("pie_high_cardinality", {issue["code"] for issue in issues})

    def test_reports_template_pressure(self):
        _, issues = validate_report_plan([
            {"id": "t1", "type": "text", "indicator": "T1"},
            {"id": "t2", "type": "text", "indicator": "T2"},
        ], self.metadata(), {"text_slots": 1, "graphic_slots": 2})

        self.assertIn("text_template_limit", {issue["code"] for issue in issues})


if __name__ == "__main__":
    unittest.main()

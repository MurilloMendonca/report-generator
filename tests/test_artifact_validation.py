import unittest

from app.utils.artifact_validation import validate_report_plan


class ArtifactValidationTests(unittest.TestCase):
    def metadata(self):
        return [{
            "filename": "sales.csv",
            "columns": [
                {"name": "region", "type": "object", "unique_values": 4},
                {"name": "customer", "type": "object", "unique_values": 120},
                {"name": "case_id", "type": "object", "unique_values": 200},
                {"name": "revenue", "type": "float64", "unique_values": 50},
                {"name": "date", "type": "datetime64[ns]", "unique_values": 12},
                {
                    "name": "Year",
                    "type": "int64",
                    "role": "measure",
                    "unique_values": 5,
                    "stats": {"min": 2020, "max": 2024},
                },
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
        self.assertEqual(artifacts[0]["component_preset_id"], "bar_ranked")
        self.assertIn("pie_high_cardinality", {issue["code"] for issue in issues})

    def test_infers_component_preset_and_visual_options(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_table",
                "indicator": "Top regions",
                "type": "table",
                "columns": ["sales.csv:region", "sales.csv:revenue"],
                "visual_options": {
                    "top_n": 5,
                    "labels": {"region": "Regiao", "revenue": "Receita"},
                },
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["component_preset_id"], "table_summary")
        self.assertEqual(artifacts[0]["visual_options"]["top_n"], 5)
        self.assertEqual(artifacts[0]["visual_options"]["labels"]["revenue"], "Receita")
        self.assertTrue(artifacts[0]["visual_options"]["striped_rows"])
        self.assertEqual(artifacts[0]["render_mode"], "generated_code")
        self.assertEqual(artifacts[0]["data_binding"]["source"], "sales.csv")
        self.assertEqual(artifacts[0]["data_binding"]["roles"]["dimensions"], "region")
        self.assertEqual(artifacts[0]["data_binding"]["roles"]["metrics"], "revenue")
        self.assertNotIn("invalid_component_preset", {issue["code"] for issue in issues})

    def test_preserves_visual_color_options(self):
        artifacts, _issues = validate_report_plan([
            {
                "id": "g_trend",
                "indicator": "Trend",
                "type": "line",
                "columns": ["sales.csv:date", "sales.csv:case_id"],
                "component_preset_id": "line_timeseries",
                "visual_options": {"line_color": "#dc2626", "color": "#dc2626"},
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["visual_options"]["line_color"], "#dc2626")
        self.assertEqual(artifacts[0]["visual_options"]["color"], "#dc2626")

    def test_reports_invalid_component_preset(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_bad",
                "indicator": "Bad",
                "type": "scatter",
                "component_preset_id": "missing",
                "columns": ["sales.csv:revenue", "sales.csv:region"],
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["component_preset_id"], "scatter_relationship")
        self.assertIn("invalid_component_preset", {issue["code"] for issue in issues})

    def test_reports_template_pressure(self):
        _, issues = validate_report_plan([
            {"id": "t1", "type": "text", "indicator": "T1"},
            {"id": "t2", "type": "text", "indicator": "T2"},
        ], self.metadata(), {"text_slots": 1, "graphic_slots": 2})

        self.assertIn("text_template_limit", {issue["code"] for issue in issues})

    def test_validates_data_binding_roles(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_scatter",
                "indicator": "Scatter",
                "type": "scatter",
                "component_preset_id": "scatter_relationship",
                "columns": ["sales.csv:region", "sales.csv:revenue"],
                "data_binding": {
                    "source": "sales.csv",
                    "roles": {"x": "region", "y": "revenue"},
                    "aggregation": "sum",
                },
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["data_binding"]["roles"]["x"], "region")
        self.assertIn("data_binding_role_mismatch", {issue["code"] for issue in issues})
        self.assertTrue(artifacts[0]["visual_warnings"])

    def test_infers_missing_roles_when_data_binding_is_partial(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_table",
                "indicator": "Top regions",
                "type": "table",
                "component_preset_id": "table_summary",
                "columns": ["sales.csv:region", "sales.csv:revenue"],
                "data_binding": {
                    "source": "sales.csv",
                    "roles": {},
                    "aggregation": "sum",
                    "filters": [],
                },
                "render_mode": "library",
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["data_binding"]["roles"]["dimensions"], "region")
        self.assertEqual(artifacts[0]["data_binding"]["roles"]["metrics"], "revenue")
        self.assertFalse(artifacts[0]["visual_warnings"])
        self.assertNotIn("data_binding_role_missing", {issue["code"] for issue in issues})

    def test_accepts_numeric_year_as_temporal_binding(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_yearly",
                "indicator": "Yearly revenue",
                "type": "graphic",
                "component_preset_id": "line_timeseries",
                "columns": ["sales.csv:Year", "sales.csv:revenue"],
                "data_binding": {
                    "source": "sales.csv",
                    "roles": {"date": "Year", "value": "revenue"},
                    "aggregation": "sum",
                },
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["data_binding"]["roles"]["date"], "Year")
        self.assertEqual(artifacts[0]["visual_warnings"], [])
        self.assertNotIn("data_binding_role_mismatch", {issue["code"] for issue in issues})

    def test_count_aggregation_accepts_categorical_measure_bindings(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_cases_trend",
                "indicator": "Cases over time",
                "type": "graphic",
                "component_preset_id": "line_timeseries",
                "columns": ["sales.csv:date", "sales.csv:case_id"],
                "data_binding": {
                    "source": "sales.csv",
                    "roles": {"date": "date", "value": "case_id"},
                    "aggregation": "count",
                },
                "render_mode": "library",
            },
            {
                "id": "g_summary",
                "indicator": "Cases by region",
                "type": "table",
                "component_preset_id": "table_summary",
                "columns": ["sales.csv:region", "sales.csv:case_id"],
                "data_binding": {
                    "source": "sales.csv",
                    "roles": {"dimensions": "region", "metrics": "case_id"},
                    "aggregation": "count",
                },
                "render_mode": "library",
            },
        ], self.metadata())

        self.assertEqual(artifacts[0]["visual_warnings"], [])
        self.assertEqual(artifacts[1]["visual_warnings"], [])
        issue_codes = {issue["code"] for issue in issues}
        self.assertNotIn("data_binding_role_mismatch", issue_codes)
        self.assertNotIn("preset_role_mismatch", issue_codes)

    def test_library_render_mode_is_preserved_for_supported_presets(self):
        artifacts, issues = validate_report_plan([
            {
                "id": "g_table",
                "indicator": "Table",
                "type": "table",
                "columns": ["sales.csv:region", "sales.csv:revenue"],
                "render_mode": "library",
            }
        ], self.metadata())

        self.assertEqual(artifacts[0]["render_mode"], "library")
        self.assertNotIn("library_render_unsupported", {issue["code"] for issue in issues})


if __name__ == "__main__":
    unittest.main()

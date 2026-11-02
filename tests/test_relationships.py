import os
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from app.services import pipeline_service
from app.utils.artifact_validation import normalize_data_binding
from app.utils.component_library import get_preset
from app.utils.component_renderers import sandbox_runtime_source
from app.utils.relationships import detect_relationships


def outcomes():
    return pd.DataFrame({
        "campus": ["East", "East", "West", "West", "Central", "North"],
        "program": ["Data", "Design", "Data", "Business", "Design", "Data"],
        "students": [120, 90, 100, 80, 110, 40],
    })


def budget():
    return pd.DataFrame({"campus": ["East", "West", "Central"], "total_budget": [780000, 640000, 975000]})


class DetectionTests(unittest.TestCase):
    def test_same_name_key_is_verified_many_to_one(self):
        found = detect_relationships({"outcomes.xlsx": outcomes(), "budget.csv": budget()})
        self.assertEqual(len(found), 1)
        link = found[0]
        self.assertEqual((link["from"], link["to"]), ({"dataset": "outcomes.xlsx", "column": "campus"},
                                                      {"dataset": "budget.csv", "column": "campus"}))
        self.assertEqual(link["cardinality"], "many_to_one")
        self.assertEqual((link["matched_values"], link["from_distinct"]), (3, 4))
        self.assertAlmostEqual(link["coverage"], 0.75)

    def test_foreign_key_names_link_to_the_referenced_table_id(self):
        orders = pd.DataFrame({"order": [1, 2, 3], "customer_id": [10, 10, 11]})
        customers = pd.DataFrame({"id": [10.0, 11.0, 12.0], "name": ["Ana", "Bia", "Caio"]})
        found = detect_relationships({"orders.csv": orders, "customers.csv": customers})
        self.assertEqual([(item["from"]["column"], item["to"]["column"], item["match_reason"]) for item in found],
                         [("customer_id", "id", "key_name")])

    def test_rejects_repeated_targets_low_coverage_and_text_codes_that_only_look_numeric(self):
        repeated = pd.DataFrame({"campus": ["East", "East", "West"], "budget": [1, 2, 3]})
        self.assertEqual(detect_relationships({"a.csv": outcomes().iloc[:3], "b.csv": repeated}), [])
        unrelated = pd.DataFrame({"campus": ["South", "Lake", "East"], "budget": [1, 2, 3]})
        self.assertEqual(detect_relationships({"a.csv": outcomes(), "b.csv": unrelated}), [])
        codes = pd.DataFrame({"code": ["07", "08"], "label": ["x", "y"]})
        numbers = pd.DataFrame({"code": [7, 8, 8], "value": [1, 2, 3]})
        self.assertEqual(detect_relationships({"numbers.csv": numbers, "codes.csv": codes}), [])

    def test_numeric_amounts_with_the_same_name_are_not_keys(self):
        merged = outcomes().assign(total_budget=[780000, 780000, 640000, 640000, 975000, 975000], year=2026)
        targets = budget().assign(year=[2026, 2025, 2024])
        found = detect_relationships({"merged.csv": merged, "targets.csv": targets})
        self.assertEqual(sorted(item["from"]["column"] for item in found), ["campus", "year"])

    def test_unique_keys_on_both_sides_are_one_to_one_in_each_direction(self):
        left = pd.DataFrame({"campus": ["East", "West"], "dean": ["A", "B"]})
        found = detect_relationships({"deans.csv": left, "budget.csv": budget()})
        self.assertEqual({(item["from"]["dataset"], item["cardinality"]) for item in found},
                         {("deans.csv", "one_to_one"), ("budget.csv", "one_to_one")})


class JoinRenderingTests(unittest.TestCase):
    def setUp(self):
        self.runtime = {}
        exec(sandbox_runtime_source(), self.runtime)
        self.runtime["_COMPONENT_OUTCOME"].clear()
        self.frames = {"outcomes.xlsx": outcomes(), "budget.csv": budget()}
        self.binding = {"source": "outcomes.xlsx", "joins": [{"source": "budget.csv", "left_on": "campus", "right_on": "campus"}]}

    def test_join_keeps_every_base_row_and_marks_unmatched_rows(self):
        joined = self.runtime["_library_joined_frame"](self.frames, self.binding)
        self.assertEqual(len(joined), 6)
        self.assertEqual(joined["students"].sum(), outcomes()["students"].sum())
        self.assertIn("budget.csv:campus", joined.columns)
        self.assertTrue(pd.isna(joined.loc[joined["campus"] == "North", "total_budget"]).all())
        self.assertIn("Some rows had no matching record in a joined table", self.runtime["_COMPONENT_OUTCOME"]["warnings"])

    def test_repeated_join_key_is_rejected_instead_of_inflating_totals(self):
        self.frames["budget.csv"] = pd.concat([budget(), budget().iloc[:1]])
        with self.assertRaises(self.runtime["VisualBindingError"]) as raised:
            self.runtime["_library_joined_frame"](self.frames, self.binding)
        self.assertIn("repeats", str(raised.exception))

    def test_numeric_keys_match_across_integer_and_float_columns(self):
        frames = {"orders.csv": pd.DataFrame({"customer_id": [10, 11, 10]}),
                  "customers.csv": pd.DataFrame({"id": [10.0, 11.0], "region": ["N", "S"]})}
        joined = self.runtime["_library_joined_frame"](frames, {"source": "orders.csv", "joins": [
            {"source": "customers.csv", "left_on": "customer_id", "right_on": "id"}]})
        self.assertEqual(list(joined["region"]), ["N", "S", "N"])

    def test_bound_visual_reads_columns_from_the_joined_table(self):
        captured = {}

        def fake_bar(pdf, dataframes, x, y, w, h, **kwargs):
            captured.update(kwargs, frame=dataframes[kwargs["source"]])

        self.runtime["render_bar_chart"] = fake_bar
        artifact = {"id": "g", "component_preset_id": "bar_ranked", "data_binding": {
            **self.binding, "roles": {"category": "campus", "value": "budget.csv:total_budget"}, "aggregation": "max"}}
        self.runtime["_render_bound_visual_component"](object(), self.frames, artifact, 0, 0, 100, 60)
        self.assertEqual(captured["value"], "budget.csv:total_budget")
        resolved = self.runtime["_library_resolve_column"](captured["frame"], captured["value"])
        self.assertEqual(resolved, "total_budget")
        self.assertEqual(len(captured["frame"]), 6)

    def test_bound_visual_cannot_use_a_table_that_is_not_joined(self):
        artifact = {"id": "g", "component_preset_id": "bar_ranked", "data_binding": {
            "source": "outcomes.xlsx", "roles": {"category": "campus", "value": "budget.csv:total_budget"}}}
        with self.assertRaises(self.runtime["VisualBindingError"]):
            self.runtime["_render_bound_visual_component"](object(), self.frames, artifact, 0, 0, 100, 60)


class PlanValidationTests(unittest.TestCase):
    def metadata(self, budget_unique=3):
        return [
            {"filename": "outcomes.xlsx", "shape": {"rows": 6}, "columns": [
                {"name": "campus", "role": "dimension", "type": "object", "unique_values": 4, "null_count": 0},
                {"name": "students", "role": "measure", "type": "int64", "unique_values": 6, "null_count": 0}]},
            {"filename": "budget.csv", "shape": {"rows": 3}, "columns": [
                {"name": "campus", "role": "dimension", "type": "object", "unique_values": budget_unique, "null_count": 0},
                {"name": "total_budget", "role": "measure", "type": "int64", "unique_values": 3, "null_count": 0}]},
        ]

    def normalize(self, binding, metadata=None):
        issues = []
        artifact = {"id": "g", "type": "graphic", "data_binding": binding}
        result = normalize_data_binding(artifact, get_preset("bar_ranked"), metadata or self.metadata(), issues)
        return result, issues

    def test_unqualified_column_from_joined_table_is_qualified(self):
        binding, issues = self.normalize({"source": "outcomes.xlsx", "roles": {"category": "campus", "value": "total_budget"},
                                          "joins": [{"source": "budget.csv", "left_on": "campus", "right_on": "campus"}]})
        self.assertEqual(binding["roles"]["value"], "budget.csv:total_budget")
        self.assertEqual(binding["joins"][0]["how"], "left")
        self.assertEqual(issues, [])

    def test_join_on_repeated_key_or_missing_table_is_reported(self):
        _binding, issues = self.normalize({"source": "outcomes.xlsx", "roles": {"category": "campus", "value": "students"},
                                           "joins": [{"source": "budget.csv", "left_on": "campus", "right_on": "campus"}]},
                                          self.metadata(budget_unique=2))
        self.assertIn("data_binding_join_key_not_unique", [issue["code"] for issue in issues])
        _binding, issues = self.normalize({"source": "outcomes.xlsx", "roles": {"category": "campus", "value": "students"},
                                           "joins": [{"source": "missing.csv", "left_on": "campus", "right_on": "campus"}]})
        self.assertIn("data_binding_join_source_missing", [issue["code"] for issue in issues])

    def test_columns_from_a_table_without_join_are_still_rejected(self):
        _binding, issues = self.normalize({"source": "outcomes.xlsx", "roles": {"category": "campus", "value": "budget.csv:total_budget"}})
        self.assertIn("data_binding_column_missing", [issue["code"] for issue in issues])


class PipelineDetectionTests(unittest.TestCase):
    def test_relationships_are_detected_once_per_data_version(self):
        with tempfile.TemporaryDirectory(prefix="test-relationships-") as directory:
            outcomes_path, budget_path = os.path.join(directory, "outcomes.csv"), os.path.join(directory, "budget.csv")
            outcomes().to_csv(outcomes_path, index=False)
            budget().to_csv(budget_path, index=False)
            files = [{"original_filename": "outcomes.csv", "path": outcomes_path, "artifact": {"filename": "outcomes.csv"}},
                     {"original_filename": "budget.csv", "path": budget_path, "artifact": {"filename": "budget.csv"}}]
            with patch.object(pipeline_service, "detect_relationships", wraps=pipeline_service.detect_relationships) as detect:
                model = pipeline_service.semantic_model_for(files)
                again = pipeline_service.semantic_model_for(files, model)
                self.assertEqual(detect.call_count, 1)
                budget().assign(campus=["East", "South", "Lake"]).to_csv(budget_path, index=False)
                os.utime(budget_path, ns=(1, 1))
                changed = pipeline_service.semantic_model_for(files, again)
                self.assertEqual(detect.call_count, 2)
            self.assertEqual(len(model["verified_relationships"]), 1)
            self.assertEqual(again["verified_relationships"], model["verified_relationships"])
            self.assertEqual(changed["verified_relationships"], [])
            prompt_payload = pipeline_service.metadata_json({"files": files, "semantic_model": model})
            self.assertIn('"verified_relationships"', prompt_payload)


if __name__ == "__main__":
    unittest.main()

"""Deterministic safeguards applied to model plans before rendering."""
import json
import unittest
from unittest.mock import patch

from app.services.llm.planning import ensure_narrative_sections, generate_artifacts


CHART = {"id": "g_sales", "type": "graphic", "indicator": "Sales", "page": 1}


class NarrativeSectionTests(unittest.TestCase):
    def test_chart_only_plans_get_a_summary_and_a_conclusion(self):
        plan = [{"id": "m_total", "type": "metric"}, CHART, {**CHART, "id": "g_mix", "page": 2}]
        artifacts, added = ensure_narrative_sections(plan, {"language": "Portuguese"})
        self.assertEqual(added, ["kpi_resumo", "kpi_conclusao"])
        texts = {a["id"]: a for a in artifacts if a["type"] == "text"}
        self.assertEqual((texts["kpi_resumo"]["page"], texts["kpi_resumo"]["indicator"]), (1, "Resumo executivo"))
        self.assertEqual((texts["kpi_conclusao"]["page"], texts["kpi_conclusao"]["indicator"]), (2, "Conclusão"))
        self.assertEqual(len(plan), 3)  # The input list is not changed.

    def test_plans_with_any_text_are_left_alone(self):
        plan = [CHART, {"id": "analysis", "type": "text", "page": 1}]
        self.assertEqual(ensure_narrative_sections(plan, {}), (plan, []))

    def test_plans_without_visuals_are_left_alone(self):
        plan = [{"id": "m_total", "type": "metric"}]
        self.assertEqual(ensure_narrative_sections(plan, {}), (plan, []))

    def test_other_languages_use_english_labels(self):
        artifacts, _ = ensure_narrative_sections([CHART], {"language": "German"})
        self.assertEqual([a["indicator"] for a in artifacts[1:]], ["Executive summary", "Conclusion"])


METRIC = {"id": "m_total", "type": "metric", "indicator": "Total", "columns": ["sales.csv:revenue"]}


class PlanRetryTests(unittest.TestCase):
    def plan(self, responses, **kwargs):
        with patch("app.services.llm.client.call_llm", side_effect=[json.dumps(r) for r in responses]) as llm:
            artifacts = generate_artifacts("{}", "Revenue by region", settings={"language": "Portuguese"}, **kwargs)
        return artifacts, llm

    def test_tabular_plans_without_metrics_are_requested_again(self):
        artifacts, llm = self.plan([[CHART], [METRIC, CHART]], require_metrics=True)
        self.assertEqual(llm.call_count, 2)
        self.assertIn("no metric artifacts", llm.call_args.args[3])
        self.assertIn("m_total", [a["id"] for a in artifacts])

    def test_the_last_plan_is_accepted_even_without_metrics(self):
        artifacts, llm = self.plan([[CHART]] * 3, require_metrics=True)
        self.assertEqual(llm.call_count, 3)
        self.assertEqual([a["type"] for a in artifacts], ["graphic", "text", "text"])

    def test_metrics_are_optional_for_document_sessions(self):
        _, llm = self.plan([[CHART]])
        self.assertEqual(llm.call_count, 1)

    def test_added_texts_are_paginated_with_the_plan(self):
        artifacts, _ = self.plan([[METRIC, CHART, {**CHART, "id": "g_mix", "page": 2}]], require_metrics=True)
        pages = {a["id"]: a.get("page") for a in artifacts}
        self.assertEqual(pages["kpi_resumo"], 1)
        self.assertEqual(pages["kpi_conclusao"], max(pages["g_sales"], pages["g_mix"]))


if __name__ == "__main__":
    unittest.main()

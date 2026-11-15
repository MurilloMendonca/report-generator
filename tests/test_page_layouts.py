import json
import unittest
from unittest.mock import patch

from app.services.llm.planning import generate_artifacts
from app.utils.page_layouts import compact_report_pages, page_designs, select_page_design
from app.utils.template_library import _default_templates, build_layout_from_template, page_layout_catalog


def components(texts, graphics, page=1, offset=0, layout_id=None):
    return [{"id": f"component_{offset + index}", "indicator": f"Evidence {offset + index}",
             "type": "text" if index < texts else "graphic", "page": page,
             **({"page_layout_id": layout_id} if layout_id else {})}
            for index in range(texts + graphics)]


class PagePackingTests(unittest.TestCase):
    def setUp(self):
        self.templates = _default_templates()
        self.designs = page_designs(self.templates)

    def test_eight_sparse_pages_become_two_full_narrative_pages(self):
        plan = [components(1, 0, page, page)[0] for page in range(1, 9)]
        packed = compact_report_pages(plan, self.designs)
        self.assertEqual([item["page"] for item in packed], [1] * 4 + [2] * 4)
        self.assertEqual({item["id"] for item in packed}, {item["id"] for item in plan})
        self.assertEqual([item["page"] for item in plan], list(range(1, 9)), "Packing must not mutate the proposed plan")
        self.assertEqual({item["page_layout_id"] for item in packed}, {"document_text_only:1"})

    def test_packing_balances_pages_instead_of_leaving_a_single_trailing_chart(self):
        packed = compact_report_pages(components(0, 7), self.designs)
        counts = [sum(item["page"] == page for item in packed) for page in (1, 2)]
        self.assertEqual(sorted(counts), [3, 4])

    def test_short_reports_remain_short_without_inventing_components(self):
        for texts, graphics in ((1, 0), (1, 1), (2, 1), (3, 2)):
            plan = components(texts, graphics)
            self.assertEqual(len(compact_report_pages(plan, self.designs)), len(plan))

    def test_summary_and_conclusion_stay_at_the_ends_when_pages_merge(self):
        plan = components(2, 1, 1) + components(2, 1, 2, offset=3)
        plan[1]["id"] = "kpi_resumo"
        plan[-2]["id"] = "kpi_conclusao"
        packed = compact_report_pages(plan, self.designs)
        self.assertEqual(packed[0]["id"], "kpi_resumo")
        self.assertEqual(packed[-1]["id"], "kpi_conclusao")
        self.assertEqual(packed[0]["page"], 1)
        self.assertEqual(packed[-1]["page"], max(item["page"] for item in packed))

    def test_empty_catalog_and_unsupported_types_use_a_bounded_grid(self):
        self.assertEqual(len({item["page"] for item in compact_report_pages(components(0, 8), [])}), 2)
        narrative_designs = [item for item in self.designs if item["graphic_slots"] == 0]
        packed = compact_report_pages(components(0, 7), narrative_designs)
        self.assertEqual(len(packed), 7)
        self.assertEqual(len({item["page"] for item in packed}), 2)

    def test_page_bases_follow_the_content_mix_and_valid_ai_choices(self):
        narrative = select_page_design(components(4, 0), self.designs)
        comparison = select_page_design(components(4, 2, layout_id="comparison_split:1"), self.designs)
        dashboard = select_page_design(components(2, 4, layout_id="executive_standard:1"), self.designs)
        self.assertEqual(narrative["id"], "document_text_only:1")
        self.assertEqual(comparison["id"], "comparison_split:1")
        self.assertEqual(dashboard["id"], "executive_standard:1")

    def test_catalog_passes_slot_dimensions_to_the_ai(self):
        with patch("app.utils.template_library.load_layout_templates", return_value=self.templates) as load:
            catalog = page_layout_catalog("report-owner")
        load.assert_called_once_with("report-owner")
        self.assertTrue(all({"x", "y", "w", "h"}.issubset(slot) for design in catalog for slot in design["slots"]))
        self.assertIn("research_brief:2", {design["id"] for design in catalog})

    def test_planner_uses_catalog_and_consolidates_sparse_ai_proposals(self):
        plan = [components(1, 0, page, page)[0] for page in range(1, 9)]
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(plan)) as llm:
            result = generate_artifacts("{}", "Analyze performance", page_layouts=self.designs)
        self.assertEqual(len({item["page"] for item in result}), 2)
        self.assertTrue(all(item.get("page_layout_id") for item in result))
        self.assertIn("Available Page Layouts", llm.call_args.args[3])

    def test_planner_retries_unknown_or_malformed_template_ids(self):
        for invalid in ("invented:1", ["document_text_only:1"]):
            plan = components(2, 0)
            plan[0]["page_layout_id"] = invalid
            with patch("app.services.llm.client.call_llm", side_effect=[json.dumps(plan), json.dumps(components(2, 0))]) as llm:
                generate_artifacts("{}", "Analyze", page_layouts=self.designs)
            self.assertEqual(llm.call_count, 2)

    def test_fixed_length_reports_keep_their_pages(self):
        plan = components(1, 0, 1) + components(1, 0, 2, offset=1) + components(1, 0, 3, offset=2)
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(plan)):
            result = generate_artifacts("{}", "Analyze", settings={"page_count": 3}, page_layouts=self.designs)
        self.assertEqual([item["page"] for item in result], [1, 2, 3])

    def test_template_capacity_cannot_push_a_large_plan_past_the_page_limit(self):
        packed = compact_report_pages(components(300, 0), self.designs)
        self.assertEqual(len(packed), 300)
        self.assertEqual(max(item["page"] for item in packed), 50)

    def test_layout_uses_different_templates_on_different_fixed_pages(self):
        plan = components(4, 2, 1, layout_id="comparison_split:1")
        plan += components(2, 4, 2, offset=6, layout_id="executive_standard:1")
        with patch("app.utils.template_library.load_layout_templates", return_value=self.templates):
            layout = build_layout_from_template(plan, page_count=2)
        self.assertEqual({item["page_layout_id"] for item in layout if item["page"] == 1}, {"comparison_split:1"})
        self.assertEqual({item["page_layout_id"] for item in layout if item["page"] == 2}, {"executive_standard:1"})
        self.assertEqual(len(layout), len(plan))
        for item in layout:
            self.assertGreaterEqual(item["y"], 30)
            self.assertLessEqual(item["y"] + item["h"], 285)

    def test_unused_template_bands_and_columns_are_reclaimed(self):
        plan = components(2, 0, layout_id="executive_standard:1")
        plan[0]["id"] = "kpi_resumo"
        plan[1]["id"] = "kpi_conclusao"
        with patch("app.utils.template_library.load_layout_templates", return_value=self.templates):
            layout = build_layout_from_template(plan, page_count=1)
        first, last = sorted(layout, key=lambda item: item["y"])
        self.assertEqual(first["y"], 30)
        self.assertEqual(last["y"] + last["h"], 285)
        self.assertLess(last["y"] - first["y"] - first["h"], 20)
        self.assertTrue(all(item["w"] == 190 for item in layout))

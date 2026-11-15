"""A visual that keeps failing is left out of the report with a review warning."""
import json
import unittest
import uuid
from unittest.mock import patch

from app.services import pipeline_service
from app.services.cleanup_service import delete_entire_session
from app.services.executor_service import probe_bubblewrap
from app.services.pipeline_service import omit_components_payload, run_pipeline
from app.services.session_manager import get_session
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import create_session, default_user

BWRAP_AVAILABLE, BWRAP_ERROR = probe_bubblewrap()

TEXT = {"id": "kpi_resumo", "type": "text", "indicator": "Summary"}
CHART = {"id": "g_bad", "type": "graphic", "indicator": "Broken chart"}


class OmitPayloadTests(unittest.TestCase):
    def session(self):
        return {"artifacts": [TEXT, CHART, {"id": "m_total", "type": "metric"}],
                "template": [{"id": "kpi_resumo", "x": 10, "y": 10, "w": 90, "h": 40, "page": 1},
                             {"id": "g_bad", "x": 10, "y": 60, "w": 90, "h": 40, "page": 1}],
                "components": {"kpi_resumo": "code", "g_bad": "code"}}

    def test_failing_visuals_leave_the_plan_and_are_recorded(self):
        payload = omit_components_payload(self.session(), {"g_bad": "render_error"})
        self.assertEqual([a["id"] for a in payload["artifacts"]], ["kpi_resumo", "m_total"])
        self.assertEqual([slot["id"] for slot in payload["template"]], ["kpi_resumo"])
        self.assertEqual(payload["components"], {"kpi_resumo": "code"})
        self.assertEqual(payload["omitted_components"], [{"id": "g_bad", "indicator": "Broken chart", "reason": "render_error",
                                                          "artifact": CHART}])

    def test_text_components_and_whole_reports_are_never_omitted(self):
        self.assertIsNone(omit_components_payload(self.session(), {"kpi_resumo": "render_error"}))
        only_chart = {**self.session(), "template": [{"id": "g_bad", "x": 10, "y": 10, "w": 90, "h": 40, "page": 1}]}
        self.assertIsNone(omit_components_payload(only_chart, {"g_bad": "empty"}))

    def test_at_most_half_of_the_planned_visuals_are_omitted(self):
        charts = [{"id": f"g_{i}", "type": "graphic", "indicator": f"Chart {i}"} for i in range(4)]
        session = {"artifacts": [TEXT] + charts, "components": {},
                   "template": [{"id": a["id"], "x": 10, "y": 10 + 20 * i, "w": 90, "h": 15, "page": 1}
                                for i, a in enumerate([TEXT] + charts)]}
        self.assertIsNone(omit_components_payload(session, {"g_0": "x", "g_1": "x", "g_2": "x"}))
        first = omit_components_payload(session, {"g_0": "x"})
        self.assertIsNotNone(first)
        second = omit_components_payload({**session, **first}, {"g_1": "x"})
        self.assertEqual(len(second["omitted_components"]), 2)
        self.assertIsNone(omit_components_payload({**session, **second}, {"g_2": "x"}))


class OmissionReviewTests(unittest.TestCase):
    def test_review_names_each_omitted_component_and_its_cause(self):
        from app.services.report_quality import review_report
        session = {"omitted_components": [{"id": "g_a", "indicator": "Chart A", "reason": "blank"},
                                          {"id": "g_b", "indicator": "Chart B", "reason": "empty"}]}
        issues = [i for i in review_report(session, "/missing.pdf", {"components": []})["issues"]
                  if i["code"] == "component_omitted"]
        self.assertEqual([i["severity"] for i in issues], ["warning", "warning"])
        self.assertIn("Chart A", issues[0]["message"])
        self.assertIn("em branco", issues[0]["message"])
        self.assertIn("sem dados", issues[1]["message"])

@unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
class OmissionPipelineTests(unittest.TestCase):
    def test_report_is_published_without_the_chart_that_keeps_failing(self):
        job_id = f"test-omit-{uuid.uuid4().hex}"
        paths = ReportPaths.for_new_report(job_id, str(default_user().id)).ensure()
        csv = paths.uploads / "sales.csv"
        csv.write_text("region,revenue\nNorth,10\nSouth,20\n", encoding="utf-8")
        create_session(job_id, {"files": [{"original_filename": "sales.csv", "path": str(csv)}],
                                "initial_prompt": "Revenue by region", "provider": "deepseek",
                                "model_name": "deepseek-flash", **paths.session_fields()})
        self.addCleanup(delete_entire_session, job_id)
        plan = [{**TEXT, "page": 1}, {"id": "kpi_conclusao", "type": "text", "indicator": "Conclusion", "page": 1},
                {**CHART, "page": 1, "render_mode": "generated_code", "columns": ["sales.csv:revenue"]},
                {"id": "m_total", "type": "metric", "indicator": "Total", "columns": ["sales.csv:revenue"]}]
        fixes = []

        def fake_llm(provider, model_name, system, prompt, **kwargs):
            if "Data Preprocessing Planner" in system:
                return json.dumps({"needs_preprocessing": False})
            if "Report Planning & Indicator Selection" in system:
                return json.dumps(plan)
            if "Python Component Developer" in system:
                return "def render_g_bad(pdf, dataframes, x, y, w, h):\n    raise ValueError('boom')\n"
            if "Python Autocorrection" in system:
                fixes.append(1)
                return prompt.split("Current Failing Script:\n", 1)[1].split("\n\nData Artifacts:", 1)[0]
            if "Executive Copywriter" in system:
                return json.dumps({"kpi_resumo": "North and South.", "kpi_conclusao": "South leads."})
            if "title" in system.lower():
                return "Revenue"
            return 'import json\nprint(json.dumps({"value": 30}))'

        with patch("app.services.llm.client.call_llm", side_effect=fake_llm):
            run_pipeline(job_id)
        session = get_session(job_id)
        self.assertEqual(session["current_step"], "completed", session.get("error"))
        self.assertEqual(len(fixes), pipeline_service.COMPONENT_FIX_ATTEMPTS)
        self.assertEqual([item["id"] for item in session["omitted_components"]], ["g_bad"])
        self.assertNotIn("g_bad", {slot["id"] for slot in session["template"]})
        codes = [issue["code"] for issue in session["quality_review"]["issues"]]
        self.assertIn("component_omitted", codes)


if __name__ == "__main__":
    unittest.main()

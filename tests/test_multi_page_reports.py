import asyncio
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pypdf import PdfReader

from app.main import app
from app.models.session import ReportSettings
from app.routers import api
from app.services.cleanup_service import delete_entire_session
from app.services.executor_service import probe_bubblewrap
from app.services.llm.errors import LLMContractError
from app.services.llm.planning import generate_artifacts
from app.services.pipeline_service import run_pipeline
from app.services.report_quality import review_report
from app.services.session_manager import get_session, merge_report_settings
from app.utils.layout import normalize_template
from app.utils.pagination import paginate_artifacts
from app.utils.report_paths import ReportPaths
from app.utils.template_library import build_layout_from_template
from tests.auth_helpers import create_session, default_user, request_as, signed_in_headers


def text_plan(pages):
    return [
        {"id": "kpi_resumo" if page == 1 else "kpi_conclusao" if page == pages else f"analysis_{page}",
         "indicator": f"Section {page}", "type": "text", "page": page}
        for page in range(1, pages + 1)
    ]


class PaginationTests(unittest.TestCase):
    def test_settings_accept_automatic_and_integer_counts_only(self):
        for value in (None, 1, 3, 50):
            self.assertEqual(ReportSettings(page_count=value).page_count, value)
        for value in (0, -1, 51, 2.5, True, "3"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ReportSettings(page_count=value)

    def test_rejects_gaps_invalid_pages_and_incorrect_requested_length(self):
        invalid_plans = [
            ([{"id": "a", "type": "text", "page": value}], None)
            for value in (0, -1, 51, 1.5, True, "1")
        ]
        invalid_plans.extend([
            (text_plan(2), 3), (text_plan(3), 2),
            ([{"id": "a", "page": 1}, {"id": "b", "page": 3}], None),
            ([{"id": "a", "page": 1}, {"id": "b"}], None),
            ([{"id": f"a_{i}", "page": 1} for i in range(7)], 1),
        ])
        for plan, requested in invalid_plans:
            with self.subTest(plan=plan, requested=requested), self.assertRaises(ValueError):
                paginate_artifacts(plan, requested)

    def test_metrics_do_not_occupy_pages_and_plan_is_not_mutated(self):
        plan = text_plan(3) + [{"id": "total", "type": "metric"}]
        self.assertEqual(paginate_artifacts(plan, 3), text_plan(3))
        self.assertNotIn("page", plan[-1])
        legacy = [{"id": "a", "type": "text"}, {"id": "b", "type": "text"}]
        self.assertEqual([item["page"] for item in paginate_artifacts(legacy, 2)], [1, 2])
        self.assertNotIn("page", legacy[0])

    def test_ai_page_assignments_and_length_are_passed_through(self):
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(text_plan(3))) as llm:
            result = generate_artifacts("{}", "Analyze the supplied data")
        self.assertEqual([item["page"] for item in result], [1, 2, 3])
        self.assertIn("Report Length: automatic", llm.call_args.args[3])

    def test_planner_retries_an_incomplete_fixed_length_plan(self):
        with patch("app.services.llm.client.call_llm", side_effect=[json.dumps(text_plan(2)), json.dumps(text_plan(3))]) as llm:
            result = generate_artifacts("{}", "Analyze", settings={"page_count": 3})
        self.assertEqual(len(result), 3)
        self.assertEqual(llm.call_count, 2)
        self.assertIn("Report Length: exactly 3 pages", llm.call_args.args[3])
        self.assertIn("Correct the previous response", llm.call_args.args[3])

    def test_planner_fails_after_invalid_page_retries(self):
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(text_plan(2))) as llm:
            with self.assertRaises(LLMContractError) as error:
                generate_artifacts("{}", "Analyze", settings={"page_count": 3})
        self.assertEqual(llm.call_count, 3)
        self.assertEqual(error.exception.field, "page")


class MultiPageLayoutTests(unittest.TestCase):
    def assert_valid_layout(self, layout, artifacts, pages):
        self.assertEqual({item["page"] for item in layout}, set(range(1, pages + 1)))
        self.assertEqual(sorted(item["id"] for item in layout), sorted(item["id"] for item in artifacts))
        self.assertEqual(layout, normalize_template(layout))
        for index, item in enumerate(layout):
            for other in layout[index + 1:]:
                overlap = (item["page"] == other["page"] and item["x"] < other["x"] + other["w"]
                           and other["x"] < item["x"] + item["w"] and item["y"] < other["y"] + other["h"]
                           and other["y"] < item["y"] + item["h"])
                self.assertFalse(overlap, (item, other))

    def test_repeats_a_single_page_blueprint_across_requested_pages(self):
        plan = text_plan(3)
        layout = build_layout_from_template(plan, "executive_standard", page_count=3)
        self.assert_valid_layout(layout, plan, 3)
        self.assertEqual(layout[0]["y"], 30)
        self.assertEqual(layout[-1]["y"], 235)

    def test_preserves_ai_selected_pages_without_a_user_count(self):
        plan = text_plan(4)
        layout = build_layout_from_template(plan, "document_text_only")
        self.assert_valid_layout(layout, plan, 4)

    def test_grid_adapts_when_a_blueprint_has_too_few_matching_slots(self):
        plan = [{"id": f"text_{i}", "type": "text", "page": page}
                for page in (1, 2) for i in range((page - 1) * 6, page * 6)]
        layout = build_layout_from_template(plan, "executive_standard", page_count=2)
        self.assert_valid_layout(layout, plan, 2)
        self.assertGreaterEqual(min(item["h"] for item in layout), 80)

    def test_a_multi_page_blueprint_can_be_adapted_to_one_requested_page(self):
        plan = [{"id": "summary", "type": "text", "page": 1},
                {"id": "chart", "type": "graphic", "page": 1}]
        layout = build_layout_from_template(plan, "research_brief", page_count=1)
        self.assert_valid_layout(layout, plan, 1)


class PageCountRouteTests(unittest.TestCase):
    def new_report(self, page_count=2):
        job_id = f"test-pages-{uuid.uuid4().hex}"
        create_session(job_id, {"settings": {"page_count": page_count, "title": "Keep this title"},
                                "artifacts": text_plan(2), "components": {"kpi_resumo": "cached"},
                                "template": [{"id": "kpi_resumo", "page": 1}]})
        self.addCleanup(delete_entire_session, job_id)
        return job_id

    def test_initial_form_persists_count_or_automatic_choice(self):
        with patch.object(api, "run_pipeline"), TestClient(app) as client:
            headers = signed_in_headers()
            for count in ("3", "", None):
                data = {"provider": "deepseek"}
                if count is not None:
                    data["page_count"] = count
                response = client.post("/api/sessions", data=data, headers=headers,
                                       files={"files": ("data.csv", b"region,revenue\nA,10\n", "text/csv")})
                self.assertEqual(response.status_code, 200, response.text)
                job_id = response.json()["job_id"]
                self.addCleanup(delete_entire_session, job_id)
                self.assertEqual(get_session(job_id)["settings"]["page_count"], 3 if count else None)

    def test_invalid_initial_count_is_rejected_before_creating_report(self):
        with patch.object(api, "create_session") as create, TestClient(app) as client:
            for count in ("0", "-1", "51", "1.5", "invalid"):
                response = client.post("/api/sessions", data={"page_count": count}, headers=signed_in_headers())
                self.assertEqual(response.status_code, 422, response.text)
            create.assert_not_called()

    def test_length_change_and_return_to_automatic_replan_in_background(self):
        for count in (3, None):
            job_id = self.new_report()
            tasks = BackgroundTasks()
            asyncio.run(api.update_settings(request_as(), job_id, tasks, {"page_count": count}))
            saved = get_session(job_id)
            self.assertEqual(saved["settings"]["page_count"], count)
            self.assertEqual(saved["settings"]["title"], "Keep this title")
            self.assertEqual(saved["artifacts"], [])
            self.assertEqual(tasks.tasks[0].args[0], api.run_pipeline)

    def test_partial_regeneration_with_a_new_length_replans_everything(self):
        job_id = self.new_report()
        tasks = BackgroundTasks()
        asyncio.run(api.regenerate_session(request_as(), job_id, tasks, {"scope": "texts", "settings": {"page_count": 3}}))
        saved = get_session(job_id)
        self.assertEqual(saved["settings"]["page_count"], 3)
        self.assertEqual(saved["artifacts"], [])
        self.assertEqual(saved["template"], [])

    def test_settings_reject_invalid_count_without_mutation(self):
        job_id = self.new_report()
        for count in (0, 51, 1.5, True, "2"):
            with self.subTest(count=count), self.assertRaises(HTTPException) as error:
                asyncio.run(api.update_settings(request_as(), job_id, BackgroundTasks(), {"page_count": count}))
            self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(get_session(job_id)["settings"]["page_count"], 2)

    def test_settings_merge_keeps_other_fields_and_same_length_plan(self):
        job_id = self.new_report()
        merge_report_settings(job_id, {"font": "Courier"})
        saved = merge_report_settings(job_id, {"page_count": 2})
        self.assertEqual(saved["settings"]["font"], "Courier")
        self.assertTrue(saved["artifacts"])

    def test_manual_layout_cannot_exceed_fixed_length(self):
        job_id = self.new_report()
        with self.assertRaises(HTTPException) as error:
            asyncio.run(api.update_template_manually(request_as(), job_id, BackgroundTasks(),
                        [{"id": "a", "page": 1}, {"id": "b", "page": 3}]))
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(len(get_session(job_id)["template"]), 1)


BWRAP_AVAILABLE, BWRAP_ERROR = probe_bubblewrap()


@unittest.skipUnless(BWRAP_AVAILABLE, f"bubblewrap unavailable: {BWRAP_ERROR}")
class MultiPagePipelineTests(unittest.TestCase):
    def test_fixed_and_ai_selected_reports_render_real_pdfs_with_content_on_every_page(self):
        for requested, expected in ((3, 3), (None, 2)):
            with self.subTest(requested=requested):
                job_id = f"test-pages-pipeline-{uuid.uuid4().hex}"
                paths = ReportPaths.for_new_report(job_id, str(default_user().id)).ensure()
                source = paths.uploads / "data.csv"
                source.write_text("region,revenue\nNorth,10\nSouth,20\n", encoding="utf-8")
                plan = text_plan(expected)
                narratives = list(plan)
                texts = {item["id"]: f"Evidence and findings for {item['indicator']}." for item in narratives}
                plan.append({"id": "regional_revenue", "type": "graphic", "indicator": "Regional revenue", "page": 2,
                             "component_preset_id": "bar_ranked", "render_mode": "library",
                             "data_binding": {"source": "data.csv", "roles": {"category": "region", "value": "revenue"},
                                              "aggregation": "sum"}})
                create_session(job_id, {"files": [{"original_filename": "data.csv", "path": str(source)}],
                                       "initial_prompt": "Analyze regional performance",
                                       "settings": {"page_count": requested, "title": "Multi page report"},
                                       **paths.session_fields()})
                self.addCleanup(delete_entire_session, job_id)

                def fake_llm(provider, model_name, system, prompt, **kwargs):
                    if "Data Preprocessing Planner" in system:
                        return '{"needs_preprocessing": false}'
                    if "Report Planning & Indicator Selection" in system:
                        return json.dumps(plan)
                    if "Executive Copywriter" in system:
                        return json.dumps(texts)
                    self.fail(f"Unexpected model call: {system[:80]}")

                with patch("app.services.llm.client.call_llm", side_effect=fake_llm):
                    run_pipeline(job_id)
                session = get_session(job_id)
                self.assertEqual(session["current_step"], "completed", session.get("error"))
                reader = PdfReader(session["pdf_path"])
                self.assertEqual(len(reader.pages), expected)
                for page, artifact in zip(reader.pages, narratives):
                    self.assertIn(texts[artifact["id"]], " ".join(page.extract_text().split()))
                self.assertIn("/XObject", reader.pages[1]["/Resources"])
                manifest = json.loads(Path(session["pdf_path"] + ".manifest.json").read_text())
                self.assertEqual(manifest["page_count"], expected)
                self.assertEqual({item["id"]: item["page"] for item in manifest["components"]},
                                 {item["id"]: item["page"] for item in plan})
                # Publication review must catch an incorrect length, including repaired scripts.
                session["settings"]["page_count"] = expected + 1
                review = review_report(session, session["pdf_path"], manifest)
                self.assertEqual(review["status"], "failed")
                self.assertIn("page_count_mismatch", [item["code"] for item in review["issues"]])

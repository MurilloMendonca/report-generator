import hashlib
import json
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.services.cleanup_service import delete_entire_session
from app.services.component_service import component_text
from app.services.pipeline_service import run_script_generation, text_component_code
from app.services.session_manager import get_session, recover_interrupted_sessions, update_session
from app.utils.data_analyzer import extract_metadata
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import create_session, default_user, signed_in_headers


class ComponentEditorTests(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-component-editor-{uuid.uuid4().hex}"
        self.paths = ReportPaths.for_new_report(self.job_id, str(default_user().id)).ensure()
        source = self.paths.uploads / "sales.csv"
        source.write_text("region,revenue\nNorth,10\nSouth,20\n")
        self.text = {"id": "summary", "type": "text", "indicator": "Summary", "columns": []}
        self.chart = {"id": "sales", "type": "bar", "indicator": "Revenue by region", "render_mode": "library", "visual_theme_id": "executive_clean",
                      "component_preset_id": "bar_ranked", "columns": ["sales.csv:region", "sales.csv:revenue"],
                      "data_binding": {"source": "sales.csv", "roles": {"category": "region", "value": "revenue"}, "aggregation": "sum"}}
        create_session(self.job_id, {
            "job_id": self.job_id, **self.paths.session_fields(), "initial_prompt": "Analyze revenue", "current_goal": "Analyze revenue",
            "provider": "deepseek", "model_name": "deepseek-flash", "settings": {"title": "Revenue report"},
            "files": [{"original_filename": "sales.csv", "path": str(source), "artifact": extract_metadata(str(source), "sales.csv")}],
            "artifacts": [self.text, self.chart], "components": {"summary": text_component_code(self.text, "Revenue totals 30.")},
            "calculated_metrics": {"total": 30},
            "template": [{"id": "summary", "page": 1, "x": 10, "y": 30, "w": 190, "h": 40},
                         {"id": "sales", "page": 1, "x": 10, "y": 80, "w": 190, "h": 100}],
        })
        self.addCleanup(delete_entire_session, self.job_id)
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("Initial render needs no model")):
            run_script_generation(self.job_id)
        self.before = get_session(self.job_id)
        self.assertEqual(self.before["current_step"], "completed")
        self.headers = signed_in_headers()

    def edit(self, component_id, payload):
        with TestClient(app) as client:
            return client.post(f"/api/sessions/{self.job_id}/components/{component_id}/edit", json=payload, headers=self.headers)

    def assert_preserved_context(self, after):
        self.assertEqual(after["template"], self.before["template"])
        self.assertEqual(after["calculated_metrics"], self.before["calculated_metrics"])
        self.assertEqual(after["current_goal"], self.before["current_goal"])
        self.assertEqual(after["settings"], self.before["settings"])

    def test_manual_text_edit_renders_without_model_and_preserves_layout_and_chart(self):
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("Manual text edits need no model")):
            response = self.edit("summary", {"text": 'Revenue totals 30. Say "hello".', "base_pdf_version": self.before["pdf_version"]})
        self.assertEqual(response.status_code, 202, response.text)
        after = get_session(self.job_id)
        self.assertEqual(after["component_edit"]["status"], "completed", after.get("error"))
        self.assertEqual(component_text(after["components"]["summary"]), 'Revenue totals 30. Say "hello".')
        self.assertEqual(after["artifacts"][1], self.before["artifacts"][1])
        self.assertEqual(after["pdf_version"], self.before["pdf_version"] + 1)
        self.assert_preserved_context(after)
        with TestClient(app) as client:
            self.assertEqual(client.get(f"/api/sessions/{self.job_id}", headers=self.headers).json()["component_texts"]["summary"], 'Revenue totals 30. Say "hello".')

    def test_ai_text_revision_uses_current_draft_evidence_and_only_selected_text(self):
        with patch("app.services.llm.client.call_llm", return_value='{"summary":"Revenue is 30."}') as llm:
            self.edit("summary", {"instruction": "Make the text concise.", "text": "Revenue totals 30. This is a draft."})
        llm.assert_called_once()
        self.assertIn("Make the text concise.", llm.call_args.args[3])
        self.assertIn("This is a draft.", llm.call_args.args[3])
        self.assertIn('"total": 30', llm.call_args.args[3])
        after = get_session(self.job_id)
        self.assertEqual(component_text(after["components"]["summary"]), "Revenue is 30.")
        self.assertEqual(after["artifacts"][1], self.before["artifacts"][1])
        self.assert_preserved_context(after)

    def test_chart_option_edit_uses_one_model_call_and_cannot_change_other_components(self):
        decision = {"intent": "visual", "scope": "components", "component_ids": ["sales"],
                    "instruction": "Show the chart values.", "visual_options": {"show_values": True}}
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(decision)) as llm:
            self.edit("sales", {"instruction": "Show values on this chart."})
        llm.assert_called_once()
        context = json.loads(llm.call_args.args[3].split("\n\nDecision JSON schema:")[0])
        self.assertEqual(context["selected_component_id"], "sales")
        after = get_session(self.job_id)
        self.assertEqual(after["component_edit"]["status"], "completed", after.get("error"))
        self.assertTrue(after["artifacts"][1]["visual_options"]["show_values"])
        self.assertEqual(after["components"]["summary"], self.before["components"]["summary"])
        self.assert_preserved_context(after)

    def test_scope_escape_fails_without_changing_editable_report_or_pdf(self):
        digest = hashlib.sha256((self.paths.output / f"{self.job_id}.pdf").read_bytes()).hexdigest()
        decision = {"intent": "visual", "scope": "report", "instruction": "Change every chart.", "settings_changes": {"visual_theme_id": "print_mono"}}
        with patch("app.services.llm.client.call_llm", return_value=json.dumps(decision)):
            self.edit("sales", {"instruction": "Improve this chart."})
        after = get_session(self.job_id)
        self.assertEqual(after["component_edit"]["status"], "failed")
        self.assertEqual(after["artifacts"], self.before["artifacts"])
        self.assertEqual(after["components"], self.before["components"])
        self.assertEqual(after["pdf_version"], self.before["pdf_version"])
        self.assertEqual(hashlib.sha256((self.paths.output / f"{self.job_id}.pdf").read_bytes()).hexdigest(), digest)
        self.assert_preserved_context(after)

    def test_stale_version_and_overlapping_edits_are_rejected(self):
        self.assertEqual(self.edit("summary", {"text": "Stale", "base_pdf_version": 0}).status_code, 409)
        with patch("app.routers.api.run_component_edit"):
            self.assertEqual(self.edit("summary", {"text": "First edit"}).status_code, 202)
            self.assertEqual(self.edit("sales", {"instruction": "Another edit"}).status_code, 409)
        recover_interrupted_sessions()
        self.assertEqual(get_session(self.job_id)["component_edit"]["status"], "failed")
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("No model")):
            self.assertEqual(self.edit("summary", {"text": "Recovered draft."}).status_code, 202)
        self.assertEqual(get_session(self.job_id)["component_edit"]["status"], "completed")

    def test_plain_chart_controls_render_without_a_model(self):
        artifact = {**self.before["artifacts"][1], "visual_options": {"top_n": 1, "show_values": True}}
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("Library controls need no model")):
            self.edit("sales", {"artifact": artifact})
        after = get_session(self.job_id)
        self.assertEqual(after["component_edit"]["status"], "completed", after.get("error"))
        self.assertEqual(after["artifacts"][1]["visual_options"]["top_n"], 1)
        self.assert_preserved_context(after)

    def test_empty_manual_text_is_rejected_without_a_provider_call(self):
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("Empty edits must not use a model")) as llm:
            self.edit("summary", {"text": "   "})
        llm.assert_not_called()
        after = get_session(self.job_id)
        self.assertEqual(after["component_edit"]["status"], "failed")
        self.assertEqual(after["components"], self.before["components"])
        self.assertEqual(after["pdf_version"], self.before["pdf_version"])

    def test_component_theme_can_return_to_global_without_a_model_call(self):
        artifact = {**self.before["artifacts"][1], "visual_theme_id": None}
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("Theme controls need no model")):
            self.edit("sales", {"artifact": artifact})
        after = get_session(self.job_id)
        self.assertEqual(after["component_edit"]["status"], "completed", after.get("error"))
        self.assertIsNone(after["artifacts"][1].get("visual_theme_id"))
        self.assert_preserved_context(after)

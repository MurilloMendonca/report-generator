import hashlib
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from tests.auth_helpers import signed_in_headers
from app.services.pipeline_service import run_script_generation, text_component_code
from app.services.session_manager import (
    delete_session, get_session,
)
from tests.auth_helpers import create_session


class RefinementHttpTests(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-refinement-http-{uuid.uuid4().hex}"
        self.paths = [
            f"workspace/uploads/{self.job_id}.csv", f"workspace/scripts/{self.job_id}.py",
            f"workspace/reports/{self.job_id}.pdf", f"workspace/reports/{self.job_id}.pdf.manifest.json",
        ]
        for path in self.paths:
            self.addCleanup(self.remove_if_present, path)
        with open(self.paths[0], "w", encoding="utf-8") as upload:
            upload.write("region,revenue\nNorth,10\nSouth,20\n")
        text = {"id": "t_summary", "type": "text", "indicator": "Summary", "columns": []}
        chart = {
            "id": "g_sales", "type": "graphic", "indicator": "Revenue by region",
            "columns": ["sales.csv:region", "sales.csv:revenue"],
            "component_preset_id": "bar_ranked", "render_mode": "library",
            "data_binding": {"source": "sales.csv", "roles": {"category": "region", "value": "revenue"}, "aggregation": "sum", "filters": []},
            "visual_options": {"show_values": True},
        }
        create_session(self.job_id, {
            "job_id": self.job_id, "initial_prompt": "Analyze revenue", "current_goal": "Analyze revenue",
            "settings": {"title": "Sales report"},
            "files": [{"original_filename": "sales.csv", "path": self.paths[0], "artifact": {
                "filename": "sales.csv", "columns": [
                    {"name": "region", "type": "object", "role": "dimension"},
                    {"name": "revenue", "type": "int64", "role": "measure"},
                ],
            }}],
            "artifacts": [text, chart], "components": {"t_summary": text_component_code(text, "Revenue totals 30.")},
            "calculated_metrics": {"total": 30},
            "template": [
                {"id": "t_summary", "page": 1, "x": 10, "y": 30, "w": 190, "h": 30},
                {"id": "g_sales", "page": 1, "x": 10, "y": 70, "w": 190, "h": 100},
            ],
        })
        with patch("app.services.llm.client.call_llm", side_effect=AssertionError("Initial render must not use a model")):
            run_script_generation(self.job_id)
        self.assertEqual(get_session(self.job_id)["current_step"], "completed")

    @staticmethod
    def remove_if_present(path):
        if os.path.exists(path):
            os.remove(path)

    def test_chat_routes_through_model_clarifies_safely_and_renders_real_pdf(self):
        before = get_session(self.job_id)
        endpoint = f"/api/sessions/{self.job_id}"
        headers = signed_in_headers()
        with TestClient(app) as client, \
             patch("app.services.llm.client.call_llm") as llm, \
             patch("app.services.pipeline_service.generate_artifacts", side_effect=AssertionError("Styling must not replan")):
            llm.return_value = json.dumps({
                "intent": "visual", "scope": "report", "instruction": "Use grayscale for printing.",
                "settings_changes": {"visual_theme_id": "print_mono", "chart_palette": []},
            })
            response = client.post(endpoint + "/chat", data={"prompt": "Use only black and white"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            llm.assert_called_once()
            styled = client.get(endpoint, headers=headers).json()
            self.assertEqual(styled["current_step"], "completed")
            self.assertEqual(styled["pdf_version"], before["pdf_version"] + 1)
            self.assertEqual(styled["template"], before["template"])
            self.assertEqual(styled["components"]["t_summary"], before["components"]["t_summary"])
            self.assertEqual(styled["calculated_metrics"], {"total": 30})
            pdf = client.get(endpoint + "/pdf", headers=headers)
            self.assertEqual(pdf.status_code, 200)
            self.assertTrue(pdf.content.startswith(b"%PDF-"))
            digest = hashlib.sha256(pdf.content).hexdigest()

            llm.reset_mock()
            llm.return_value = json.dumps({
                "intent": "clarify", "scope": "report", "instruction": "Improve the report.",
                "clarification_question": "Do you want to change the layout or the content?",
            })
            client.post(endpoint + "/chat", data={"prompt": "Improve this"}, headers=headers)
            waiting = client.get(endpoint, headers=headers).json()
            llm.assert_called_once()
            self.assertEqual(waiting["current_step"], "awaiting_clarification")
            self.assertEqual(waiting["pdf_version"], styled["pdf_version"])
            self.assertEqual(waiting["current_goal"], styled["current_goal"])
            self.assertEqual(hashlib.sha256(client.get(endpoint + "/pdf", headers=headers).content).hexdigest(), digest)

            llm.reset_mock()
            llm.return_value = json.dumps({
                "intent": "layout", "scope": "components", "component_ids": ["g_sales"],
                "instruction": "Move the revenue chart down by 5 mm.",
                "layout_updates": [{"id": "g_sales", "y": 75}],
            })
            client.post(endpoint + "/chat", data={"prompt": "Move the chart down by 5 mm"}, headers=headers)
            completed = client.get(endpoint, headers=headers).json()
            llm.assert_called_once()
            routing_context = json.loads(llm.call_args.args[3].split("\n\nDecision JSON schema:")[0])
            self.assertEqual(routing_context["pending_clarification"]["prompt"], "Improve this")
            self.assertEqual(completed["current_step"], "completed")
            self.assertEqual(completed["pdf_version"], styled["pdf_version"] + 1)
            self.assertEqual(completed["template"][1]["y"], 75)
            self.assertEqual(completed["pending_refinement"], {})
            self.assertNotIn("Improve this", completed["current_goal"])
            manifest = json.loads(Path(completed["pdf_path"] + ".manifest.json").read_text())
            self.assertTrue(all(item["status"] == "rendered" for item in manifest["components"]))

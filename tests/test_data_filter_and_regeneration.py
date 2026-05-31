import asyncio
import os
import uuid
import unittest

import pandas as pd
from fastapi import BackgroundTasks, HTTPException

from app.main import (
    apply_data_filter,
    build_files_map,
    clear_data_filter_state,
    has_data_filter_decision,
    regenerate_session,
    should_consider_data_filter,
)
from app.services.session_manager import create_session, get_session, get_session_path


class DataFilterTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []

    def tearDown(self):
        for path in self.created_paths:
            if path and os.path.exists(path):
                os.remove(path)

    def test_detects_filter_intent_in_portuguese_and_english(self):
        self.assertTrue(should_consider_data_filter("quero excluir pokemon variantes de região"))
        self.assertTrue(should_consider_data_filter("focus on enterprise accounts only"))
        self.assertTrue(should_consider_data_filter('Não inclua nenhuma forma regional ou mega. É possível identificar isso pela presença de um "-" no nome do pokemon'))
        self.assertTrue(should_consider_data_filter("", {"data_filter": {"needs_filter": True}}))
        self.assertFalse(should_consider_data_filter("gere um relatório exploratório dos dados"))

    def test_default_filter_state_is_not_treated_as_existing_decision(self):
        self.assertFalse(has_data_filter_decision({}))
        self.assertFalse(has_data_filter_decision({
            "needs_filter": False,
            "description": "",
            "code": "",
        }))
        self.assertTrue(has_data_filter_decision({
            "needs_filter": False,
            "description": "Não filtrar estes dados",
            "code": "",
        }))

    def test_applies_filter_once_and_preserves_original_upload(self):
        source_path = f"workspace/uploads/filter_test_{uuid.uuid4().hex}.csv"
        pd.DataFrame({
            "name": ["Pikachu", "Alolan Raichu", "Bulbasaur"],
            "variant": ["base", "regional", "base"],
            "value": [1, 2, 3],
        }).to_csv(source_path, index=False)
        self.created_paths.append(source_path)

        session = {
            "files": [{
                "original_filename": "pokemon.csv",
                "path": source_path,
                "raw_artifact": {"filename": "pokemon.csv"},
                "artifact": {"filename": "pokemon.csv"},
            }]
        }
        data_filter = {
            "needs_filter": True,
            "description": "Excluir variantes regionais",
            "code": (
                "def filter_dataframes(dataframes):\n"
                "    filtered = {}\n"
                "    for name, df in dataframes.items():\n"
                "        out = df.copy()\n"
                "        if 'variant' in out.columns:\n"
                "            out = out[out['variant'].astype(str).str.lower() != 'regional']\n"
                "        filtered[name] = out\n"
                "    return filtered"
            ),
        }

        filter_job_id = f"test_filter_{uuid.uuid4().hex}"
        self.created_paths.append(f"workspace/scripts/filter_{filter_job_id}.py")

        files = apply_data_filter(filter_job_id, session, data_filter)
        filtered_path = files[0]["filtered_path"]
        self.created_paths.append(filtered_path)

        self.assertEqual(files[0]["path"], source_path)
        self.assertTrue(os.path.exists(filtered_path))
        self.assertEqual(files[0]["filter_description"], "Excluir variantes regionais")
        self.assertEqual(
            pd.read_csv(filtered_path)["name"].tolist(),
            ["Pikachu", "Bulbasaur"],
        )
        self.assertEqual(files[0]["artifact"]["shape"]["rows"], 2)

    def test_build_files_map_prefers_filtered_path_and_can_clear_filter_state(self):
        files = [{
            "original_filename": "data.csv",
            "path": "workspace/uploads/original.csv",
            "filtered_path": "workspace/filtered/filtered.csv",
            "filter_description": "Filtro ativo",
            "raw_artifact": {"shape": {"rows": 3}},
            "artifact": {"shape": {"rows": 2}},
        }]

        files_map = build_files_map({"files": files})
        self.assertTrue(files_map["data.csv"].endswith("workspace/filtered/filtered.csv"))

        cleared = clear_data_filter_state(files)
        self.assertNotIn("filtered_path", cleared[0])
        self.assertNotIn("filter_description", cleared[0])
        self.assertEqual(cleared[0]["artifact"], {"shape": {"rows": 3}})


class RegenerationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            path = get_session_path(job_id)
            if os.path.exists(path):
                os.remove(path)

    def create_report_session(self):
        job_id = f"test-regenerate-{uuid.uuid4().hex}"
        self.job_ids.append(job_id)
        create_session(job_id, {
            "job_id": job_id,
            "status": "Concluído",
            "logs": [],
            "files": [],
            "initial_prompt": "Analyze data",
            "current_goal": "Analyze data",
            "refinement_history": [],
            "artifacts": [
                {"id": "t_summary", "type": "text", "indicator": "Summary"},
                {"id": "g_chart", "type": "graphic", "indicator": "Chart"},
                {"id": "m_total", "type": "metric", "indicator": "Total"},
            ],
            "template": [{"id": "t_summary", "page": 1, "x": 10, "y": 10, "w": 80, "h": 40}],
            "components": {
                "t_summary": "text component",
                "g_chart": "graphic component",
                "m_total": "metric helper",
            },
            "calculated_metrics": {"m_total": 10},
            "settings": {"title": "Original", "alignment": "J"},
            "script": "generated script",
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash",
            "pdf_version": 1,
            "retry_count": 0,
            "explore_history": [],
        })
        return job_id

    def call_regenerate(self, job_id, payload):
        return asyncio.run(regenerate_session(job_id, BackgroundTasks(), payload))

    def test_regenerate_texts_removes_only_text_components_and_saves_model_settings(self):
        job_id = self.create_report_session()

        self.call_regenerate(job_id, {
            "scope": "texts",
            "settings": {
                "title": "Updated",
                "alignment": "J",
                "provider": "gemini",
                "model_name": "models/gemini-2.5-flash",
            },
        })

        session = get_session(job_id)
        self.assertNotIn("t_summary", session["components"])
        self.assertIn("g_chart", session["components"])
        self.assertIn("m_total", session["components"])
        self.assertEqual(session["settings"]["title"], "Updated")
        self.assertEqual(session["provider"], "gemini")
        self.assertEqual(session["model_name"], "models/gemini-2.5-flash")

    def test_regenerate_graphics_removes_only_visual_components(self):
        job_id = self.create_report_session()

        self.call_regenerate(job_id, {"scope": "graphics"})

        session = get_session(job_id)
        self.assertIn("t_summary", session["components"])
        self.assertNotIn("g_chart", session["components"])
        self.assertIn("m_total", session["components"])

    def test_regenerate_all_clears_derived_report_state(self):
        job_id = self.create_report_session()

        self.call_regenerate(job_id, {"scope": "all"})

        session = get_session(job_id)
        self.assertEqual(session["settings"]["title"], "")
        self.assertEqual(session["artifacts"], [])
        self.assertEqual(session["template"], [])
        self.assertEqual(session["components"], {})
        self.assertEqual(session["calculated_metrics"], {})
        self.assertEqual(session["script"], "")

    def test_regenerate_rejects_invalid_scope(self):
        job_id = self.create_report_session()

        with self.assertRaises(HTTPException) as error:
            self.call_regenerate(job_id, {"scope": "unknown"})
        self.assertEqual(error.exception.status_code, 400)


if __name__ == "__main__":
    unittest.main()

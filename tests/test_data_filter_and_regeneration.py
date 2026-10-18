import asyncio
import os
import uuid
import unittest

import pandas as pd
from fastapi import BackgroundTasks, HTTPException

from app.routers.api import regenerate_session
from app.services.pipeline_service import (
    apply_data_filter,
    build_files_map,
    clear_data_filter_state,
    has_data_filter_decision,
)
from app.services.web_helpers import session_file_variant
from app.services.session_manager import delete_session, get_session
from tests.auth_helpers import create_session, request_as


class DataFilterTests(unittest.TestCase):
    def setUp(self):
        self.created_paths = []

    def tearDown(self):
        for path in self.created_paths:
            if path and os.path.exists(path):
                os.remove(path)

    def test_default_filter_state_is_not_treated_as_existing_decision(self):
        self.assertFalse(has_data_filter_decision({}))
        self.assertTrue(has_data_filter_decision({"evaluated": True, "needs_preprocessing": False}))
        self.assertFalse(has_data_filter_decision({
            "needs_filter": False,
            "description": "",
            "code": "",
        }))
        self.assertTrue(has_data_filter_decision({
            "needs_preprocessing": False,
            "description": "Não filtrar estes dados",
            "code": "",
        }))

    def test_applies_preprocessing_once_and_preserves_original_upload(self):
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
            "needs_preprocessing": True,
            "description": "Excluir variantes regionais e calcular valor dobrado",
            "code": (
                "def preprocess_dataframes(dataframes):\n"
                "    processed = {}\n"
                "    for name, df in dataframes.items():\n"
                "        out = df.copy()\n"
                "        if 'variant' in out.columns:\n"
                "            out = out[out['variant'].astype(str).str.lower() != 'regional']\n"
                "        out['value_doubled'] = out['value'] * 2\n"
                "        processed[name] = out\n"
                "    return processed"
            ),
        }

        filter_job_id = f"test_filter_{uuid.uuid4().hex}"
        self.created_paths.append(f"workspace/scripts/preprocess_{filter_job_id}.py")

        files = apply_data_filter(filter_job_id, session, data_filter)
        filtered_path = files[0]["preprocessed_path"]
        self.created_paths.append(filtered_path)

        self.assertEqual(files[0]["path"], source_path)
        self.assertEqual(files[0]["filtered_path"], filtered_path)
        self.assertTrue(os.path.exists(filtered_path))
        self.assertEqual(files[0]["preprocessing_description"], "Excluir variantes regionais e calcular valor dobrado")
        self.assertEqual(files[0]["preprocessing_impact"]["rows_before"], 3)
        self.assertEqual(files[0]["preprocessing_impact"]["rows_after"], 2)
        self.assertEqual(files[0]["preprocessing_impact"]["columns_added"], ["value_doubled"])
        self.assertEqual(data_filter["impact"]["pokemon.csv"]["rows_removed"], 1)
        processed = pd.read_csv(filtered_path)
        self.assertEqual(processed["name"].tolist(), ["Pikachu", "Bulbasaur"])
        self.assertEqual(processed["value_doubled"].tolist(), [2, 6])
        self.assertEqual(files[0]["artifact"]["shape"]["rows"], 2)

    def test_build_files_map_prefers_filtered_path_and_can_clear_filter_state(self):
        files = [{
            "original_filename": "data.csv",
            "path": "workspace/uploads/original.csv",
            "preprocessed_path": "workspace/filtered/preprocessed.csv",
            "preprocessing_description": "Pré-processamento ativo",
            "filtered_path": "workspace/filtered/filtered.csv",
            "filter_description": "Filtro ativo",
            "raw_artifact": {"shape": {"rows": 3}},
            "artifact": {"shape": {"rows": 2}},
        }]

        files_map = build_files_map({"files": files})
        self.assertTrue(files_map["data.csv"].endswith("workspace/filtered/preprocessed.csv"))

        cleared = clear_data_filter_state(files)
        self.assertNotIn("preprocessed_path", cleared[0])
        self.assertNotIn("preprocessing_description", cleared[0])
        self.assertNotIn("preprocessing_impact", cleared[0])
        self.assertNotIn("filtered_path", cleared[0])
        self.assertNotIn("filter_description", cleared[0])
        self.assertNotIn("filter_impact", cleared[0])
        self.assertEqual(cleared[0]["artifact"], {"shape": {"rows": 3}})

    def test_session_file_variant_exposes_preprocessed_file(self):
        source_path = f"workspace/uploads/original_{uuid.uuid4().hex}.csv"
        processed_path = f"workspace/filtered/processed_{uuid.uuid4().hex}.csv"
        pd.DataFrame({"value": [1]}).to_csv(source_path, index=False)
        pd.DataFrame({"value": [1], "value_doubled": [2]}).to_csv(processed_path, index=False)
        self.created_paths.extend([source_path, processed_path])

        file_info = {
            "original_filename": "original.csv",
            "path": source_path,
            "preprocessed_path": processed_path,
            "mime_type": "text/csv",
        }

        original = session_file_variant(file_info)
        processed = session_file_variant(file_info, "preprocessed")

        self.assertEqual(original["path"], source_path)
        self.assertEqual(processed["path"], processed_path)
        self.assertEqual(processed["filename"], "original_preprocessed.csv")
        self.assertEqual(processed["mime_type"], "text/csv")


class RegenerationEndpointTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            delete_session(job_id)

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
        return asyncio.run(regenerate_session(request_as(), job_id, BackgroundTasks(), payload))

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
        self.assertEqual(session["model_name"], "gemini-2.5-flash")

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

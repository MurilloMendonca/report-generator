import json
import os
import uuid
import unittest

from app.models.session import CURRENT_SESSION_SCHEMA_VERSION, normalize_session
from app.services.session_manager import append_stage_event, create_session, get_session, get_session_lock_path, get_session_path, set_session_stage, update_session


class SessionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            for path in [get_session_path(job_id), get_session_lock_path(job_id)]:
                if os.path.exists(path):
                    os.remove(path)

    def new_job_id(self):
        job_id = f"test-schema-{uuid.uuid4().hex}"
        self.job_ids.append(job_id)
        return job_id

    def write_raw_session(self, job_id, payload):
        os.makedirs(os.path.dirname(get_session_path(job_id)), exist_ok=True)
        with open(get_session_path(job_id), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

    def test_normalizes_legacy_session_defaults_without_losing_unknown_fields(self):
        job_id = self.new_job_id()
        self.write_raw_session(job_id, {
            "initial_prompt": "Analisar vendas",
            "files": [{
                "original_filename": "sales.csv",
                "path": "workspace/uploads/sales.csv",
                "artifact": {"shape": {"rows": 10}},
            }],
            "settings": {"title": "Relatório"},
            "custom_state": {"keep": True},
        })

        session = get_session(job_id)

        self.assertEqual(session["schema_version"], CURRENT_SESSION_SCHEMA_VERSION)
        self.assertEqual(session["job_id"], job_id)
        self.assertEqual(session["current_goal"], "Analisar vendas")
        self.assertEqual(session["provider"], "deepseek")
        self.assertEqual(session["model_name"], "deepseek-flash")
        self.assertEqual(session["settings"]["alignment"], "J")
        self.assertEqual(session["settings"]["font"], "Arial")
        self.assertEqual(session["settings"]["visual_theme_id"], "executive_clean")
        self.assertEqual(session["settings"]["chart_style_id"], "modern")
        self.assertEqual(session["data_filter"]["needs_filter"], False)
        self.assertEqual(session["data_preprocessing"]["needs_preprocessing"], False)
        self.assertEqual(session["files"][0]["raw_artifact"], {"shape": {"rows": 10}})
        self.assertEqual(session["custom_state"], {"keep": True})

    def test_create_session_writes_normalized_shape(self):
        job_id = self.new_job_id()
        create_session(job_id, {
            "files": [],
            "initial_prompt": "Objetivo",
            "settings": {"alignment": ""},
        })

        session = get_session(job_id)

        self.assertEqual(session["schema_version"], CURRENT_SESSION_SCHEMA_VERSION)
        self.assertEqual(session["settings"]["alignment"], "J")
        self.assertEqual(session["current_goal"], "Objetivo")
        self.assertEqual(session["artifacts"], [])
        self.assertEqual(session["components"], {})

    def test_normalizes_legacy_artifacts_dict_to_list(self):
        session = normalize_session({
            "artifacts": {
                "kpi_resumo": {"id": "kpi_resumo", "type": "metric"},
                "texto": {"id": "texto", "type": "text"},
            },
            "template": {
                "texto": {"id": "texto", "page": 1},
            },
            "settings": None,
            "data_filter": None,
        })

        self.assertEqual(
            session["artifacts"],
            [
                {"id": "kpi_resumo", "type": "metric"},
                {"id": "texto", "type": "text"},
            ],
        )
        self.assertEqual(session["template"], [{"id": "texto", "page": 1}])
        self.assertEqual(session["settings"]["alignment"], "J")
        self.assertEqual(session["data_filter"]["needs_filter"], False)
        self.assertEqual(session["data_preprocessing"]["needs_preprocessing"], False)

    def test_migrates_legacy_filter_to_preprocessing(self):
        session = normalize_session({
            "job_id": "legacy-filter",
            "files": [{
                "original_filename": "data.csv",
                "path": "workspace/uploads/data.csv",
                "artifact": {"shape": {"rows": 10}},
                "filtered_path": "workspace/filtered/data.csv",
                "filter_description": "Filtro antigo",
            }],
            "data_filter": {
                "needs_filter": True,
                "description": "Filtro antigo",
                "code": "def filter_dataframes(dataframes):\n    return dataframes",
            },
        })

        self.assertTrue(session["data_preprocessing"]["needs_preprocessing"])
        self.assertEqual(session["files"][0]["preprocessed_path"], "workspace/filtered/data.csv")
        self.assertEqual(session["files"][0]["preprocessing_description"], "Filtro antigo")

    def test_update_session_preserves_unknown_fields_and_applies_defaults(self):
        job_id = self.new_job_id()
        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Original",
            "unknown_feature_flag": True,
        })

        updated = update_session(job_id, {"status": "Processando"})

        self.assertEqual(updated["status"], "Processando")
        self.assertTrue(updated["unknown_feature_flag"])
        self.assertIn("updated_at", updated)
        self.assertEqual(updated["settings"]["alignment"], "J")

    def test_update_session_writes_atomically_under_concurrent_updates(self):
        import threading

        job_id = self.new_job_id()
        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Concurrent",
            "logs": [],
        })

        def worker(index):
            update_session(job_id, {f"value_{index}": index})

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        session = get_session(job_id)
        for index in range(10):
            self.assertEqual(session[f"value_{index}"], index)

    def test_set_session_stage_records_progress_and_structured_error(self):
        job_id = self.new_job_id()
        create_session(job_id, {"job_id": job_id, "initial_prompt": "Stages"})

        set_session_stage(job_id, "graphics", 75)
        session = get_session(job_id)
        self.assertEqual(session["current_step"], "graphics")
        self.assertEqual(session["progress"], 75)

        set_session_stage(job_id, "failed", 120, {
            "code": "render_error",
            "message": "Render failed",
            "stage": "render",
            "retryable": False,
        })
        session = get_session(job_id)
        self.assertEqual(session["progress"], 100)
        self.assertEqual(session["status"], "Render failed")
        self.assertEqual(session["error"]["code"], "render_error")

    def test_append_stage_event_persists_stage_history(self):
        job_id = self.new_job_id()
        create_session(job_id, {"job_id": job_id, "initial_prompt": "Observability"})

        append_stage_event(job_id, "metadata", "start", status="in_progress", provider="deepseek", model_name="deepseek-v4-flash")
        append_stage_event(job_id, "metadata", "end", status="ok", duration_ms=123.4, provider="deepseek", model_name="deepseek-v4-flash")

        session = get_session(job_id)
        self.assertEqual(len(session["stage_history"]), 2)
        self.assertEqual(session["stage_history"][0]["stage"], "metadata")
        self.assertEqual(session["stage_history"][0]["event"], "start")
        self.assertEqual(session["stage_history"][1]["status"], "ok")
        self.assertEqual(session["stage_history"][1]["duration_ms"], 123.4)

    def test_normalize_session_accepts_none(self):
        self.assertIsNone(normalize_session(None))


if __name__ == "__main__":
    unittest.main()

import json
import os
import uuid
import unittest

from app.models.session import CURRENT_SESSION_SCHEMA_VERSION, normalize_session
from app.services.session_manager import create_session, get_session, get_session_path, update_session


class SessionSchemaTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            path = get_session_path(job_id)
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
        self.assertEqual(session["model_name"], "deepseek-v4-flash")
        self.assertEqual(session["settings"]["alignment"], "J")
        self.assertEqual(session["settings"]["font"], "Arial")
        self.assertEqual(session["data_filter"]["needs_filter"], False)
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

    def test_normalize_session_accepts_none(self):
        self.assertIsNone(normalize_session(None))


if __name__ == "__main__":
    unittest.main()

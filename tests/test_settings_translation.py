import asyncio
import unittest
import uuid
from unittest.mock import patch

from fastapi import BackgroundTasks

import app.routers.api as api
from app.services import web_helpers
from app.services.cleanup_service import delete_entire_session
from app.services.llm.errors import LLMTimeoutError
from app.services.session_manager import create_session, get_session


class SettingsTranslationTests(unittest.TestCase):
    """Language changes translate in a background job so the request returns
    well inside Cloudflare's ~100 s limit."""

    def setUp(self):
        self.job_id = f"test-translate-{uuid.uuid4().hex}"
        self.addCleanup(delete_entire_session, self.job_id)
        create_session(self.job_id, {
            "job_id": self.job_id,
            "current_step": "completed",
            "provider": "deepseek",
            "model_name": "deepseek-flash",
            "settings": {"language": "pt", "chart_style_id": "modern", "selected_template_id": "classic"},
            "artifacts": [
                {"id": "t_intro", "type": "text", "indicator": "Introdução"},
                {"id": "g_sales", "type": "bar", "indicator": "Vendas"},
            ],
            "components": {"t_intro": "texto", "g_sales": "def render_g_sales(): pass"},
        })

    def save(self, settings):
        tasks = BackgroundTasks()
        response = asyncio.run(api.update_settings(self.job_id, tasks, settings))
        return response, tasks

    def translated(self, session, target_lang, job_id=None):
        return {
            "artifacts": [{**artifact, "indicator": artifact["indicator"] + " (en)"} for artifact in session["artifacts"]],
            "components": {key: code + " # en" for key, code in session["components"].items()},
        }

    def test_language_change_returns_before_translating(self):
        with patch.object(web_helpers, "translate_workspace", side_effect=self.translated) as translate, \
             patch.object(api, "run_script_generation") as render:
            response, tasks = self.save({"language": "en"})
            self.assertEqual(response, {"status": "queued", "translating": True})
            self.assertEqual(get_session(self.job_id)["current_step"], "translating")
            self.assertEqual(get_session(self.job_id)["settings"]["language"], "en")
            translate.assert_not_called()

            asyncio.run(tasks())

        translate.assert_called_once()
        render.assert_called_once_with(self.job_id)
        session = get_session(self.job_id)
        self.assertEqual(session["artifacts"][0]["indicator"], "Introdução (en)")
        self.assertEqual(session["components"]["t_intro"], "texto # en")

    def test_translation_failure_is_stored_and_skips_render(self):
        with patch.object(web_helpers, "translate_workspace", side_effect=LLMTimeoutError("workspace_translation", 45)), \
             patch.object(api, "run_script_generation") as render:
            _, tasks = self.save({"language": "en"})
            asyncio.run(tasks())
        render.assert_not_called()
        session = get_session(self.job_id)
        self.assertEqual(session["current_step"], "translation_failed")
        self.assertEqual(session["error"]["code"], "llm_timeout")

    def test_style_change_keeps_visual_components_dropped_after_translation(self):
        with patch.object(web_helpers, "translate_workspace", side_effect=self.translated), \
             patch.object(api, "run_pipeline") as pipeline:
            _, tasks = self.save({"language": "en", "chart_style_id": "minimal"})
            asyncio.run(tasks())
        pipeline.assert_called_once_with(self.job_id)
        components = get_session(self.job_id)["components"]
        self.assertNotIn("g_sales", components)
        self.assertEqual(components["t_intro"], "texto # en")

    def test_template_change_regenerates_without_translating(self):
        with patch.object(web_helpers, "translate_workspace") as translate, \
             patch.object(api, "run_pipeline") as pipeline:
            response, tasks = self.save({"language": "en", "selected_template_id": "two_columns"})
            asyncio.run(tasks())
        self.assertEqual(response, {"status": "OK"})
        translate.assert_not_called()
        pipeline.assert_called_once_with(self.job_id)
        self.assertEqual(get_session(self.job_id)["artifacts"], [])

    def test_plain_settings_change_rerenders(self):
        with patch.object(web_helpers, "translate_workspace") as translate, \
             patch.object(api, "run_script_generation") as render:
            response, tasks = self.save({"title": "Novo título"})
            asyncio.run(tasks())
        self.assertEqual(response, {"status": "OK"})
        translate.assert_not_called()
        render.assert_called_once_with(self.job_id)
        self.assertEqual(get_session(self.job_id)["settings"]["title"], "Novo título")


if __name__ == "__main__":
    unittest.main()

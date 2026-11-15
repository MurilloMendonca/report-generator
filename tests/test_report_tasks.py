"""Background jobs started through the API report their outcome by task id."""
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.routers.api as api
from app.main import app
from app.services import session_manager
from app.services.cleanup_service import delete_entire_session
from app.services.job_runner import run_job
from app.services.session_manager import finish_task, get_task, queue_task, start_task, update_session
from tests.auth_helpers import create_session, signed_in_headers


class TaskTestCase(unittest.TestCase):
    def setUp(self):
        self.job_id = f"test-task-{uuid.uuid4().hex}"
        create_session(self.job_id, {"current_step": "completed", "pdf_version": 1})
        self.addCleanup(delete_entire_session, self.job_id)

    def state(self, task_id):
        return get_task(self.job_id, task_id)["state"]


class TaskLifecycleTests(TaskTestCase):
    def test_task_moves_from_queued_to_completed_with_the_pdf_version(self):
        task = queue_task(self.job_id, "run_pipeline")
        self.assertEqual(self.state(task["task_id"]), "queued")
        start_task(self.job_id, task["task_id"])
        self.assertEqual(self.state(task["task_id"]), "running")
        update_session(self.job_id, {"pdf_version": 2})
        finish_task(self.job_id, task["task_id"])
        finished = get_task(self.job_id, task["task_id"])
        self.assertEqual((finished["state"], finished["pdf_version"], finished["error"]), ("completed", 2, None))
        self.assertTrue(finished["started_at"] and finished["finished_at"])

    def test_failed_report_fails_the_task_with_its_error(self):
        task = queue_task(self.job_id, "run_pipeline")
        start_task(self.job_id, task["task_id"])
        update_session(self.job_id, {"current_step": "failed", "error": {"code": "llm_timeout", "message": "slow"}})
        finish_task(self.job_id, task["task_id"])
        finished = get_task(self.job_id, task["task_id"])
        self.assertEqual(finished["state"], "failed")
        self.assertEqual(finished["error"]["code"], "llm_timeout")

    def test_a_task_overtaken_by_a_later_one_is_superseded(self):
        first, second = queue_task(self.job_id, "run_pipeline"), queue_task(self.job_id, "process_chat_refinement")
        start_task(self.job_id, first["task_id"])
        start_task(self.job_id, second["task_id"])
        finish_task(self.job_id, first["task_id"])
        finish_task(self.job_id, second["task_id"])
        self.assertEqual([self.state(first["task_id"]), self.state(second["task_id"])], ["superseded", "completed"])

    def test_failed_exploration_fails_only_its_task(self):
        update_session(self.job_id, {"explore_history": [{"role": "assistant", "status": "error", "content": "Sem dados"}]})
        task = queue_task(self.job_id, "complete_exploration_job")
        start_task(self.job_id, task["task_id"])
        finish_task(self.job_id, task["task_id"])
        self.assertEqual(get_task(self.job_id, task["task_id"])["error"]["code"], "exploration_failed")

    def test_run_job_closes_the_task_even_when_the_job_raises(self):
        task = queue_task(self.job_id, "run_pipeline")

        def boom(_job_id):
            raise RuntimeError("broken")

        with self.assertLogs("app.services.job_runner", "ERROR"):
            run_job(boom, self.job_id, task_id=task["task_id"])
        finished = get_task(self.job_id, task["task_id"])
        self.assertEqual((finished["state"], finished["error"]["code"]), ("failed", "job_error"))
        self.assertIsNotNone(finished["started_at"])

    def test_restart_fails_open_tasks(self):
        task = queue_task(self.job_id, "run_pipeline")
        start_task(self.job_id, task["task_id"])
        with patch.object(session_manager, "has_pending_exploration", return_value=False):
            self.assertIn(self.job_id, session_manager.recover_interrupted_sessions())
        finished = get_task(self.job_id, task["task_id"])
        self.assertEqual((finished["state"], finished["error"]["code"]), ("failed", "interrupted"))


class TaskRouteTests(TaskTestCase):
    def test_chat_returns_a_task_id_that_reports_the_outcome(self):
        with patch.object(api, "process_chat_refinement") as refine, TestClient(app) as client:
            response = client.post(f"/api/sessions/{self.job_id}/chat", data={"prompt": "Mais curto"}, headers=signed_in_headers())
            task_id = response.json()["task_id"]
            status = client.get(f"/api/sessions/{self.job_id}/tasks/{task_id}", headers=signed_in_headers())
            missing = client.get(f"/api/sessions/{self.job_id}/tasks/{'b' * 32}", headers=signed_in_headers())
        self.assertEqual(response.status_code, 200, response.text)
        refine.assert_called_once()
        self.assertEqual(status.json()["state"], "completed")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()

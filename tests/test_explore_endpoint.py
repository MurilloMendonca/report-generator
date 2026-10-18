import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

import app.routers.api as api
from app.services import exploration_service
from app.services.cleanup_service import delete_entire_session
from app.services.session_manager import create_session, get_session


class ExploreEndpointTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            delete_entire_session(job_id)

    def create_session(self):
        job_id = f"test-explore-{uuid.uuid4().hex}"
        self.job_ids.append(job_id)
        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Explore data",
            "files": [],
            "provider": "deepseek",
            "model_name": "deepseek-v4-flash",
            "explore_history": [],
        })
        return job_id

    def test_explore_runs_generated_script_and_persists_history(self):
        job_id = self.create_session()

        with patch.object(exploration_service, "generate_exploration_script", return_value="print('answer')") as generate_mock, \
             patch.object(exploration_service, "execute_script", return_value={"success": True, "stdout": "answer\n", "stderr": ""}) as execute_mock:
            tasks = BackgroundTasks()
            response = asyncio.run(api.explore_data(job_id, tasks, "Quantos registros existem?"))

            # The request only records the question; the answer is a background job.
            self.assertEqual(response["status"], "pending")
            self.assertEqual(response["history"][-1]["status"], "pending")
            generate_mock.assert_not_called()
            asyncio.run(tasks())

        generate_mock.assert_called_once()
        execute_mock.assert_called_once()
        session = get_session(job_id)
        self.assertEqual(len(session["explore_history"]), 2)
        self.assertEqual(session["explore_history"][0]["role"], "user")
        self.assertEqual(session["explore_history"][1]["content"], "answer\n")
        self.assertEqual(session["explore_history"][1]["status"], "ok")
        self.assertEqual(session["explore_history"][1]["turn_id"], response["turn_id"])

    def test_second_question_is_refused_while_one_is_pending(self):
        job_id = self.create_session()
        first = asyncio.run(api.explore_data(job_id, BackgroundTasks(), "Primeira"))
        with self.assertRaises(HTTPException) as error:
            asyncio.run(api.explore_data(job_id, BackgroundTasks(), "Segunda"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(len(get_session(job_id)["explore_history"]), 2)
        self.assertEqual(get_session(job_id)["explore_history"][1]["turn_id"], first["turn_id"])

    def test_restart_recovery_resolves_pending_question(self):
        from app.services.session_manager import recover_interrupted_sessions
        job_id = self.create_session()
        asyncio.run(api.explore_data(job_id, BackgroundTasks(), "Pergunta"))
        self.assertIn(job_id, recover_interrupted_sessions())
        answer = get_session(job_id)["explore_history"][1]
        self.assertEqual(answer["status"], "error")
        self.assertEqual(answer["error_status"], 503)


if __name__ == "__main__":
    unittest.main()

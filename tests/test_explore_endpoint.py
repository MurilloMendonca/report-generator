import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

import app.main as main
from app.services.session_manager import create_session, get_session, get_session_lock_path, get_session_path


class ExploreEndpointTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            for path in [get_session_path(job_id), get_session_lock_path(job_id)]:
                if os.path.exists(path):
                    os.remove(path)

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

        with patch.object(main, "generate_exploration_script", return_value="print('answer')") as generate_mock, \
             patch.object(main, "execute_script", return_value={"success": True, "stdout": "answer\n", "stderr": ""}) as execute_mock:
            response = asyncio.run(main.explore_data(job_id, "Quantos registros existem?"))

        self.assertEqual(response, {"answer": "answer\n"})
        generate_mock.assert_called_once()
        execute_mock.assert_called_once()

        session = get_session(job_id)
        self.assertEqual(session["explore_history"][0]["role"], "user")
        self.assertEqual(session["explore_history"][1]["content"], "answer\n")


if __name__ == "__main__":
    unittest.main()

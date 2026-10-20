import os
import threading
import time
import unittest
from unittest.mock import patch

from app.services.pipeline_service import (
    _invalidate_components_for_binding_warnings,
    is_current_session_run,
    run_pipeline,
)
from app.services.session_manager import delete_session, get_session
from tests.auth_helpers import create_session


class PipelineRunTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            delete_session(job_id)

    def new_job_id(self):
        job_id = f"test-pipeline-{len(self.job_ids)}"
        self.job_ids.append(job_id)
        return job_id

    def test_later_run_supersedes_earlier_pipeline_run(self):
        job_id = self.new_job_id()
        create_session(job_id, {
            "job_id": job_id,
            "initial_prompt": "Pipeline concurrency",
            "files": [],
            "logs": [],
        })

        first_started = threading.Event()
        release_first = threading.Event()
        call_order = []

        def fake_run_pipeline(job_id_arg, run_id):
            call_order.append(run_id)
            if len(call_order) == 1:
                first_started.set()
                release_first.wait(timeout=5)
                if is_current_session_run(job_id_arg, run_id):
                    from app.services.session_manager import update_session
                    update_session(job_id_arg, {"status": f"run-{run_id}"})
            else:
                from app.services.session_manager import update_session
                update_session(job_id_arg, {"status": f"run-{run_id}"})

        with patch("app.services.pipeline_service._run_pipeline", side_effect=fake_run_pipeline):
            first_thread = threading.Thread(target=run_pipeline, args=(job_id,))
            first_thread.start()
            self.assertTrue(first_started.wait(timeout=5))

            second_thread = threading.Thread(target=run_pipeline, args=(job_id,))
            second_thread.start()

            deadline = time.time() + 5
            while time.time() < deadline:
                if get_session(job_id)["active_run_id"] == 2:
                    break
                time.sleep(0.01)

            release_first.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

        session = get_session(job_id)
        self.assertEqual(session["status"], "run-2")
        self.assertEqual(session["run_sequence"], 2)
        self.assertEqual(session["active_run_id"], 2)
        self.assertEqual(call_order, [1, 2])

    def test_binding_warning_invalidates_component_once(self):
        artifacts = [{"id": "g_yearly", "type": "graphic", "indicator": "Evolução anual"}]
        issues = [{
            "code": "data_binding_role_mismatch",
            "id": "g_yearly",
            "role": "date",
            "column": "Gender Violence Spain Dataset (20202024).xlsx:Year",
            "expected_kind": "temporal",
            "actual_kind": "numeric",
        }]
        components = {
            "g_yearly": "def render_g_yearly(pdf, dataframes, x, y, w, h): pass",
            "t_summary": "def render_t_summary(pdf, dataframes, x, y, w, h): pass",
        }

        components, retry_state, invalidated = _invalidate_components_for_binding_warnings(
            artifacts,
            issues,
            components,
            {},
        )

        self.assertEqual(invalidated, ["g_yearly"])
        self.assertNotIn("g_yearly", components)
        self.assertIn("t_summary", components)
        self.assertIn("g_yearly", retry_state)

        regenerated_components = dict(components)
        regenerated_components["g_yearly"] = "def render_g_yearly(pdf, dataframes, x, y, w, h): pass"
        components, retry_state, invalidated = _invalidate_components_for_binding_warnings(
            artifacts,
            issues,
            regenerated_components,
            retry_state,
        )

        self.assertEqual(invalidated, [])
        self.assertIn("g_yearly", components)
        self.assertIn("g_yearly", retry_state)

    def test_resolved_binding_warning_clears_retry_state(self):
        components, retry_state, invalidated = _invalidate_components_for_binding_warnings(
            [{"id": "g_yearly", "type": "graphic"}],
            [],
            {"g_yearly": "code"},
            {"g_yearly": ["old-signature"]},
        )

        self.assertEqual(invalidated, [])
        self.assertEqual(components, {"g_yearly": "code"})
        self.assertEqual(retry_state, {})


if __name__ == "__main__":
    unittest.main()

import json
import uuid
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.security import create_session_token
from app.services import exploration_service as explorer
from app.services.cleanup_service import delete_entire_session, get_session_generated_paths
from app.services.llm.errors import LLMBudgetError, LLMTimeoutError
from app.services.session_manager import create_session, get_session, update_session
from app.utils.exploration_charts import (
    MAX_CHART_BYTES, chart_path, collect_exploration_charts, exploration_directory, validate_chart_image,
)


BAR_SCRIPT = """
df = dataframes['sales.csv']
totals = df.groupby('region')['revenue'].sum(min_count=1).sort_values()
fig, ax = plt.subplots()
totals.plot.barh(ax=ax)
ax.set_xlabel('Revenue')
ax.set_ylabel('Region')
fig.tight_layout()
show_chart(fig, title='Revenue by region', caption='Total revenue by region, all rows.')
print(f'Total revenue: {totals.sum():.0f}')
"""


class StoredExploreResult:
    """A background explorer answer read back from the session, shaped like the
    old synchronous response so the assertions below stay the same."""

    def __init__(self, session, turn_id):
        history = session["explore_history"]
        turn = next(m for m in history if m.get("role") == "assistant" and m.get("turn_id") == turn_id)
        body = {"answer": turn["content"], "charts": turn.get("charts", []), "turn_id": turn_id, "history": history}
        if turn["status"] == "error":
            self.status_code = turn["error_status"]
            self._json = {"detail": {"message": turn["content"], **body}}
        else:
            self.status_code = 200
            self._json = body
        self.text = json.dumps(self._json)

    def json(self):
        return self._json


class ExplorerChartTests(unittest.TestCase):
    def setUp(self):
        self.job_ids = []
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.headers = {"x-access-token": create_session_token()}
        guard = patch("app.services.llm.client.call_llm", side_effect=AssertionError("No live providers in tests"))
        guard.start()
        self.addCleanup(guard.stop)
        self.job_id = self.create_report()

    def tearDown(self):
        for job_id in self.job_ids:
            delete_entire_session(job_id)

    def create_report(self):
        job_id = f"test-explorer-chart-{uuid.uuid4().hex}"
        self.job_ids.append(job_id)
        upload = Path(f"workspace/uploads/{job_id}.csv")
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_text("region,revenue\nNorth,10\nSouth,20\nNorth,15\n")
        create_session(job_id, {
            "job_id": job_id, "initial_prompt": "Explore data", "current_goal": "Original goal",
            "status": "Concluído", "current_step": "completed", "progress": 100,
            "pdf_version": 4, "script": "saved report script", "error": {},
            "artifacts": [{"id": "t_summary", "type": "text", "indicator": "Summary"}],
            "template": [{"id": "t_summary", "page": 1, "x": 10, "y": 30, "w": 190, "h": 50}],
            "components": {"t_summary": "saved code"}, "calculated_metrics": {"total": 45},
            "files": [{"original_filename": "sales.csv", "path": str(upload), "artifact": {
                "filename": "sales.csv", "columns": [
                    {"name": "region", "type": "object"}, {"name": "revenue", "type": "int64"},
                ],
            }}],
        })
        return job_id

    def endpoint(self):
        return f"/api/sessions/{self.job_id}/explore"

    def post_question(self, prompt):
        """POST a question; the test client runs the background answer before returning."""
        response = self.client.post(self.endpoint(), data={"prompt": prompt}, headers=self.headers)
        if response.status_code != 202:
            return response
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.json()["history"][-1]["status"], "pending")
        return StoredExploreResult(get_session(self.job_id), response.json()["turn_id"])

    def ask(self, script, prompt="Show a revenue chart"):
        with patch("app.services.llm.client.call_llm", return_value=script) as model:
            response = self.post_question(prompt)
        model.assert_called_once()
        return response

    def assert_report_unchanged(self, before):
        after = get_session(self.job_id)
        for key in ("status", "current_step", "progress", "error", "pdf_version", "script", "artifacts", "template", "components", "calculated_metrics", "current_goal", "settings"):
            self.assertEqual(after[key], before[key], key)

    def test_text_answer_is_supported_and_report_is_unchanged(self):
        before = get_session(self.job_id)
        response = self.ask("print(len(dataframes['sales.csv']))", "How many rows?")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "3\n")
        self.assertEqual(response.json()["charts"], [])
        self.assertEqual(len(response.json()["history"]), 2)
        self.assert_report_unchanged(before)

    def test_real_chart_is_authenticated_downloadable_and_restored_on_reload(self):
        before = get_session(self.job_id)
        response = self.ask(BAR_SCRIPT)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["answer"], "Total revenue: 45\n")
        chart = data["charts"][0]
        self.assertNotIn(str(Path.cwd()), response.text)
        self.assertEqual(self.client.get(chart["url"]).status_code, 401)
        png = self.client.get(chart["url"], headers=self.headers)
        self.assertEqual(png.status_code, 200)
        self.assertEqual(png.headers["content-type"], "image/png")
        self.assertIn("inline", png.headers["content-disposition"])
        with Image.open(BytesIO(png.content)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (chart["width"], chart["height"]))
        download = self.client.get(chart["url"] + "?download=true", headers=self.headers)
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(download.content, png.content)
        restored = self.client.get(f"/api/sessions/{self.job_id}", headers=self.headers).json()
        self.assertEqual(restored["explore_history"], data["history"])
        self.assert_report_unchanged(before)

    def test_multiple_charts_and_pyplot_show_are_captured(self):
        response = self.ask("for n in range(2):\n    fig, ax = plt.subplots()\n    ax.plot([1, 2], [n, 3])\n    ax.set_title('Trend ' + str(n))\nplt.show()\nprint('Two charts')")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([chart["id"] for chart in response.json()["charts"]], ["chart_1", "chart_2"])

    def test_chart_only_response_is_allowed(self):
        response = self.ask("fig, ax = plt.subplots()\nax.bar(['A'], [3])\nshow_chart(fig, 'Count')")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "")
        self.assertEqual(len(response.json()["charts"]), 1)

    def test_follow_up_has_chart_descriptions_not_paths_and_recomputes(self):
        first = self.ask(BAR_SCRIPT).json()
        with patch("app.services.llm.client.call_llm", return_value="print(dataframes['sales.csv']['revenue'].mean())") as model:
            response = self.post_question("Now show the mean instead")
        self.assertEqual(response.status_code, 200, response.text)
        context = model.call_args.args[3]
        self.assertIn("Revenue by region", context)
        self.assertNotIn(first["charts"][0]["url"], context)
        self.assertNotIn(first["turn_id"], context)
        self.assertEqual(response.json()["answer"], "15.0\n")
        self.assertEqual(len(response.json()["history"]), 4)

    def test_failure_preserves_report_and_prior_charts_without_exposing_tracebacks(self):
        first = self.ask(BAR_SCRIPT).json()
        before = get_session(self.job_id)
        response = self.ask("raise ValueError('private file paths')")
        self.assertEqual(response.status_code, 422)
        detail = response.json()["detail"]
        self.assertEqual(detail["charts"], [])
        self.assertEqual(detail["history"][-1]["status"], "error")
        self.assertNotIn("private file paths", response.text)
        self.assertFalse(exploration_directory(self.job_id, detail["turn_id"]).exists())
        self.assertEqual(self.client.get(first["charts"][0]["url"], headers=self.headers).status_code, 200)
        self.assert_report_unchanged(before)

    def test_model_timeouts_and_budgets_do_not_fail_the_report(self):
        for failure, status in ((LLMTimeoutError("exploration_script", 45), 504), (LLMBudgetError("exploration_script", 1), 429)):
            before = get_session(self.job_id)
            with patch("app.services.llm.client.call_llm", side_effect=failure):
                response = self.post_question("Show a chart")
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json()["detail"]["history"][-1]["status"], "error")
            self.assert_report_unchanged(before)

    def test_concurrent_request_returns_conflict_without_model_call(self):
        with explorer.exploration_lock(self.job_id), patch("app.services.llm.client.call_llm") as model:
            response = self.post_question("Show a chart")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(get_session(self.job_id)["explore_history"], [])
        model.assert_not_called()

    def test_empty_or_large_query_is_rejected_before_model(self):
        with patch("app.services.llm.client.call_llm") as model:
            for prompt in ("   ", "x" * 6001):
                response = self.post_question(prompt)
                self.assertEqual(response.status_code, 400)
        model.assert_not_called()

    def test_history_append_keeps_concurrent_session_updates(self):
        def generate(*args, **kwargs):
            update_session(self.job_id, {"calculated_metrics": {"new_metric": 123}, "logs": ["Concurrent update"]})
            return "print('answer')"
        with patch.object(explorer, "generate_exploration_script", side_effect=generate), patch.object(explorer, "execute_script", return_value={"success": True, "stdout": "answer\n"}):
            response = self.post_question("Question")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(get_session(self.job_id)["calculated_metrics"], {"new_metric": 123})
        self.assertEqual(get_session(self.job_id)["logs"], ["Concurrent update"])

    def test_chart_limit_empty_large_text_and_corrupt_image_outputs_are_errors(self):
        for script in (
            "for n in range(4):\n    fig, ax = plt.subplots()\n    ax.bar(['A'], [1])\n    show_chart(fig, 'Too many')",
            "pass", "print('x' * 20001)",
            "open(_exploration_output + '/chart_1.png', 'w').write('not a png')\n_exploration_charts.append({'id':'chart_1','title':'Bad','caption':''})",
        ):
            response = self.ask(script)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(response.json()["detail"]["charts"], [])

    def test_charts_live_in_the_reports_own_folder(self):
        from app.utils.report_paths import ReportPaths
        paths = ReportPaths.for_new_report(f"test-explorer-chart-{uuid.uuid4().hex}").ensure()
        self.job_ids.append(paths.job_id)
        upload = paths.uploads / "sales.csv"
        upload.write_text("region,revenue\nNorth,10\nSouth,20\nNorth,15\n")
        session = get_session(self.job_id)
        create_session(paths.job_id, {
            **session, "job_id": paths.job_id, "explore_history": [],
            "files": [{**session["files"][0], "path": str(upload)}],
            **paths.session_fields(),
        })
        self.job_id = paths.job_id
        response = self.ask(BAR_SCRIPT)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        stored = chart_path(paths.job_id, data["turn_id"], "chart_1", paths.output)
        self.assertTrue(stored.exists())
        self.assertFalse(exploration_directory(paths.job_id, data["turn_id"]).exists(), "nothing in the shared folder")
        self.assertEqual(self.client.get(data["charts"][0]["url"], headers=self.headers).status_code, 200)
        delete_entire_session(paths.job_id)
        self.assertFalse(paths.root.exists())

    def test_cross_session_unknown_and_symlink_assets_are_rejected(self):
        data = self.ask(BAR_SCRIPT).json()
        url = data["charts"][0]["url"]
        other = self.create_report()
        self.assertEqual(self.client.get(url.replace(self.job_id, other), headers=self.headers).status_code, 404)
        self.assertEqual(self.client.get(url.replace("chart_1", "chart_2"), headers=self.headers).status_code, 404)
        path = chart_path(self.job_id, data["turn_id"], "chart_1")
        path.unlink()
        path.symlink_to(Path(get_session(self.job_id)["files"][0]["path"]).absolute())
        self.assertEqual(self.client.get(url, headers=self.headers).status_code, 404)

    def test_deletion_covers_chart_directories_and_scripts(self):
        data = self.ask(BAR_SCRIPT).json()
        directory = exploration_directory(self.job_id, data["turn_id"])
        generated = get_session_generated_paths(self.job_id, get_session(self.job_id))
        self.assertIn(directory, {Path(path).absolute() for path in generated})
        script = Path(f"workspace/scripts/explore_{self.job_id}_{data['turn_id']}.py")
        self.assertTrue(script.exists())
        delete_entire_session(self.job_id)
        self.assertFalse(directory.exists())
        self.assertFalse(script.exists())

    def test_manifest_paths_duplicates_extra_fields_and_size_are_rejected(self):
        turn = uuid.uuid4().hex
        directory = exploration_directory(self.job_id, turn)
        directory.mkdir(parents=True)
        Image.new("RGB", (10, 10)).save(directory / "chart_1.png")
        item = {"id": "chart_1", "title": "Chart", "caption": ""}
        for entries in ([{**item, "id": "../../other"}], [item, item], [{**item, "path": "/etc/passwd"}], [{**item, "title": ""}], [item] * 4):
            (directory / "charts.json").write_text(json.dumps(entries))
            with self.assertRaises(ValueError):
                collect_exploration_charts(self.job_id, turn)
        (directory / "charts.json").write_text(" " * 16001)
        with self.assertRaises(ValueError):
            collect_exploration_charts(self.job_id, turn)
        with self.assertRaises(ValueError):
            chart_path(self.job_id, turn, "../chart_1")

    def test_non_png_large_dimensions_and_large_files_are_rejected(self):
        directory = exploration_directory(self.job_id, uuid.uuid4().hex)
        directory.mkdir(parents=True)
        path = directory / "chart_1.png"
        Image.new("RGB", (10, 10)).save(path, format="JPEG")
        with self.assertRaises(ValueError):
            validate_chart_image(path)
        Image.new("RGB", (2401, 2)).save(path)
        with self.assertRaises(ValueError):
            validate_chart_image(path)
        path.write_bytes(b"x" * (MAX_CHART_BYTES + 1))
        with self.assertRaises(ValueError):
            validate_chart_image(path)

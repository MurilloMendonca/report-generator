"""The MCP server's API client against the real app: token auth, tasks, downloads and sharing."""
import asyncio
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from fpdf import FPDF

import app.routers.api as api
from app.main import app
from app.services import api_token_service
from app.services.cleanup_service import delete_entire_session
from app.services.session_manager import get_session, update_session
from app.utils.report_paths import ReportPaths
from tests.auth_helpers import make_user

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integrations" / "mcp"))
from repgen_mcp.client import RepGenClient, RepGenError  # noqa: E402

HAS_MCP = importlib.util.find_spec("mcp") is not None


def fake_pipeline(job_id):
    """Stands in for the model-driven pipeline: publishes a one-page PDF."""
    session = get_session(job_id)
    pdf_path = Path(ReportPaths.from_session(session).output) / f"{job_id}.r-1.pdf"
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.cell(text="Receita por regiao")
    pdf.output(str(pdf_path))
    update_session(job_id, {"pdf_path": str(pdf_path), "pdf_version": 1, "current_step": "completed",
                            "status": "Relatório concluído", "settings": {**session["settings"], "title": "Receita"}})


class McpClientTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="repgen-mcp-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "data").mkdir()
        (self.root / "data" / "sales.csv").write_text("region,revenue\nNorth,10\nSouth,20\n", encoding="utf-8")
        (self.root / "secret.txt").write_text("not for upload", encoding="utf-8")
        self.user = make_user()
        token = api_token_service.create_token(self.user.id, "mcp", 1)["token"]
        self.http = TestClient(app)
        self.addCleanup(self.http.close)
        self.client = RepGenClient("http://testserver", token, files_root=self.root / "data",
                                   download_dir=self.root / "downloads", http=self.http, sleep=lambda _s: None)

    def create(self):
        with patch.object(api, "run_pipeline", side_effect=fake_pipeline):
            created = self.client.create_report(["sales.csv"], "Receita por região")
        self.addCleanup(delete_entire_session, created["job_id"])
        return created

    def test_report_flow_create_wait_summarize_download_and_share(self):
        created = self.create()
        task = self.client.wait_for_task(created["job_id"], created["task_id"], timeout_seconds=5)
        self.assertEqual(task["state"], "completed")
        self.assertEqual(task["pdf_version"], 1)
        summary = self.client.report_summary(created["job_id"])
        self.assertEqual((summary["title"], summary["files"]), ("Receita", ["sales.csv"]))
        self.assertIn(created["job_id"], [item["job_id"] for item in self.client.list_reports()])
        saved = self.client.download(created["job_id"], "pdf")
        self.assertEqual(saved.parent, (self.root / "downloads").resolve())
        self.assertTrue(saved.read_bytes().startswith(b"%PDF"))
        link = self.client.share(created["job_id"], "mcp", None)
        self.assertRegex(link["url"], r"^http://testserver/share/[A-Za-z0-9_-]{43}$")
        self.assertIsNone(link["expires_at"])

    def test_refinement_returns_a_task(self):
        created = self.create()
        with patch.object(api, "process_chat_refinement") as refine:
            result = self.client.refine(created["job_id"], "Destaque a região Sul")
        refine.assert_called_once()
        self.assertEqual(self.client.task(created["job_id"], result["task_id"])["state"], "completed")

    def test_uploads_stay_inside_the_files_root(self):
        for path in ("../secret.txt", str(self.root / "secret.txt"), "missing.csv"):
            with self.subTest(path=path), self.assertRaises(RepGenError):
                self.client.create_report([path])

    def test_downloads_use_a_safe_file_name_inside_the_download_dir(self):
        target = self.client.download_path("../../etc/passwd")
        self.assertEqual(target, (self.root / "downloads" / "passwd").resolve())

    def test_api_errors_carry_the_server_message(self):
        with self.assertRaises(RepGenError) as raised:
            self.client.report_summary("missing-report")
        self.assertTrue(str(raised.exception).startswith("404"))

    def test_tokens_must_look_like_api_tokens(self):
        with self.assertRaises(RepGenError):
            RepGenClient("http://testserver", "pdf_report_session_cookie", files_root=self.root, download_dir=self.root)


@unittest.skipUnless(HAS_MCP, "mcp package not installed (integrations/mcp/requirements.txt)")
class McpServerTests(unittest.TestCase):
    def test_server_exposes_the_report_tools(self):
        from repgen_mcp.server import build_server
        client = RepGenClient("http://testserver", "rg_" + "x" * 43, files_root=Path("."), download_dir=Path("."))
        with patch.object(client, "list_reports", return_value=[{"job_id": "r1"}]) as listing:
            server = build_server(client)
            names = {tool.name for tool in asyncio.run(server.list_tools())}
            asyncio.run(server.call_tool("list_reports", {"limit": 5}))
        self.assertEqual(names, {"list_reports", "create_report", "wait_for_task", "get_task", "get_report", "refine_report",
                                 "regenerate_report", "ask_question", "get_answer", "download_report", "share_report"})
        listing.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()

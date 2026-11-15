"""HTTP client for the Report Generator API, used by the MCP server.

It authenticates with a personal API token and keeps local files inside two folders:
uploads may only come from ``files_root`` and downloads only go to ``download_dir``.
"""
import mimetypes
import re
import time
from pathlib import Path
from typing import Any, Callable

import httpx

FINAL_TASK_STATES = {"completed", "failed", "superseded"}
FORMATS = {"pdf": ("/pdf", ".pdf"), "docx": ("/docx", ".docx")}


class RepGenError(RuntimeError):
    """An API error, with the server's message when it sent one."""


class RepGenClient:
    def __init__(self, base_url: str, token: str, *, files_root: Path, download_dir: Path,
                 http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = 120.0):
        if not token or not token.startswith("rg_"):
            raise RepGenError("Defina REPGEN_API_TOKEN com um token criado na página Conta (rg_...).")
        self.files_root = Path(files_root).resolve()
        self.download_dir = Path(download_dir).resolve()
        self.sleep = sleep
        self.http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self.http.headers.update({"Authorization": f"Bearer {token}", "User-Agent": "repgen-mcp"})

    # --- HTTP -------------------------------------------------------------------------

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        response = self.http.request(method, path, **kwargs)
        if response.is_success:
            return response
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("code")
        elif isinstance(detail, list):
            detail = "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
        raise RepGenError(f"{response.status_code}: {detail or response.reason_phrase}")

    def json(self, method: str, path: str, **kwargs) -> Any:
        return self.request(method, path, **kwargs).json()

    # --- Local files --------------------------------------------------------------------

    def upload_path(self, value: str) -> Path:
        path = (self.files_root / value).resolve()
        if not path.is_relative_to(self.files_root):
            raise RepGenError(f"Só é possível enviar arquivos dentro de {self.files_root}.")
        if not path.is_file():
            raise RepGenError(f"Arquivo não encontrado: {value}")
        return path

    def download_path(self, filename: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "relatorio"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        return self.download_dir / safe

    # --- Reports ------------------------------------------------------------------------

    def list_reports(self, limit: int = 20) -> list[dict]:
        sessions = self.json("GET", "/api/sessions")["sessions"][:max(1, limit)]
        keys = ("job_id", "initial_prompt", "status", "file_count", "pdf_version", "created_at", "updated_at")
        return [{key: item.get(key) for key in keys if key in item} for item in sessions]

    def create_report(self, files: list[str], goal: str = "", provider: str | None = None,
                      model_name: str | None = None, page_count: int | None = None) -> dict:
        if not files:
            raise RepGenError("Informe ao menos um arquivo de dados.")
        paths = [self.upload_path(item) for item in files]
        data = {"prompt": goal or ""}
        if provider:
            data["provider"] = provider
        if model_name:
            data["model_name"] = model_name
        if page_count:
            data["page_count"] = str(page_count)
        handles = [path.open("rb") for path in paths]
        try:
            upload = [("files", (path.name, handle, mimetypes.guess_type(path.name)[0] or "application/octet-stream"))
                      for path, handle in zip(paths, handles)]
            return self.json("POST", "/api/sessions", data=data, files=upload)
        finally:
            for handle in handles:
                handle.close()

    def report_summary(self, job_id: str) -> dict:
        session = self.json("GET", f"/api/sessions/{job_id}")
        review = session.get("quality_review") or {}
        return {
            "job_id": job_id,
            "title": (session.get("settings") or {}).get("title"),
            "status": session.get("status"),
            "current_step": session.get("current_step"),
            "pdf_version": session.get("pdf_version"),
            "error": session.get("error") or None,
            "files": [item.get("original_filename") for item in session.get("files", [])],
            "components": [{"id": item.get("id"), "type": item.get("type"), "indicator": item.get("indicator")}
                           for item in session.get("artifacts", []) if item.get("type") != "metric"],
            "metrics": {key: value.get("value") if isinstance(value, dict) else value
                        for key, value in (session.get("calculated_metrics") or {}).items()},
            "review": {"status": review.get("status"), "pages": review.get("page_count"),
                       "issues": [{"severity": issue.get("severity"), "component_id": issue.get("component_id"),
                                   "message": issue.get("message")} for issue in review.get("issues", [])[:30]]},
        }

    def task(self, job_id: str, task_id: str) -> dict:
        return self.json("GET", f"/api/sessions/{job_id}/tasks/{task_id}")

    def wait_for_task(self, job_id: str, task_id: str, timeout_seconds: int = 900, poll_seconds: float = 10) -> dict:
        deadline = time.monotonic() + max(1, timeout_seconds)
        while True:
            task = self.task(job_id, task_id)
            if task.get("state") in FINAL_TASK_STATES or time.monotonic() >= deadline:
                return task
            self.sleep(poll_seconds)

    def refine(self, job_id: str, instruction: str) -> dict:
        return self.json("POST", f"/api/sessions/{job_id}/chat", data={"prompt": instruction})

    def regenerate(self, job_id: str, scope: str = "all", settings: dict | None = None) -> dict:
        return self.json("POST", f"/api/sessions/{job_id}/regenerate", json={"scope": scope, **({"settings": settings} if settings else {})})

    def update_settings(self, job_id: str, settings: dict) -> dict:
        return self.json("PUT", f"/api/sessions/{job_id}/settings", json=settings)

    def ask(self, job_id: str, question: str) -> dict:
        started = self.json("POST", f"/api/sessions/{job_id}/explore", data={"prompt": question})
        return {"turn_id": started.get("turn_id"), "task_id": started.get("task_id")}

    def answer(self, job_id: str, turn_id: str) -> dict:
        history = self.json("GET", f"/api/sessions/{job_id}").get("explore_history", [])
        turn = next((item for item in history if isinstance(item, dict) and item.get("role") == "assistant"
                     and item.get("turn_id") == turn_id), None)
        if turn is None:
            raise RepGenError("Pergunta não encontrada neste relatório.")
        return {"status": turn.get("status"), "answer": turn.get("content"),
                "charts": [chart.get("title") or chart.get("id") for chart in turn.get("charts", []) if isinstance(chart, dict)]}

    def download(self, job_id: str, fmt: str = "pdf") -> Path:
        if fmt not in FORMATS:
            raise RepGenError("Formato deve ser pdf ou docx.")
        path_suffix, extension = FORMATS[fmt]
        response = self.request("GET", f"/api/sessions/{job_id}{path_suffix}")
        target = self.download_path(f"relatorio-{job_id}{extension}")
        target.write_bytes(response.content)
        return target

    def share(self, job_id: str, label: str = "", expires_in_hours: int | None = 168) -> dict:
        link = self.json("POST", f"/api/sessions/{job_id}/share-links", json={"label": label, "expires_in_hours": expires_in_hours})
        return {"url": str(self.http.base_url).rstrip("/") + link["path"], "expires_at": link.get("expires_at"), "id": link.get("id")}

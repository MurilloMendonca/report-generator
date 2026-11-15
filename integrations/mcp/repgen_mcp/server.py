"""MCP server (stdio) that lets an assistant create, refine, question and share reports.

Configuration (environment):
  REPGEN_URL            Report Generator address, for example https://repgen.example.com
  REPGEN_API_TOKEN      personal API token from the account page (rg_...)
  REPGEN_FILES_ROOT     folder the server may upload files from (default: current folder)
  REPGEN_DOWNLOAD_DIR   folder where PDFs and DOCX files are saved (default: ./repgen-downloads)
"""
import os
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from repgen_mcp.client import RepGenClient

INSTRUCTIONS = (
    "Gera relatórios analíticos em PDF a partir de arquivos de dados (CSV, XLSX, JSON) e documentos de contexto. "
    "Fluxo típico: create_report -> wait_for_task -> get_report -> refine_report ou ask_question -> download_report ou share_report. "
    "Criar, refinar e regenerar levam minutos e respondem com um task_id; use wait_for_task para esperar."
)


def build_server(client: RepGenClient) -> MCPServer:
    server = MCPServer("repgen", instructions=INSTRUCTIONS)

    @server.tool()
    def list_reports(limit: int = 20) -> list[dict]:
        """Lista os relatórios mais recentes da conta, com id, título e estado."""
        return client.list_reports(limit)

    @server.tool()
    def create_report(files: list[str], goal: str = "", page_count: int | None = None,
                      provider: str | None = None, model_name: str | None = None) -> dict:
        """Cria um relatório. `files` são caminhos relativos a REPGEN_FILES_ROOT; `goal` é o objetivo da análise
        (vazio gera um relatório exploratório). Devolve job_id e task_id; use wait_for_task em seguida."""
        return client.create_report(files, goal, provider, model_name, page_count)

    @server.tool()
    def wait_for_task(job_id: str, task_id: str, timeout_seconds: int = 900) -> dict:
        """Espera uma tarefa terminar (completed, failed ou superseded) e devolve o estado final."""
        return client.wait_for_task(job_id, task_id, min(timeout_seconds, 1800))

    @server.tool()
    def get_task(job_id: str, task_id: str) -> dict:
        """Estado atual de uma tarefa, sem esperar."""
        return client.task(job_id, task_id)

    @server.tool()
    def get_report(job_id: str) -> dict:
        """Resumo do relatório: título, estado, componentes, métricas calculadas e avisos da revisão."""
        return client.report_summary(job_id)

    @server.tool()
    def refine_report(job_id: str, instruction: str) -> dict:
        """Pede uma mudança em linguagem natural (texto, gráficos, layout, foco da análise). Devolve task_id."""
        return client.refine(job_id, instruction)

    @server.tool()
    def regenerate_report(job_id: str, scope: str = "all", page_count: int | None = None, language: str | None = None) -> dict:
        """Regera textos (texts), gráficos (graphics) ou tudo (all), opcionalmente com nova quantidade de páginas ou idioma."""
        settings = {key: value for key, value in (("page_count", page_count), ("language", language)) if value is not None}
        return client.regenerate(job_id, scope, settings or None)

    @server.tool()
    def ask_question(job_id: str, question: str) -> dict:
        """Faz uma pergunta sobre os dados do relatório sem alterá-lo. Use wait_for_task e depois get_answer."""
        return client.ask(job_id, question)

    @server.tool()
    def get_answer(job_id: str, turn_id: str) -> dict:
        """Resposta de uma pergunta feita com ask_question."""
        return client.answer(job_id, turn_id)

    @server.tool()
    def download_report(job_id: str, format: str = "pdf") -> str:
        """Salva o relatório publicado em REPGEN_DOWNLOAD_DIR como pdf ou docx e devolve o caminho do arquivo."""
        return str(client.download(job_id, format))

    @server.tool()
    def share_report(job_id: str, label: str = "", expires_in_hours: int | None = 168) -> dict:
        """Cria um link somente leitura, que abre sem login. expires_in_hours nulo cria um link sem expiração."""
        return client.share(job_id, label, expires_in_hours)

    return server


def main() -> None:
    url = os.environ.get("REPGEN_URL", "http://localhost:8000")
    client = RepGenClient(url, os.environ.get("REPGEN_API_TOKEN", ""),
                          files_root=Path(os.environ.get("REPGEN_FILES_ROOT", ".")),
                          download_dir=Path(os.environ.get("REPGEN_DOWNLOAD_DIR", "repgen-downloads")))
    build_server(client).run("stdio")


if __name__ == "__main__":
    main()

# Report Generator MCP server

An [MCP](https://modelcontextprotocol.io) server (stdio) over the Report Generator API. An assistant that
supports MCP (Claude Desktop, Claude Code, editors with MCP clients) can then create reports from local
data files, wait for them, read the review, refine them in plain language, ask questions about the data,
download the PDF or DOCX and create share links.

## Setup

1. On the account page of your Report Generator, create an API token (`rg_...`). It acts as you on
   reports, sources and templates, but cannot change the account, provider keys or database connections.
2. Install the dependencies (Python 3.11+): `pip install -r integrations/mcp/requirements.txt`.
3. Register the server in your MCP client. For Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "repgen": {
      "command": "python",
      "args": ["-m", "repgen_mcp.server"],
      "env": {
        "PYTHONPATH": "/path/to/pdf-report-generator/integrations/mcp",
        "REPGEN_URL": "https://repgen.example.com",
        "REPGEN_API_TOKEN": "rg_...",
        "REPGEN_FILES_ROOT": "/path/to/your/data",
        "REPGEN_DOWNLOAD_DIR": "/path/to/your/reports"
      }
    }
  }
}
```

For Claude Code: `claude mcp add repgen --env REPGEN_URL=... --env REPGEN_API_TOKEN=rg_... --env PYTHONPATH=/path/to/integrations/mcp -- python -m repgen_mcp.server`.

## Tools

| Tool | What it does |
| --- | --- |
| `list_reports` | Recent reports of the account. |
| `create_report` | Uploads files from `REPGEN_FILES_ROOT` with a goal (empty for an exploratory report). Returns `job_id` and `task_id`. |
| `wait_for_task`, `get_task` | Waits for, or reads, a task: `completed`, `failed` or `superseded`. |
| `get_report` | Title, state, components, calculated metrics and review warnings. |
| `refine_report` | Plain-language change request. Returns a `task_id`. |
| `regenerate_report` | Regenerates texts, graphics or everything, optionally with a page count or language. |
| `ask_question`, `get_answer` | Asks about the report's data without changing it. |
| `download_report` | Saves the PDF or DOCX to `REPGEN_DOWNLOAD_DIR`. |
| `share_report` | Creates a read-only link; `expires_in_hours: null` never expires. |

## Local files

Uploads are read only from inside `REPGEN_FILES_ROOT` (default: the current folder); paths that leave it are
refused. Downloads are written only to `REPGEN_DOWNLOAD_DIR`, with a sanitized file name. Generation runs on
the server with the account's provider keys and limits, as it does from the browser.

## Tests

`tests/test_mcp_integration.py` runs the client against the real app (token auth, tasks, downloads,
sharing) with the model-driven pipeline mocked, and checks the server's tool list when `mcp` is installed.

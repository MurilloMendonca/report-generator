# Report Generator

FastAPI application that uses LLMs to turn uploaded CSV, Excel, and PDF context files into analytical PDF reports. The app provides a browser workspace where users can upload and inspect files, optionally preprocess data, generate report components, edit indicators and layout, refine the report through chat, ask ad-hoc questions about the data, inspect generated Python, and download the final PDF.

The current UI text is mostly Portuguese, and the default report language is Portuguese.

## What It Does

1. Accepts one or more CSV/XLS/XLSX files plus optional PDF context files.
   The report objective is optional; blank prompts generate an exploratory report.
2. Extracts metadata from each dataset: shape, columns, null counts, unique counts, numeric stats, and sample rows.
3. Detects optional data preprocessing requests before analysis. This step can filter rows, create calculated columns, normalize values, aggregate records, or otherwise prepare tabular data once for all downstream metrics and components.
4. Sends metadata and the user objective to an LLM to plan report artifacts:
   - `metric`: calculated values used by narrative text.
   - `graphic`, `bar`, `line`, `pie`, `scatter`, `table`, `chart`: visual PDF components.
   - `text`: narrative report sections.
5. Calculates metric artifacts by asking the LLM for short Python snippets, executing them, and storing the returned values.
6. Selects or generates an A4 layout template with millimeter coordinates.
7. Generates Python render functions for visual components and a shared text-generation response for text components.
8. Assembles a standalone Python report script, executes it in a bubblewrap sandbox, and writes the PDF.
9. If execution fails, asks the LLM to repair the whole script and retries up to five times.

## Tech Stack

- Backend: FastAPI, Uvicorn (Python 3.12, pinned in `.python-version`)
- Data: pandas, openpyxl
- Charts: matplotlib, seaborn
- PDF rendering: fpdf2
- LLM providers:
  - Google Gemini via `google-genai`
  - DeepSeek via OpenAI-compatible client
- Generated-code isolation: bubblewrap (`bwrap`)
- Frontend: static HTML/CSS/JavaScript
- Runtime persistence: JSON files under `workspace/sessions`

## Repository Layout

```text
app/
  main.py                    App setup: auth middleware, startup checks, router wiring
  security.py                Login secret, signed session tokens, revocation, rate limit, headers
  routers/api.py             All HTTP routes
  models/
    session.py               Pydantic session schema and legacy migrations
    refinement.py            Schema for chat-refinement routing decisions
  services/
    llm/                     LLM calls, one module per concern (see "LLM Package" below)
    pipeline_service.py      Report-generation pipeline stages
    executor_service.py      Sandboxed generated-script execution
    exploration_service.py   Ad-hoc data explorer (questions and charts)
    job_runner.py            Limits how many report jobs run at once
    session_manager.py       JSON-backed session persistence and restart recovery
    cleanup_service.py       Session deletion, retention cleanup, disk usage
    web_helpers.py           Upload validation, file responses, chat refinement handling
  utils/
    report_paths.py          Where a report's files live and what its sandbox may see
    data_analyzer.py         CSV/Excel metadata extraction
    tabular.py               Shared CSV/Excel loader (also used inside the sandbox)
    artifact_validation.py   Validation of LLM-planned artifacts against the data
    semantic_model.py        Column roles (measures, dimensions, dates) across datasets
    component_library.py     Visual presets, themes, and chart styles
    component_renderers.py   Library renderers and the sandbox runtime
    exploration_charts.py    Explorer chart storage and validation
    layout.py                Layout normalization
    template_library.py      Built-in and saved A4 layout blueprints
    model_config.py          Provider/model catalog and defaults
prompts/                     System prompts used for each LLM task
static/
  index.html                 Main browser workspace
  templates.html             Layout-template editor
  css/styles.css             Visual styling
  js/api.js                  HTTP client wrapper
  js/app.js                  Frontend state and event handling
  js/ui.js                   UI render helpers
  js/layout_editor.js        Shared drag/resize layout editor
  js/templates.js            Template editor page logic
  js/utils.js                Drag/drop, tab, JSON helpers
tests/                       unittest suite, Node specs, browser checks, canonical sessions
workspace/
  tenants/default/reports/{job_id}/
    uploads/                 This report's uploaded files
    filtered/                Its preprocessed tables
    scripts/                 Its generated scripts
    output/                  Its PDFs, previews, manifests and explorer charts
  uploads/, filtered/,       Shared folders used only by sessions created before
  scripts/, reports/           the per-report layout
  sessions/                  Session JSON files
  auth/                      Revoked session tokens
  sandbox_support/           Runtime guard and helpers loaded inside the sandbox
requirements.txt             Python dependencies
requirements-lock.txt        Frozen dependency set for reproducible installs
list_models.py               Helper script for listing Gemini models
```

The `workspace/` directories are created automatically when the app starts.

## Setup

Create a Python 3.12 virtual environment and install the locked dependency set:

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -c requirements-lock.txt
```

This is the same install CI and Docker use. Plain `pip install -r requirements.txt` also works but may resolve newer versions than the tested set.

Install bubblewrap, which isolates the LLM-generated Python (see "Security Notes"):

```bash
sudo apt-get install bubblewrap     # Debian/Ubuntu
```

On Ubuntu 24.04 and later, unprivileged user namespaces are restricted by AppArmor, which stops bubblewrap. If the app logs that bubblewrap is unavailable, allow them:

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

Create a `.env` file with at least one provider key:

```bash
GEMINI_API_KEY=your_google_gemini_key
DEEPSEEK_API_KEY=your_deepseek_key
APP_ACCESS_SECRET=your_long_random_login_secret
```

The default provider is `deepseek`, so `DEEPSEEK_API_KEY` is required for the default path. `GEMINI_API_KEY` is required when selecting Gemini. `APP_ACCESS_SECRET` is the login secret used by the browser UI. After login, the app issues a signed HttpOnly session cookie; the browser does not store the raw secret.

## Configuration

All settings are environment variables (`.env` is loaded automatically).

| Variable | Default | Purpose |
|---|---|---|
| `APP_ACCESS_SECRET` | `change-me` (dev only) | Login secret. Required outside development; `PDF_REPORT_ACCESS_SECRET` is an alias. |
| `APP_ENV` | `development` | `development`, `dev`, `local`, or `test` relax the checks below; anything else is production. |
| `APP_ADMIN_SECRET` | unset | Required in the `X-Admin-Secret` header for `/api/admin/*`. Without it, admin endpoints only work in development. |
| `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` | unset | Provider keys. |
| `PDF_REPORT_COOKIE_SECURE` | `1` in production, `0` in development | Send the session cookie over HTTPS only. Set `0` only for plain HTTP on a non-localhost address. |
| `PDF_REPORT_SESSION_TTL_SECONDS` | `28800` (8 h) | Login session lifetime. |
| `PDF_REPORT_LOGIN_MAX_FAILURES` / `PDF_REPORT_LOGIN_WINDOW_SECONDS` | `10` / `300` | Failed logins allowed per client address before login returns 429. |
| `PDF_REPORT_ALLOW_UNSAFE_SANDBOX` | unset | Set `1` to run generated code under the weaker Python-level guard when bubblewrap is unavailable. Trusted environments only. |
| `PDF_REPORT_SANDBOX_MAX_FILE_BYTES` | `209715200` (200 MB) | Largest file a generated script may write. |
| `PDF_REPORT_MAX_CONCURRENT_JOBS` | `2` | Report jobs (pipeline, refinement, render) that run at once; others wait. |
| `PDF_REPORT_RECOVER_SESSIONS_ON_START` | `1` (`0` when `APP_ENV=test`) | Mark sessions interrupted by a restart as failed on startup. |
| `PDF_REPORT_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Per-file upload limit. |
| `PDF_REPORT_MAX_REQUEST_UPLOAD_BYTES` | `94371840` (90 MB) | Total size of all files in one upload request. Cloudflare's free plan rejects bodies over 100 MB. |
| `PDF_REPORT_MAX_EXCEL_SHEETS` | `20` | Sheets allowed per Excel workbook. |
| `PDF_REPORT_CLIENT_IP_HEADER` | unset (compose: `CF-Connecting-IP`) | Header holding the real client address behind a proxy. Used only for requests from a trusted proxy. |
| `PDF_REPORT_TRUSTED_PROXIES` | unset (compose: `cloudflared` plus Docker networks) | Comma-separated hostnames, IPs or CIDR ranges whose `PDF_REPORT_CLIENT_IP_HEADER` is believed. |
| `PDF_REPORT_LLM_TIMEOUT_SECONDS` | `45` | Timeout per LLM call. Report settings can override it. |
| `PDF_REPORT_LLM_CALL_BUDGET` | `0` (unlimited) | Maximum LLM calls per top-level operation. Report settings can override it. |
| `PDF_REPORT_LLM_FALLBACK_PROVIDER` / `PDF_REPORT_LLM_FALLBACK_MODEL_NAME` | unset | Provider/model to try when the primary provider fails. |
| `PDF_REPORT_DOCUMENT_LLM_PROVIDER` / `PDF_REPORT_DOCUMENT_LLM_MODEL_NAME` | `gemini` / provider default | Model that reads PDF context files when the selected model isn't a PDF-capable Gemini model. |

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open:

```text
http://localhost:8000
```

The module also supports direct execution:

```bash
python app/main.py
```

On startup the app checks that bubblewrap works and logs an error if it doesn't. Report generation, metrics, and the explorer fail with a `sandbox_unavailable` error until it is fixed (or `PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1` is set).

## Docker Run

Create `.env` as shown above, then build and run:

```bash
docker compose up -d --build
```

The app will be available at:

```text
http://localhost:8000
```

Useful commands:

```bash
docker compose logs -f
docker compose restart
docker compose down
```

Runtime files are persisted on the host through:

```text
./workspace:/app/workspace
```

The app's port is published on `127.0.0.1` only, so the machine itself can reach it but the rest of the network can't. To change the host port:

```bash
HOST_PORT=5555 docker compose up -d --build
```

### Cloudflare Tunnel

The app can be served through a Cloudflare Tunnel in two ways:

- **`cloudflared` inside compose (recommended).** Put the tunnel token in `.env` as `CLOUDFLARE_TUNNEL_TOKEN`, and in the Cloudflare dashboard point the public hostname at `http://report-generator:8000`. Then start with the `tunnel` profile:

  ```bash
  docker compose --profile tunnel up -d --build
  ```

- **`cloudflared` on the host,** pointing at `http://localhost:8000` (or your `HOST_PORT`).

Either way, Cloudflare's real client address arrives in `CF-Connecting-IP`, and compose tells the app to trust it from `cloudflared` and Docker's networks (`PDF_REPORT_TRUSTED_PROXIES`). Login rate limits are then per visitor, not per tunnel. Authenticated responses send `Cache-Control: private, no-store`; also add a Cloudflare cache rule that bypasses the cache for `/api/*` and `/session/*`.

Cloudflare drops requests that take longer than about 100 seconds. Report generation, explorer answers and workspace translation all run as background jobs that the UI polls, so no request waits that long.

The container runs as a non-root user (UID/GID 1000 by default, configurable with the `APP_UID` and `APP_GID` build args), with a read-only root filesystem, all Linux capabilities dropped, and `no-new-privileges`. Notes:

- `./workspace` must be writable by that user. If an older container created it as root, run `sudo chown -R 1000:1000 workspace` first.
- Compose sets `APP_ENV=production`, so the session cookie is HTTPS-only. Serving plain HTTP on a non-localhost address needs `PDF_REPORT_COOKIE_SECURE=0`.
- bubblewrap needs to create namespaces, which Docker's default seccomp and AppArmor profiles block, so compose sets `seccomp=unconfined` and `apparmor=unconfined`. Removing them requires `PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1`, which gives generated code weaker isolation.

## Main Runtime Flow

The pipeline starts from `POST /api/sessions` and runs in a FastAPI background task, through `job_runner.run_job` so at most `PDF_REPORT_MAX_CONCURRENT_JOBS` jobs run at once.

1. `start_session` (`app/routers/api.py`) creates the report's folder under `workspace/tenants/`, saves the uploads into its `uploads/`, and creates a session JSON file that records the folder as `storage_dir`.
2. `run_pipeline` (`pipeline_service.py`) enriches missing file artifacts through `extract_metadata`.
3. If the goal asks to transform data first, `generate_data_filter` asks the LLM for a `preprocess_dataframes(dataframes)` function. The app executes it once and writes CSV outputs under `workspace/filtered`.
4. `generate_artifacts` asks the LLM for report component definitions using the raw or preprocessed metadata.
5. `calculate_metric_value` executes generated metric scripts and stores `calculated_metrics`.
6. The layout stage selects a saved template or normalizes the generated layout into A4 coordinates.
7. `generate_component_code` creates Python render functions for charts/tables.
8. `generate_all_texts` creates all narrative text in one LLM response for consistency.
9. `assemble_script` combines imports, helper functions, component functions, data loading, layout calls, and PDF output.
10. `execute_script` writes the generated script under `workspace/scripts` and runs it in the sandbox.
11. `run_execution` stores the PDF version on success or calls `fix_script` and retries on failure.

Preprocessed files are treated as the analytical input for all metrics and components, while the original upload remains available in the file workspace.

If the server restarts mid-job, the job is lost; on the next startup that session is marked `failed` with error code `interrupted` so the UI stops waiting and the user can retry.

## LLM Package

`app/services/llm/` holds all LLM interaction, split by concern:

| Module | Contents |
|---|---|
| `client.py` | `call_llm`, retries, timeouts, call budgets, provider fallback, PDF calls |
| `providers/` | One adapter per provider (`gemini.py`, `openai_compatible.py` for DeepSeek), registered in `registry.py`. Adapters build the SDK client, send the request, and report token counts. |
| `planning.py` | Report plan (`generate_artifacts`), document insights, layout template, data preprocessing, title |
| `metrics.py` | `calculate_metric_value` and the shared dataframe loader line |
| `components.py` | Generated render code for visual components |
| `texts.py` | Narrative texts and condensing them to fit their layout slots |
| `script.py` | `assemble_script`, `fix_script`, and explorer scripts |
| `refinement.py` | Chat refinement routing, workspace translation, component edits |
| `parsing.py` | Strict parsing of model output (JSON contracts, code fences, metric values) |
| `errors.py` | `LLMContractError`, `LLMBudgetError`, `LLMTimeoutError` |
| `prompts.py` | Loading files from `prompts/` |

Import from the submodules directly; the package deliberately has no re-exports. Every module calls `client.call_llm(...)` through the module, so tests mock all providers with a single `patch("app.services.llm.client.call_llm", ...)`. `tests/test_llm_package_layout.py` enforces this.

## API Endpoints

- `POST /api/auth/login`: validates the access secret and sets the session cookie. Returns 429 with `Retry-After` after too many failures.
- `POST /api/auth/logout`: revokes the current session token and clears the cookie.
- `GET /api/auth/check`: validates the current session token.
- `GET /`: serves the static workspace UI.
- `GET /session/{job_id}`: serves the workspace already pointed at a session.
- `GET /templates`: serves the layout-template editor.
- `GET /api/models`: lists available providers and models.
- `GET /api/component-library`: lists visual presets, themes, and chart styles.
- `GET /api/component-library/previews`: preview images for presets, given a `theme` and `style`.
- `GET /api/component-library/presets/{preset_id}/preview`: one preset's preview image.
- `POST /api/sessions`: starts a report job. Form fields:
  - `files`: one or more CSV/Excel/PDF files.
  - `prompt`: report objective. Blank means exploratory report.
  - `provider`: `gemini` or `deepseek`; default `deepseek`.
  - `model_name`: provider model name.
- `GET /api/sessions`: returns recent sessions.
- `GET /api/sessions/{job_id}`: returns the full session state.
- `DELETE /api/sessions/{job_id}`: deletes the session record and all associated workspace files (uploads, scripts, reports, JSON).
- `POST /api/sessions/{job_id}/files`: adds more files and reruns the pipeline.
- `GET /api/sessions/{job_id}/files/{file_index}/preview`: previews an uploaded file. Use `?variant=preprocessed` when available.
- `GET /api/sessions/{job_id}/files/{file_index}/download`: downloads an uploaded file. Use `?variant=preprocessed` when available.
- `DELETE /api/sessions/{job_id}/files/{file_index}`: removes a file from the session and reruns when files remain.
- `POST /api/sessions/{job_id}/chat`: routes refinement feedback to a specific component, layout, settings, or full regeneration.
- `PUT /api/sessions/{job_id}/artifacts`: manually updates artifact definitions and optional component code overrides.
- `PUT /api/sessions/{job_id}/template`: manually updates layout coordinates and rerenders.
- `PUT /api/sessions/{job_id}/settings`: updates report settings plus provider/model. A language change queues workspace translation as a background job and returns `{"status": "queued", "translating": true}`; the session shows `current_step: "translating"` until it finishes.
- `POST /api/sessions/{job_id}/regenerate`: regenerates `texts`, `graphics`, one `component`, or `all` using the current provider/model/settings.
- `GET /api/sessions/{job_id}/pdf`: downloads or previews the generated PDF.
- `GET /api/sessions/{job_id}/components/{component_id}/preview`: renders a single component to a small PDF.
- `POST /api/sessions/{job_id}/explore`: asks an ad-hoc question about the session data. Returns `202` with the question stored and a `pending` answer; the answer (text and up to three charts) is produced in the background and replaces the pending entry in `explore_history`. Returns `409` while another question is pending.
- `GET /api/sessions/{job_id}/explore/{turn_id}/charts/{chart_id}`: returns an explorer chart PNG. Add `?download=true` to download it.
- `GET /api/layout-templates`: lists available layout templates.
- `POST /api/layout-templates`: creates a layout template.
- `PUT /api/layout-templates/{template_id}`: updates a layout template.
- `DELETE /api/layout-templates/{template_id}`: deletes a layout template.
- `GET /api/admin/workspace-usage`: returns disk usage statistics for each workspace directory. Requires `X-Admin-Secret`.
- `POST /api/admin/cleanup`: manually triggers the cleanup of expired artifacts based on the retention policy. Requires `X-Admin-Secret`.

All `/api/*` endpoints except `/api/auth/login` require a valid signed session cookie. `X-Access-Token` is still accepted for non-browser clients, but it must contain a signed session token returned by the app, not the raw access secret. Session IDs must match `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`; anything else returns 404.

## Workspace Maintenance

The application includes a data lifecycle management system to prevent the indefinite accumulation of files.

### Retention Policy

The default retention policy is:
- **Sessions:** 30 days.
- **Reports (PDFs):** 30 days.
- **Uploads:** 30 days.
- **Scripts:** 7 days.
- **Filtered/Preprocessed data:** 7 days.

Files older than these limits are subject to automatic removal during cleanup tasks.

### Cleanup Tasks

- **Session Deletion:** Using `DELETE /api/sessions/{job_id}` removes all physical files linked to that session across all workspace subdirectories.
- **Automated Cleanup:** The `POST /api/admin/cleanup` endpoint triggers a scan of the workspace to remove orphaned files or expired sessions based on the configured retention policy.

### Disk Usage

The `GET /api/admin/workspace-usage` endpoint provides a breakdown of storage consumption by category (uploads, scripts, reports, etc.), helping administrators monitor workspace growth.

## Session Shape

Sessions are normalized by `app/models/session.py` and stored as `workspace/sessions/{job_id}.json`. Important fields:

- `schema_version`: current session schema version.
- `files`: uploaded file records with original name, saved path, extracted metadata, and optional preprocessing output.
  - `path`: original uploaded file.
  - `raw_artifact`: metadata for the original file.
  - `artifact`: metadata currently used by the pipeline. This is preprocessed metadata when preprocessing is active.
  - `preprocessed_path`: generated CSV used by metrics/components.
  - `preprocessing_description`: user-facing description of the preprocessing step.
  - `filtered_path` and `filter_description`: legacy aliases kept for older sessions.
- `initial_prompt`: first user objective.
- `current_goal`: initial prompt plus refinement history.
- `is_exploratory`: true when the initial prompt was blank and the default exploratory objective was used.
- `refinement_history`: list of chat refinement prompts.
- `artifacts`: LLM-planned report components.
- `template`: positioned component blocks with `x`, `y`, `w`, and `h` in millimeters.
- `components`: generated Python render functions keyed by artifact ID.
- `calculated_metrics`: metric ID to calculated value.
- `settings`: report title, font, font size, alignment, line height, and language.
- `provider` and `model_name`: selected LLM provider/model for generation and regeneration.
- `data_preprocessing`: optional preprocessing decision and Python function.
- `data_filter`: legacy alias for `data_preprocessing`.
- `script`: assembled Python report script.
- `script_path`: generated script path.
- `pdf_path`: output PDF path.
- `pdf_version`: incremented when a new PDF is successfully rendered.
- `current_step` and `error`: pipeline stage and the last structured error (`code`, `message`, `stage`, `retryable`).
- `explore_history`: ad-hoc explorer conversation history.

## Prompt Contracts

The LLM behavior is controlled by Markdown prompt files in `prompts/`:

- `GENERATE_ARTIFACTS.md`: returns JSON list of metric, visual, and text artifact definitions.
- `GENERATE_METRIC_VALUE.md`: returns Python code that prints `{"value": ...}`.
- `GENERATE_TEMPLATE.md`: legacy layout prompt kept for reference. The current pipeline builds layout coordinates from the local template library in `app/utils/template_library.py`.
- `GENERATE_COMPONENT_CODE.md`: returns one Python render function for a visual component.
- `GENERATE_ALL_TEXTS.md`: returns JSON object mapping text artifact IDs to text content.
- `GENERATE_DATA_FILTER.md`: returns optional `preprocess_dataframes(dataframes)` code for data preprocessing. The filename is legacy; the current contract supports filtering, calculated columns, aggregation, normalization, and related preparation.
- `GENERATE_REPORT_TITLE.md`: returns a short report title.
- `EXTRACT_DOCUMENT_INSIGHTS.md`: returns structured insights from PDF context files.
- `REFINE_MODULAR.md`: returns a routing target: component ID, `LAYOUT`, `SETTINGS`, or `ALL`.
- `FIX_SCRIPT.md`: returns a full corrected Python script after execution failure.
- `EXPLORE_DATA.md`: returns Python code that prints an ad-hoc answer.
- `TRANSLATE_WORKSPACE.md`: returns translated artifacts and component code.

Most structured LLM outputs are parsed with schema validation and clear diagnostics (`app/services/llm/parsing.py`). Keep prompt output formats strict when changing these files.

## Frontend Behavior

The frontend is a static single-page workspace:

- `static/js/app.js` owns session state, polling, form handlers, and event flow.
- `static/js/api.js` wraps calls to the FastAPI endpoints.
- `static/js/ui.js` renders logs, artifacts, files, layout canvas, metadata, settings, and full generated script.
- `static/js/layout_editor.js` provides the drag/resize layout editor shared by the workspace and the template editor.
- `static/js/templates.js` drives the layout-template editor page.
- `static/js/utils.js` provides tab switching, JSON formatting, and drag/resize behavior.

After starting a session, the UI polls every two seconds until the status indicates completion or failure. PDF refresh is keyed by `pdf_version`.

File cards support drag-and-drop uploads, preview, download, and removal. When preprocessing produced a derived CSV, the file modal exposes tabs for `Original` and `Pré-processado`, and download uses the selected variant.

The settings panel can switch provider/model for future generation. The regeneration actions update only texts, only graphics, or the full report.

The Content-Security-Policy allows scripts only from the app's own origin, so inline `<script>` blocks and `on*=` attributes don't run. Keep browser code in `static/js/`.

## Generated Report Script Contract

`assemble_script` builds a Python script that:

- Imports pandas, numpy, matplotlib, seaborn, fpdf2, tempfile, and os.
- Defines `render_text_block`.
- Includes every generated component function.
- Loads uploaded or preprocessed datasets into a `dataframes` dictionary keyed by original filename.
- Creates one A4-ish FPDF page.
- Draws the configured title.
- Calls each component function with `pdf`, `dataframes`, `x`, `y`, `w`, `h`, and a backward-compatible `df` keyword when possible.
- Writes the PDF to `workspace/reports/{job_id}.pdf`.

Visual components are expected to render themselves as temporary matplotlib images and insert them into the PDF.

## Security Notes

- The app executes LLM-generated Python for preprocessing, metrics, exploration, and PDF rendering. Uploaded data reaches the model, so a crafted file could steer what code is generated; the sandbox is the boundary that contains it.
- Generated scripts run under bubblewrap with no network and a private `/tmp`. They see the interpreter and system libraries read-only, **their own report's** `uploads/` read-only, and its `filtered/`, `scripts/` and `output/` writable. Other reports, session files, the source tree, `.env`, and the server's environment variables are not visible. (Sessions created before the per-report layout still use the shared `workspace/uploads`, `filtered`, `scripts` and `reports` folders until they are moved.) Scripts are also limited to 20 s of CPU time, 2 GB of memory, 30 s of wall time, and `PDF_REPORT_SANDBOX_MAX_FILE_BYTES` per file.
- A Python-level guard runs inside the sandbox as a second layer and records file access in a manifest. On its own it can be bypassed, so the app refuses to run generated code without bubblewrap unless `PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1` is set.
- Login is a single shared secret. Failed attempts are rate-limited per client address (behind a reverse proxy that address is the proxy's), and logout revokes the token server-side. Tokens are HMAC-signed with `APP_ACCESS_SECRET`; changing the secret invalidates all sessions.
- Admin endpoints need a separate `APP_ADMIN_SECRET`.
- Data metadata samples redact values from columns with sensitive names such as email, token, password, document, phone, and address before prompt context is sent to an LLM provider. Sample rows are omitted from prompt payloads by default; enable "Incluir amostras de dados nos prompts" in report settings only when extra context is necessary.
- Do not commit provider keys or access secrets. Use `.env`.

## Important Maintenance Notes

- Session persistence is file-based with per-session file locking and atomic writes. Pipeline execution is serialized per session inside a single app process; multi-process deployments should use an external queue or distributed lock. The login rate limit is also per process.
- `execute_script` has a 30-second default timeout.
- `run_execution` retries failed report scripts up to five total attempts. A missing sandbox fails immediately instead of retrying.
- `calculate_metric_value` retries each metric up to three times and returns `"N/A"` if no valid value is produced.
- The default provider/model in backend and frontend is `deepseek` / `deepseek-flash` (`deepseek-v4-flash` is migrated to it). The Gemini default is `gemini-3.8-flash`.
- Manual artifact edits invalidate generated component code only when the artifact changed and no manual code override was supplied.
- Language changes call `translate_workspace`, which asks the LLM to preserve component IDs and Python logic while translating user-facing strings.

## Tests And Development Checks

Run the automated test suite:

```bash
APP_ENV=test venv/bin/python -m unittest discover -s tests
```

The sandbox isolation tests skip themselves when bubblewrap doesn't work. Set `PDF_REPORT_REQUIRE_BWRAP=1` to make that a failure instead (CI does).

Run the frontend specs (plain Node, no dependencies):

```bash
for spec in tests/*.spec.js; do node "$spec"; done
```

The `tests/*.browser.cjs` checks drive a real browser and need Playwright installed.

Run a quick backend syntax check:

```bash
venv/bin/python -m compileall app
```

Run canonical end-to-end sessions when provider keys are available:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py
```

Canonical PDFs are copied to `tests/canonical/generated_pdfs/` for manual review.

`list_models.py` lists Gemini and DeepSeek models when `GEMINI_API_KEY` and `DEEPSEEK_API_KEY` are configured:

```bash
venv/bin/python list_models.py
```

CI (`.github/workflows/ci.yml`) installs from the lock file, installs bubblewrap, and runs the compile check, the unit tests with `PDF_REPORT_REQUIRE_BWRAP=1`, and the Node specs.

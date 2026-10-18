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
- Database: PostgreSQL 17 via SQLAlchemy 2 (sync) and psycopg 3; schema migrations with Alembic
- Files: per-report folders under `workspace/tenants/`

## Repository Layout

```text
app/
  main.py                    App setup: auth middleware, startup checks, router wiring
  db.py                      Postgres engine, transactions, automatic migrations
  cli.py                     Operator commands (migrate, create-demo-user, import-legacy)
  security.py                Session cookie, CSRF, signed-in user and admin checks, client IP, headers
  routers/auth.py            Sign-up, sign-in and account routes, auth pages
  routers/api.py             Report, template and admin routes
  templates/email/           Email texts (Portuguese, with an English summary)
  models/
    session.py               Pydantic session schema and legacy migrations
    db.py                    Database tables (users, reports, layout_templates, revoked_tokens)
    refinement.py            Schema for chat-refinement routing decisions
  services/
    llm/                     LLM calls, one module per concern (see "LLM Package" below)
    auth_service.py          Accounts, sessions, email-link flows, password hashing
    email_service.py         SMTP sending (or the log / a test outbox)
    rate_limit.py            In-memory rate limits for the auth routes
    turnstile.py             Cloudflare Turnstile verification
    pipeline_service.py      Report-generation pipeline stages
    executor_service.py      Sandboxed generated-script execution
    exploration_service.py   Ad-hoc data explorer (questions and charts)
    job_runner.py            Limits how many report jobs run at once
    session_manager.py       Session storage in the `reports` table, and restart recovery
    legacy_import.py         Imports the old JSON sessions and template library
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
alembic/                     Database migrations (alembic.ini at the root)
scripts/backup.sh            Database and workspace backup
prompts/                     System prompts used for each LLM task
static/
  index.html                 Main browser workspace
  auth.html, account.html    Sign-in pages and account settings
  templates.html             Layout-template editor
  css/styles.css             Visual styling
  js/api.js                  HTTP client wrapper (adds the CSRF header, redirects to /login on 401)
  js/auth.js, js/account.js  Sign-in pages and account settings
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
  sessions/                  Old session JSON files (read only by `import-legacy`)
  sandbox_support/           Runtime guard and helpers loaded inside the sandbox
requirements.txt             Python dependencies
requirements-lock.txt        Frozen dependency set for reproducible installs
list_models.py               Helper script for listing Gemini models
```

The `workspace/` directories are created automatically when the app starts. Session state, layout templates and revoked logins live in Postgres.

## Setup

Create a Python 3.12 virtual environment and install the locked dependency set:

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -c requirements-lock.txt
```

This is the same install CI and Docker use. Plain `pip install -r requirements.txt` also works but may resolve newer versions than the tested set.

Start a local Postgres (Docker), used for development and by the tests:

```bash
docker compose -f compose.dev.yml up -d
```

It listens on `127.0.0.1:5433`. The app creates its tables on first start.

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
DATABASE_URL=postgresql+psycopg://repgen:repgen@127.0.0.1:5433/repgen
```

The default provider is `deepseek`, so `DEEPSEEK_API_KEY` is required for the default path. `GEMINI_API_KEY` is required when selecting Gemini.

Create your account (sign-up is closed by default) and note the printed password:

```bash
python -m app.cli create-admin --email you@example.com
```

Without `SMTP_HOST`, emails (verification and password-reset links) are written to the app log instead of being sent.

## Configuration

All settings are environment variables (`.env` is loaded automatically).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | none (required) | Postgres connection URL, `postgresql+psycopg://user:password@host:port/db`. Compose builds it from `POSTGRES_PASSWORD`. |
| `POSTGRES_PASSWORD` | none (required by compose) | Password of the compose `postgres` service. Use a URL-safe value such as `openssl rand -hex 24`. |
| `TEST_DATABASE_URL` | `postgresql+psycopg://repgen:repgen@127.0.0.1:5433/repgen_test` | Database used when `APP_ENV=test`. Its name must end in `_test`; it is created if missing and emptied at the start of each test run. |
| `PDF_REPORT_AUTO_MIGRATE` | `1` | Apply pending database migrations when the app (or CLI) first connects. |
| `PDF_REPORT_DB_POOL_SIZE` / `PDF_REPORT_DB_MAX_OVERFLOW` | `10` / `10` | Database connection pool size. |
| `APP_ENV` | `development` | `development`, `dev`, `local`, or `test` relax the checks below; anything else is production. |
| `PUBLIC_BASE_URL` | `http://localhost:8000` (compose: `https://repgen.gengardev.com.br`) | Base of the links in emails. |
| `PDF_REPORT_SIGNUP_MODE` | `closed` | `open` lets anyone create an account; `closed` means accounts only come from `python -m app.cli create-user`. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM` | unset, `587` | Outgoing email. Port 465 uses implicit TLS; other ports use STARTTLS (`SMTP_STARTTLS=0` turns it off). Without `SMTP_HOST`, emails go to the log. |
| `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | unset | Cloudflare Turnstile on sign-up, forgot-password and login after a failure. Without both keys the check is skipped. |
| `PDF_REPORT_SESSION_IDLE_DAYS` / `PDF_REPORT_SESSION_MAX_DAYS` | `7` / `30` | A sign-in ends after this many days unused, or this many days after it started. |
| `GEMINI_API_KEY`, `DEEPSEEK_API_KEY` | unset | Provider keys. |
| `PDF_REPORT_COOKIE_SECURE` | `1` in production, `0` in development | Send the session cookie over HTTPS only. Set `0` only for plain HTTP on a non-localhost address. |
| `PDF_REPORT_LOGIN_MAX_FAILURES` / `PDF_REPORT_LOGIN_WINDOW_SECONDS` | `10` / `300` | Failed logins allowed per client address before login returns 429. Failures on one account also add a growing delay (2 s, 4 s, ... up to 15 min) after the fourth, instead of locking it. |
| `PDF_REPORT_ALLOW_UNSAFE_SANDBOX` | unset | Set `1` to run generated code under the weaker Python-level guard when bubblewrap is unavailable. Trusted environments only. |
| `PDF_REPORT_SANDBOX_MAX_FILE_BYTES` | `209715200` (200 MB) | Largest file a generated script may write. |
| `PDF_REPORT_MAX_CONCURRENT_JOBS` | `2` | Report jobs (pipeline, refinement, render, explorer) that run at once across all users; others wait. |
| `PDF_REPORT_MAX_JOBS_PER_USER` | `1` | Jobs one user can run at once. Their next job waits ("Aguardando seu relatório anterior terminar...") without holding a global slot. |
| `PDF_REPORT_QUOTA_STORAGE_BYTES` | `10737418240` (10 GB) | Storage per user (uploads and generated files). Uploads over it get `413 storage_quota`. |
| `PDF_REPORT_QUOTA_REPORTS_PER_DAY` | `50` | New reports per user per rolling 24 hours (`429 daily_report_limit`). |
| `PDF_REPORT_RECOVER_SESSIONS_ON_START` | `1` (`0` when `APP_ENV=test`) | Mark sessions interrupted by a restart as failed on startup. |
| `PDF_REPORT_MAX_UPLOAD_BYTES` | `52428800` (50 MB) | Per-file upload limit. |
| `PDF_REPORT_MAX_REQUEST_UPLOAD_BYTES` | `94371840` (90 MB) | Total size of all files in one upload request. Cloudflare's free plan rejects bodies over 100 MB. |
| `PDF_REPORT_MAX_EXCEL_SHEETS` | `20` | Sheets allowed per Excel workbook. |
| `PDF_REPORT_CLIENT_IP_HEADER` | unset (compose: `CF-Connecting-IP`) | Header holding the real client address behind a proxy. Used only for requests from a trusted proxy. |
| `PDF_REPORT_TRUSTED_PROXIES` | unset (compose: Docker's gateway networks) | Comma-separated hostnames, IPs or CIDR ranges whose `PDF_REPORT_CLIENT_IP_HEADER` is believed. |
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

Create `.env` as shown above, without `DATABASE_URL` and with a database password instead:

```bash
POSTGRES_PASSWORD=$(openssl rand -hex 24)   # put the value in .env
```

Then build and run:

```bash
docker compose up -d --build
```

Compose starts two services: `postgres` (data in the `postgres-data` named volume, no published port) and the app, which waits for Postgres to be healthy and applies migrations on startup.

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

### Moving existing reports into the database

Reports created before Postgres are JSON files in `workspace/sessions/`. Import them into a demo account:

```bash
docker compose exec report-generator python -m app.cli create-demo-user --email demo@gengardev.com.br
docker compose exec report-generator python -m app.cli import-legacy --owner demo@gengardev.com.br --dry-run
docker compose exec report-generator python -m app.cli import-legacy --owner demo@gengardev.com.br
```

Each report keeps its ID. Its files are copied to `workspace/tenants/{account id}/reports/{id}/`, and paths inside the session (including generated scripts) are rewritten. User layout templates from `workspace/layout_templates.json` are imported too. Running it again skips what was already imported. The old files are left in place; delete `workspace/sessions`, `uploads`, `filtered`, `scripts`, `reports` and `layout_templates.json` by hand once you've checked the imported reports.

### Accounts

Sign-up is closed until you open it (`PDF_REPORT_SIGNUP_MODE=open`). Keep it closed until users bring their own provider keys (plan Phase 4); until then every account's reports use the server's keys. Reports and templates are already private to their owner.

```bash
docker compose exec report-generator python -m app.cli create-admin --email you@example.com
docker compose exec report-generator python -m app.cli create-user --email friend@example.com
docker compose exec report-generator python -m app.cli set-password --email demo@gengardev.com.br
```

Passwords are generated and printed once.

**Email.** Pick a transactional provider with SMTP (Resend, Postmark, Amazon SES, ...), add its SPF, DKIM and DMARC records to the `gengardev.com.br` zone in Cloudflare DNS, and set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` (for example `no-reply@gengardev.com.br`) in `.env`.

**Turnstile.** In the Cloudflare dashboard, add a Turnstile widget for `repgen.gengardev.com.br` and put its keys in `.env` as `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET_KEY`.

### Backups

`scripts/backup.sh` writes a `pg_dump` of the database and a tarball of `workspace/` to `./backups` (or `BACKUP_DIR`), and deletes copies older than `BACKUP_KEEP_DAYS` (14). Set `BACKUP_SYNC_COMMAND` to copy them off the machine, for example `rclone sync ./backups r2:repgen-backups`. Run it nightly from the host, for example with cron:

```text
0 3 * * * cd /path/to/pdf-report-generator && BACKUP_SYNC_COMMAND='rclone sync ./backups r2:repgen-backups' scripts/backup.sh
```

To restore:

```bash
docker compose down
docker volume ls | grep postgres-data                   # <folder name>_postgres-data
docker volume rm <folder name>_postgres-data             # only if replacing the database
docker compose up -d --wait postgres
docker compose exec -T postgres pg_restore -U repgen -d repgen --clean --if-exists --no-owner < backups/db-YYYYMMDD-HHMMSS.dump
tar -xzf backups/workspace-YYYYMMDD-HHMMSS.tar.gz         # from the project folder, replaces workspace/
docker compose up -d
```

### Cloudflare Tunnel

Run `cloudflared` on the host as usual, pointing at `http://localhost:8000` (or your `HOST_PORT`). Compose doesn't start a tunnel.

Cloudflare's real client address arrives in `CF-Connecting-IP`, and compose tells the app to trust it from Docker's gateway networks, which is where a host `cloudflared` connects from (`PDF_REPORT_TRUSTED_PROXIES`). Login rate limits are then per visitor, not per tunnel. Authenticated responses send `Cache-Control: private, no-store`; also add a Cloudflare cache rule that bypasses the cache for `/api/*` and `/session/*`.

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

Accounts (`app/routers/auth.py`). Errors return `{"detail": {"code": ..., "message": ...}}`.

- `GET /api/auth/config`: whether sign-up is open, the Turnstile site key, the minimum password length. Public.
- `POST /api/auth/signup`: `{email, password, display_name?, turnstile_token?}`. Creates an unverified account and emails a link. Answers the same way when the email is taken. Public, `403 signup_closed` unless sign-up is open.
- `POST /api/auth/login`: `{email, password, turnstile_token?}`. Sets the `pdf_report_session` cookie (HttpOnly, SameSite=Lax). Public. 429 with `Retry-After` after too many failures.
- `POST /api/auth/verify-email`, `POST /api/auth/reset-password`, `POST /api/auth/confirm-email`: consume a link token (`{token}`, plus `password` for reset). Public.
- `POST /api/auth/forgot-password`: `{email, turnstile_token?}`. Always answers the same. Public.
- `GET /api/auth/me`, `PATCH /api/auth/me` (`{display_name}`): the signed-in account.
- `POST /api/auth/logout`, `POST /api/auth/logout-all`: end this session, or every session of the account.
- `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}`: signed-in devices.
- `POST /api/auth/resend-verification`, `POST /api/auth/change-password`, `POST /api/auth/change-email`.
- `DELETE /api/auth/account` (`{password}`): deletes the account with all its reports, files and templates. The only admin can't delete their account (`409 last_admin`).
- Pages: `/login`, `/signup`, `/forgot-password`, `/reset-password`, `/verify-email`, `/confirm-email` (`static/auth.html`) and `/account` (`static/account.html`). Email links put the token in the URL fragment (`#token=...`), so it never reaches server logs.

Reports:

- `GET /`: serves the static workspace UI.
- `GET /session/{job_id}`: serves the workspace already pointed at a session.
- `GET /templates`: serves the layout-template editor.
- `GET /api/models`: lists available providers and models.
- `GET /api/usage`: your storage use, reports in the last 24 hours, and the limits.
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
- `DELETE /api/sessions/{job_id}`: deletes the session's database row and all its files.
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
- `GET /api/layout-templates`: the built-in templates (`"builtin": true`) followed by your own.
- `POST /api/layout-templates`: creates one of your templates (the server assigns the id).
- `PUT /api/layout-templates/{template_id}`: updates one of your templates. Built-ins answer `403 builtin_template`; the editor saves a copy instead.
- `DELETE /api/layout-templates/{template_id}`: deletes one of your templates.
- `GET /api/admin/workspace-usage`: returns disk usage statistics for each workspace directory. Requires the admin role.
- `POST /api/admin/cleanup`: manually triggers the cleanup of expired artifacts based on the retention policy, plus ended sessions, used email links and week-old unverified accounts. Requires the admin role.

Every `/api/*` endpoint except the public auth ones above needs a signed-in session cookie. Every request that changes state (anything but GET, HEAD, OPTIONS) also needs an `X-CSRF-Token` header equal to the `csrf_token` cookie; `static/js/api.js` adds it. Creating a report (`POST /api/sessions`) needs a verified email (`403 email_not_verified`). Reports belong to the account that created them: any other account gets `404` for every report route (never `403`, so report ids can't be probed), and `GET /api/sessions` lists only your own. Session IDs must match `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`; anything else returns 404.

## Workspace Maintenance

The application includes a data lifecycle management system to prevent the indefinite accumulation of files.

### Retention Policy

The default retention policy is:
- **Sessions:** 30 days.
- **Reports (PDFs):** 30 days.
- **Uploads:** 30 days.
- **Scripts:** 7 days.
- **Filtered/Preprocessed data:** 7 days.

Sessions expire by the time of their last update in the database. Files older than these limits are subject to automatic removal during cleanup tasks.

### Cleanup Tasks

- **Session Deletion:** Using `DELETE /api/sessions/{job_id}` removes the session's database row and all its files.
- **Automated Cleanup:** The `POST /api/admin/cleanup` endpoint triggers a scan of the workspace to remove orphaned files or expired sessions based on the configured retention policy.

### Disk Usage

The `GET /api/admin/workspace-usage` endpoint provides a breakdown of storage consumption by category (uploads, scripts, reports, tenants, and `sessions`, which is the size of the `reports` table), helping administrators monitor workspace growth.

## Session Shape

Sessions are normalized by `app/models/session.py` and stored as JSONB in `reports.state` (one row per report, keyed by `job_id`). The row also copies a few fields into columns for listing (`title`, `status`, `current_step`, `pdf_version`, `updated_at`). Important fields:

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
- Some Docker hosts (Docker Desktop on macOS, for example) refuse to mount a fresh `/proc` inside the container (`bwrap: Can't mount proc on /proc`). The app detects this at startup and runs the sandbox without `/proc`; the rest of the isolation is unchanged. The startup log says when this happens.
- A Python-level guard runs inside the sandbox as a second layer and records file access in a manifest. On its own it can be bypassed, so the app refuses to run generated code without bubblewrap unless `PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1` is set.
- Accounts sign in with email and password. Passwords are hashed with argon2id (12 to 256 characters). The session cookie holds a random token; the database stores only its SHA-256, so a database leak doesn't hand out sessions. Changing the password ends every other session, and a reset ends all of them.
- Login, sign-up, forgot-password and email links are rate-limited per client address (the real one from `CF-Connecting-IP` behind the tunnel), and per account or email where it matters. Repeated failures on one account slow it down rather than lock it, so nobody can lock out a known user.
- Sign-up, forgot-password, and logins after a failure require Cloudflare Turnstile when its keys are set.
- Sign-up and forgot-password never reveal whether an email has an account.
- Admin endpoints need `users.role = 'admin'` (`python -m app.cli create-admin`).
- Data metadata samples redact values from columns with sensitive names such as email, token, password, document, phone, and address before prompt context is sent to an LLM provider. Sample rows are omitted from prompt payloads by default; enable "Incluir amostras de dados nos prompts" in report settings only when extra context is necessary.
- Do not commit provider keys, SMTP credentials or Turnstile secrets. Use `.env`.

## Important Maintenance Notes

- Every session change locks the report's row (`SELECT ... FOR UPDATE`), applies the change and writes it back in one transaction, so concurrent updates are never lost. Postgres can't store NUL characters or NaN/Infinity in JSONB, so `app/db.py` drops NULs and stores NaN/Infinity as `null`. Pipeline execution is serialized per session inside a single app process; multi-process deployments should use an external queue or distributed lock. The login rate limit is also per process.
- Changing a table in `app/models/db.py` needs a migration: `DATABASE_URL=... alembic revision --autogenerate -m "..."`, then review the file in `alembic/versions/`. A test fails if the models and migrations disagree.
- `execute_script` has a 30-second default timeout.
- `run_execution` retries failed report scripts up to five total attempts. A missing sandbox fails immediately instead of retrying.
- `calculate_metric_value` retries each metric up to three times and returns `"N/A"` if no valid value is produced.
- The default provider/model in backend and frontend is `deepseek` / `deepseek-flash` (`deepseek-v4-flash` is migrated to it). The Gemini default is `gemini-3.8-flash`.
- Manual artifact edits invalidate generated component code only when the artifact changed and no manual code override was supplied.
- Language changes call `translate_workspace`, which asks the LLM to preserve component IDs and Python logic while translating user-facing strings.

## Tests And Development Checks

Run the automated test suite (needs the local Postgres from `compose.dev.yml`):

```bash
docker compose -f compose.dev.yml up -d
APP_ENV=test venv/bin/python -m unittest discover -s tests
```

Tests use the `repgen_test` database (`TEST_DATABASE_URL`), which is emptied at the start of every run, so they never touch your development data. They still share `workspace/` with a running dev server.

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

# Repository Guidelines

## Project Structure & Module Organization

This repository is a FastAPI app with a static browser workspace. See `README.md` for the full layout, configuration, and endpoint list.

- `app/main.py`: app setup only (auth middleware, startup checks, router wiring). Don't add routes here.
- `app/db.py`: Postgres engine, `transaction()`, automatic Alembic migrations. `app/models/db.py`: the tables. `alembic/`: migrations.
- `app/cli.py`: operator commands (`migrate`, `create-demo-user`, `import-legacy`).
- `app/routers/api.py`: report, template and admin routes.
- `app/routers/credentials.py`: the caller's encrypted provider keys and model preferences. `app/routers/operations.py`: usage and admin pages/APIs.
- `app/services/usage_service.py`: durable provider attempts and monthly/report limits. `admin_service.py`: account administration and audit listings. `cloudflare_access.py`: signed admin Access assertions. `deployment_service.py`: local launch checks.
- `app/security.py`: session cookie, CSRF, `current_user` / `require_verified_user` / `require_admin`, client IP, security headers.
- `app/routers/auth.py` and `app/services/auth_service.py`: sign-up, sign-in, sessions, email-link flows. `email_service.py` sends mail (memory outbox under tests), `rate_limit.py` and `turnstile.py` guard the public routes.
- `app/services/llm/`: LLM calls, one module per concern (`client`, `planning`, `metrics`, `components`, `texts`, `script`, `refinement`, `parsing`, `errors`, `prompts`).
- `app/services/`: pipeline stages, sandboxed execution, explorer, job runner, session persistence, cleanup, and web helpers.
- `app/utils/`: metadata extraction, artifact validation, component library and renderers, layout and template helpers, model catalog.
- `app/models/`: Pydantic schemas for sessions and refinement decisions.
- `prompts/`: Markdown prompt contracts used by LLM generation steps.
- `static/`: frontend assets. JavaScript is split into `api.js`, `app.js`, `ui.js`, `layout_editor.js`, `templates.js`, and `utils.js`; CSS lives in `static/css/styles.css`.
- `tests/`: `unittest` suite (`test_*.py`), Node specs (`*.spec.js`), Playwright browser checks (`*.browser.cjs`), and canonical end-to-end sessions.
- `workspace/`: runtime files (uploads, generated scripts, PDFs) under `workspace/tenants/`. Session state lives in Postgres (`reports.state`). Treat this as generated state, not source.
- `requirements.txt` / `requirements-lock.txt`: dependencies and the tested version set.

## Build, Test, and Development Commands

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt -c requirements-lock.txt
```

```bash
docker compose -f compose.dev.yml up -d
```

Starts the local Postgres (`127.0.0.1:5433`) used for development (`DATABASE_URL=postgresql+psycopg://repgen:repgen@127.0.0.1:5433/repgen`) and by the tests (`repgen_test`, created and emptied automatically).

Creates a local environment with the same versions CI and Docker use. Python 3.12 is pinned in `.python-version`. bubblewrap (`bwrap`) must also be installed; see `README.md`.

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Runs the development server at `http://localhost:8000`.

```bash
APP_ENV=test python -m unittest discover -s tests
for spec in tests/*.spec.js; do node "$spec"; done
```

Runs the Python test suite and the frontend specs. Add `PDF_REPORT_REQUIRE_BWRAP=1` to fail instead of skipping when bubblewrap doesn't work, as CI does.

```bash
python -m compileall app
```

Performs a quick syntax/import sanity check for backend modules.

```bash
python list_models.py
```

Lists Gemini and DeepSeek models available to the configured keys.

```bash
sh scripts/load-check.sh 10
```

Runs the automated Docker capacity check with 5–10 test users. The runner owns a unique Compose project with its own Postgres and workspace, requires bubblewrap and cleans up on exit. It uses no live provider keys. Do not replace its isolated volumes/database with production resources.

## Coding Style & Naming Conventions

Use 4-space indentation for Python and keep functions small enough to match the existing service-oriented layout. Prefer `snake_case` for Python functions, variables, and JSON session keys. Frontend code uses plain JavaScript objects such as `App`, `API`, `UI`, and `Utils`; keep new browser behavior in the matching file.

Prompt files should keep strict output contracts because parsing is intentionally simple.

Rules that tests enforce or that are easy to break:

- **LLM calls:** outside `client.py`, call the model as `client.call_llm(...)` after `from app.services.llm import client`. Never `from app.services.llm.client import call_llm`, and don't add re-exports to `app/services/llm/__init__.py`. Tests mock every provider with a single patch on `app.services.llm.client.call_llm`; importing the name directly silently escapes that mock. `tests/test_llm_package_layout.py` checks this.
- **Generated code:** run it only through `execute_script` in `executor_service.py`, and always pass the report's `paths=ReportPaths.from_session(session)`. The sandbox then sees only that report's folders. Build every report file path from `ReportPaths` (`uploads`, `filtered`, `scripts`, `output`), never from the shared `workspace/*` constants, which exist only for legacy sessions. Don't widen the sandbox mounts or make the unsafe fallback the default.
- **Providers:** provider-specific request code lives in an adapter under `app/services/llm/providers/`, registered in `registry.py`. `call_llm` owns retries, timeouts and budgets, so adapters build SDK clients with retries disabled.
- **Credentials and usage:** resolve only the authoritative report owner's key through `llm/credentials.py`. Never put plaintext keys, prompts, provider error bodies or responses in usage/audit rows. Reserve each actual attempt before dispatch so concurrent calls cannot bypass report caps; finish it with provider token counts. Monthly caps stop new calls using known tokens and may be exceeded by in-flight calls. Test with mocked SDKs, never live APIs.
- **Admin:** every admin endpoint and page must call `require_admin`, which verifies Cloudflare Access as well as the role in production. Never trust assertion-header presence or accept a request-selected issuer/JWKS URL. Admin listings expose account metadata and totals, never credentials or report contents. Audit administrative mutations in the same transaction.
- **Launch:** production must never mount shared flat workspace paths. Preserve development migration fixtures until deployment imports are confirmed. Run `migrate-storage` with workers stopped; never delete original legacy files automatically. See `docs/launch-checklist.md`, `tests/load_check.py` and `scripts/restore-drill.py` for deployment drills; use disposable test databases and do not run DB test processes concurrently.
- **Report files:** serve report artifacts through `report_files.report_file_response`, which walks directory descriptors without following symlinks and streams an already opened file. Never return a report-controlled path through a plain `FileResponse` or delete through linked parents. The executor rejects generated symlinks and special files before host pipeline stages read outputs.
- **No long requests:** Cloudflare drops requests after about 100 seconds. Anything that waits on an LLM or the sandbox for longer must run as a background job (`run_job`) that the UI polls.
- **Background jobs:** schedule pipeline, refinement, and render work as `background_tasks.add_task(run_job, func, job_id, ...)`. Wrap only the entry point: code already inside a job must call other job functions directly, or it will wait on itself for a free slot.
- **Ownership:** every report belongs to one account (`reports.owner_id`). In route handlers get a report with `require_session(job_id, request)` (404 for anyone but the owner) and never with `get_session`, which ignores the owner and is for background jobs and maintenance. Listings and templates take the caller's `owner_id`. A new route must be added to `SAMPLE_REQUESTS` (or `NOT_REPORT_SCOPED`, with a reason) in `tests/test_isolation.py`, which checks every route.
- **Sessions:** read and write them only through `session_manager` (`get_session`, `update_session`, `add_log`, ...). Each change runs in one transaction on the locked row; for a read-modify-write add a function there built on `_mutate`, never `get_session` followed by `update_session` with a whole field you computed (another thread may have changed it in between). Validate IDs with `validate_job_id`.
- **Schema changes:** edit `app/models/db.py`, then `DATABASE_URL=... alembic revision --autogenerate -m "..."` and review the generated file. `tests/test_database.py` fails when models and migrations disagree. Never edit a migration that has shipped. File matching in cleanup must use separators (`{job_id}.`, `{job_id}_`), never a bare prefix.
- **Auth:** the middleware in `app/main.py` puts the signed-in user in `request.state.user`; get it with `current_user(request)` (or `require_verified_user` / `require_admin`). Public API paths must be listed in `PUBLIC_API_PATHS`. Never return whether an email has an account (sign-up, forgot-password, change-email answer the same either way). Never store or log raw tokens or passwords; store `sha256` of tokens.
- **CSRF:** every non-GET request needs `X-CSRF-Token` equal to the `csrf_token` cookie. Frontend fetches must pass `API.authHeaders(...)`.
- **Frontend:** the Content-Security-Policy blocks inline scripts. No `<script>` blocks or `on*=` attributes in HTML; put code in `static/js/`.
- **Pipeline errors:** exceptions that should reach the UI with a specific code implement `as_error_dict()` (see `LLMContractError` and `SandboxUnavailableError`).

## Testing Guidelines

Tests use the standard library `unittest`, live under `tests/`, and are named `test_*.py`. Run them with `APP_ENV=test` and the `compose.dev.yml` Postgres running; they use a real Postgres, never SQLite (JSONB and row locks behave differently). At minimum, run `python -m compileall app` and the unit tests before finishing a change.

- Mock LLM calls with `patch("app.services.llm.client.call_llm", ...)`; patch provider clients and `record_llm_usage` on `app.services.llm.client` too. Never depend on live provider APIs in automated tests.
- To exercise a route handler directly, import it from `app.routers.api`. For full HTTP tests, use `TestClient(app.main.app)` with `headers=signed_in_headers()` from `tests/auth_helpers.py` (a real user, session cookie and CSRF token; `signed_in_headers(role="admin")` for admin routes). Read sent emails from `app.services.email_service.outbox`. Create reports in tests with `create_session` from `tests.auth_helpers` (owned by the same default user that `signed_in_headers()` signs in as); to call a handler directly, pass `request_as()` as its `request`.
- Patch a name where it is looked up: for example, `patch.object(app.routers.api, "run_pipeline")`, not on the module that defines it.
- Sandbox tests must pass under bubblewrap. Tests that need it can skip with `probe_bubblewrap()` but must not assume the unsafe fallback.
- Tests share `workspace/` with any running dev server: use unique `test-…` job IDs and clean up what you create (`delete_entire_session` removes the row and files; `delete_session` only the row). The test database is emptied once per run, not per test.

## Commit & Pull Request Guidelines

Use short, imperative commit messages, for example `Add artifact validation` or `Fix PDF retry logging`.

Pull requests should include a concise description, affected endpoints or UI areas, verification commands run, and screenshots when changing the frontend or generated PDF appearance.

## Security & Configuration Tips

Use `.env` for `GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `DATABASE_URL`, `POSTGRES_PASSWORD`, `SMTP_*` and `TURNSTILE_*`; do not commit secrets. All settings are listed in the README's "Configuration" table.

This app executes LLM-generated Python built from user-uploaded data. bubblewrap is the security boundary: the app refuses to run generated code without it unless `PDF_REPORT_ALLOW_UNSAFE_SANDBOX=1` is set, which is only acceptable in trusted environments. Changes to `executor_service.py`, `security.py`, `auth_service.py`, or upload validation should come with tests in `tests/test_sandbox_executor.py`, `tests/test_security_hardening.py`, `tests/test_auth.py`, or `tests/test_upload_security.py`.

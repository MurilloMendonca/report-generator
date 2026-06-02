# Report Generator

FastAPI application that uses LLMs to turn uploaded CSV, Excel, and PDF context files into analytical PDF reports. The app provides a browser workspace where users can upload and inspect files, optionally preprocess data, generate report components, edit indicators and layout, refine the report through chat, inspect generated Python, and download the final PDF.

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
8. Assembles a standalone Python report script, executes it in a local sandbox, and writes the PDF.
9. If execution fails, asks the LLM to repair the whole script and retries up to five times.

## Tech Stack

- Backend: FastAPI, Uvicorn
- Data: pandas, openpyxl
- Charts: matplotlib, seaborn
- PDF rendering: fpdf2
- LLM providers:
  - Google Gemini via `google-genai`
  - DeepSeek via OpenAI-compatible client
- Frontend: static HTML/CSS/JavaScript
- Runtime persistence: JSON files under `workspace/sessions`

## Repository Layout

```text
app/
  main.py                    FastAPI app, endpoints, pipeline orchestration
  models/session.py          Pydantic session schema and legacy migrations
  services/
    executor_service.py      Sandboxed generated-script execution
    llm_service.py           LLM calls, prompt loading, artifact/code/layout generation
    pipeline_service.py      Report-generation pipeline stages
    session_manager.py       JSON-backed session persistence
  utils/
    data_analyzer.py         CSV/Excel metadata extraction
    template_library.py      Built-in A4 layout blueprints
prompts/                     System prompts used for each LLM task
static/
  index.html                 Main browser workspace
  css/styles.css             Visual styling
  js/api.js                  HTTP client wrapper
  js/app.js                  Frontend state and event handling
  js/ui.js                   UI render helpers
  js/utils.js                Drag/drop, tab, JSON helpers
workspace/
  uploads/                   Uploaded data files
  filtered/                  Preprocessed tabular outputs
  scripts/                   Generated report/exploration scripts
  reports/                   Generated PDFs
  sessions/                  Session JSON files
  sandbox_support/           Runtime guard used by sandboxed script execution
requirements.txt             Python dependencies
list_models.py               Helper script for listing Gemini models
```

The `workspace/` directories are created automatically by `app/main.py` when the app starts.

## Setup

Create and activate a virtual environment, then install dependencies:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file with at least one provider key:

```bash
GEMINI_API_KEY=your_google_gemini_key
DEEPSEEK_API_KEY=your_deepseek_key
APP_ACCESS_SECRET=your_long_random_login_secret
```

The default provider is `deepseek`, so `DEEPSEEK_API_KEY` is required for the default path. `GEMINI_API_KEY` is required when selecting Gemini. `APP_ACCESS_SECRET` is the login secret used by the browser UI. After login, the app issues a signed HttpOnly session cookie; the browser does not store the raw secret.

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

To change the host port:

```bash
HOST_PORT=5555 docker compose up -d --build
```

## Main Runtime Flow

The pipeline starts from `POST /api/sessions` and runs in a FastAPI background task.

1. `start_session` saves uploads into `workspace/uploads` and creates a session JSON file.
2. `run_pipeline` enriches missing file artifacts through `extract_metadata`.
3. If the goal asks to transform data first, `generate_data_filter` asks the LLM for a `preprocess_dataframes(dataframes)` function. The app executes it once and writes CSV outputs under `workspace/filtered`.
4. `generate_artifacts` asks the LLM for report component definitions using the raw or preprocessed metadata.
5. `calculate_metric_value` executes generated metric scripts and stores `calculated_metrics`.
6. The layout stage selects a saved template or normalizes the generated layout into A4 coordinates.
7. `generate_component_code` creates Python render functions for charts/tables.
8. `generate_all_texts` creates all narrative text in one LLM response for consistency.
9. `assemble_script` combines imports, helper functions, component functions, data loading, layout calls, and PDF output.
10. `execute_script` writes the generated script under `workspace/scripts` and runs it through the local sandbox.
11. `run_execution` stores the PDF version on success or calls `fix_script` and retries on failure.

Preprocessed files are treated as the analytical input for all metrics and components, while the original upload remains available in the file workspace.

## API Endpoints

- `POST /api/auth/login`: validates the UI/API access secret.
- `GET /api/auth/check`: validates the current API token.
- `GET /`: serves the static workspace UI.
- `GET /session/{job_id}`: serves the workspace already pointed at a session.
- `GET /templates`: serves the layout-template editor.
- `POST /api/sessions`: starts a report job. Form fields:
  - `files`: one or more CSV/Excel/PDF files.
  - `prompt`: report objective. Blank means exploratory report.
  - `provider`: `gemini` or `deepseek`; default `deepseek`.
  - `model_name`: provider model name.
- `GET /api/sessions`: returns recent sessions.
- `GET /api/sessions/{job_id}`: returns the full session state.
- `DELETE /api/sessions/{job_id}`: deletes the session record and associated runtime files.
- `POST /api/sessions/{job_id}/files`: adds more files and reruns the pipeline.
- `GET /api/sessions/{job_id}/files/{file_index}/preview`: previews an uploaded file. Use `?variant=preprocessed` when available.
- `GET /api/sessions/{job_id}/files/{file_index}/download`: downloads an uploaded file. Use `?variant=preprocessed` when available.
- `DELETE /api/sessions/{job_id}/files/{file_index}`: removes a file from the session and reruns when files remain.
- `POST /api/sessions/{job_id}/chat`: routes refinement feedback to a specific component, layout, settings, or full regeneration.
- `PUT /api/sessions/{job_id}/artifacts`: manually updates artifact definitions and optional component code overrides.
- `PUT /api/sessions/{job_id}/template`: manually updates layout coordinates and rerenders.
- `PUT /api/sessions/{job_id}/settings`: updates report settings plus provider/model; language changes trigger workspace translation.
- `POST /api/sessions/{job_id}/regenerate`: regenerates `texts`, `graphics`, or `all` using the current provider/model/settings.
- `GET /api/sessions/{job_id}/pdf`: downloads or previews the generated PDF.
- `POST /api/sessions/{job_id}/explore`: runs an ad-hoc LLM-generated analysis script against the session data.
- `GET /api/layout-templates`: lists available layout templates.
- `POST /api/layout-templates`: creates a layout template.
- `PUT /api/layout-templates/{template_id}`: updates a layout template.
- `DELETE /api/layout-templates/{template_id}`: deletes a layout template.

All `/api/*` endpoints except `/api/auth/login` require a valid signed session cookie. `X-Access-Token` is still accepted for non-browser clients, but it must contain a signed session token returned by the app, not the raw access secret.

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
- `explore_history`: ad-hoc explorer conversation history.

## Prompt Contracts

The LLM behavior is controlled by Markdown prompt files in `prompts/`:

- `GENERATE_ARTIFACTS.md`: returns JSON list of metric, visual, and text artifact definitions.
- `GENERATE_METRIC_VALUE.md`: returns Python code that prints `{"value": ...}`.
- `GENERATE_TEMPLATE.md`: legacy layout prompt kept for reference. The current pipeline builds layout coordinates from the local template library in `app/utils/template_library.py`.
- `GENERATE_COMPONENT_CODE.md`: returns one Python render function for a visual component.
- `GENERATE_ALL_TEXTS.md`: returns JSON object mapping text artifact IDs to text content.
- `GENERATE_DATA_FILTER.md`: returns optional `preprocess_dataframes(dataframes)` code for data preprocessing. The filename is legacy; the current contract supports filtering, calculated columns, aggregation, normalization, and related preparation.
- `REFINE_MODULAR.md`: returns a routing target: component ID, `LAYOUT`, `SETTINGS`, or `ALL`.
- `FIX_SCRIPT.md`: returns a full corrected Python script after execution failure.
- `EXPLORE_DATA.md`: returns Python code that prints an ad-hoc answer.
- `TRANSLATE_WORKSPACE.md`: returns translated artifacts and component code.

Most LLM outputs are parsed from raw text with simple code-fence stripping and JSON slicing. Keep prompt output formats strict when changing these files.

## Frontend Behavior

The frontend is a static single-page workspace:

- `static/js/app.js` owns session state, polling, form handlers, and event flow.
- `static/js/api.js` wraps calls to the FastAPI endpoints.
- `static/js/ui.js` renders logs, artifacts, files, layout canvas, metadata, settings, and full generated script.
- `static/js/utils.js` provides tab switching, JSON formatting, and drag/resize behavior.

After starting a session, the UI polls every two seconds until the status indicates completion or failure. PDF refresh is keyed by `pdf_version`.

File cards support drag-and-drop uploads, preview, download, and removal. When preprocessing produced a derived CSV, the file modal exposes tabs for `Original` and `Pré-processado`, and download uses the selected variant.

The settings panel can switch provider/model for future generation. The regeneration actions update only texts, only graphics, or the full report.

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

- The app executes LLM-generated Python for preprocessing, metrics, exploration, and PDF rendering.
- Generated scripts run through a local sandbox that restricts environment variables, filesystem access, network connections, temporary directories, process spawning, and execution time.
- The local sandbox reduces risk for controlled development and internal use, but production multi-user deployments should still prefer stronger isolation such as containers/seccomp and per-job credentials.
- Data metadata samples redact values from columns with sensitive names such as email, token, password, document, phone, and address before prompt context is sent to an LLM provider.
- Do not commit provider keys or access secrets. Use `.env`.

## Important Maintenance Notes

- Session persistence is file-based with per-session file locking and atomic writes. Pipeline execution is serialized per session inside a single app process; multi-process deployments should use an external queue or distributed lock.
- `execute_script` has a 30-second default timeout.
- `run_execution` retries failed report scripts up to five total attempts.
- `calculate_metric_value` retries each metric up to three times and returns `"N/A"` if no valid value is produced.
- The default provider/model in backend and frontend is `deepseek` / `deepseek-v4-flash`.
- Manual artifact edits invalidate generated component code only when the artifact changed and no manual code override was supplied.
- Language changes call `translate_workspace`, which asks the LLM to preserve component IDs and Python logic while translating user-facing strings.

## Tests And Development Checks

Run the automated test suite:

```bash
venv/bin/python -m unittest discover -s tests
```

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

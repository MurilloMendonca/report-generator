# AI PDF Report Generator

FastAPI application that uses LLMs to turn uploaded CSV or Excel files into a one-page analytical PDF report. The app provides a browser workspace where users can upload data, generate report components, edit indicators and layout, refine the report through chat, inspect generated Python, and download the final PDF.

The current UI text is mostly Portuguese, and the default report language is Portuguese.

## What It Does

1. Accepts one or more CSV/XLS/XLSX files and a report objective.
2. Extracts metadata from each dataset: shape, columns, null counts, unique counts, numeric stats, and sample rows.
3. Sends metadata and the user objective to an LLM to plan report artifacts:
   - `metric`: calculated values used by narrative text.
   - `graphic`, `bar`, `line`, `pie`, `scatter`, `table`, `chart`: visual PDF components.
   - `text`: narrative report sections.
4. Calculates metric artifacts by asking the LLM for short Python snippets, executing them, and storing the returned values.
5. Generates a layout template with A4 millimeter coordinates.
6. Generates Python render functions for visual components and a shared text-generation response for text components.
7. Assembles a standalone Python report script, executes it, and writes the PDF.
8. If execution fails, asks the LLM to repair the whole script and retries up to five times.

## Tech Stack

- Backend: FastAPI, Uvicorn
- Data: pandas, openpyxl
- Charts: matplotlib, seaborn
- PDF rendering: fpdf2
- LLM providers:
  - Google Gemini via `google-generativeai`
  - DeepSeek via OpenAI-compatible client
- Frontend: static HTML/CSS/JavaScript
- Runtime persistence: JSON files under `workspace/sessions`

## Repository Layout

```text
app/
  main.py                    FastAPI app, endpoints, pipeline orchestration
  services/
    executor_service.py      Writes and runs generated Python scripts
    llm_service.py           LLM calls, prompt loading, artifact/code/layout generation
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
  scripts/                   Generated report/exploration scripts
  reports/                   Generated PDFs
  sessions/                  Session JSON files
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
```

`GEMINI_API_KEY` is required when using the default `gemini` provider. `DEEPSEEK_API_KEY` is required only when selecting `deepseek`.

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

## Main Runtime Flow

The pipeline starts from `POST /api/sessions` and runs in a FastAPI background task.

1. `start_session` saves uploads into `workspace/uploads` and creates a session JSON file.
2. `run_pipeline` enriches missing file artifacts through `extract_metadata`.
3. `generate_artifacts` asks the LLM for report component definitions.
4. `calculate_metric_value` executes generated metric scripts and stores `calculated_metrics`.
5. `generate_template` maps visual/text artifacts into A4 coordinates.
6. `generate_component_code` creates Python render functions for charts/tables.
7. `generate_all_texts` creates all narrative text in one LLM response for consistency.
8. `assemble_script` combines imports, helper functions, component functions, data loading, layout calls, and PDF output.
9. `execute_script` writes the generated script to `workspace/scripts/{job_id}.py` and runs it with the current Python interpreter.
10. `run_execution` stores the PDF version on success or calls `fix_script` and retries on failure.

## API Endpoints

- `GET /`: serves the static workspace UI.
- `POST /api/sessions`: starts a report job. Form fields:
  - `files`: one or more CSV/Excel files.
  - `prompt`: report objective.
  - `provider`: `gemini` or `deepseek`; default `gemini`.
  - `model_name`: provider model name.
- `GET /api/sessions/{job_id}`: returns the full session state.
- `POST /api/sessions/{job_id}/files`: adds more files and reruns the pipeline.
- `POST /api/sessions/{job_id}/chat`: routes refinement feedback to a specific component, layout, settings, or full regeneration.
- `PUT /api/sessions/{job_id}/artifacts`: manually updates artifact definitions and optional component code overrides.
- `PUT /api/sessions/{job_id}/template`: manually updates layout coordinates and rerenders.
- `PUT /api/sessions/{job_id}/settings`: updates report title/font/text settings; language changes trigger workspace translation.
- `GET /api/sessions/{job_id}/pdf`: downloads or previews the generated PDF.
- `POST /api/sessions/{job_id}/explore`: runs an ad-hoc LLM-generated analysis script against the session data.

## Session Shape

Sessions are JSON documents stored as `workspace/sessions/{job_id}.json`. Important fields:

- `files`: uploaded file records with original name, saved path, and extracted metadata.
- `initial_prompt`: first user objective.
- `current_goal`: initial prompt plus refinement history.
- `refinement_history`: list of chat refinement prompts.
- `artifacts`: LLM-planned report components.
- `template`: positioned component blocks with `x`, `y`, `w`, and `h` in millimeters.
- `components`: generated Python render functions keyed by artifact ID.
- `calculated_metrics`: metric ID to calculated value.
- `settings`: report title, font, font size, alignment, line height, and language.
- `script`: assembled Python report script.
- `script_path`: generated script path.
- `pdf_path`: output PDF path.
- `pdf_version`: incremented when a new PDF is successfully rendered.
- `explore_history`: ad-hoc explorer conversation history.

## Prompt Contracts

The LLM behavior is controlled by Markdown prompt files in `prompts/`:

- `GENERATE_ARTIFACTS.md`: returns JSON list of metric, visual, and text artifact definitions.
- `GENERATE_METRIC_VALUE.md`: returns Python code that prints `{"value": ...}`.
- `GENERATE_TEMPLATE.md`: returns JSON list of A4 layout coordinates.
- `GENERATE_COMPONENT_CODE.md`: returns one Python render function for a visual component.
- `GENERATE_ALL_TEXTS.md`: returns JSON object mapping text artifact IDs to text content.
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

## Generated Report Script Contract

`assemble_script` builds a Python script that:

- Imports pandas, numpy, matplotlib, seaborn, fpdf2, tempfile, and os.
- Defines `render_text_block`.
- Includes every generated component function.
- Loads uploaded datasets into a `dataframes` dictionary keyed by original filename.
- Creates one A4-ish FPDF page.
- Draws the configured title.
- Calls each component function with `pdf`, `dataframes`, `x`, `y`, `w`, `h`, and a backward-compatible `df` keyword when possible.
- Writes the PDF to `workspace/reports/{job_id}.pdf`.

Visual components are expected to render themselves as temporary matplotlib images and insert them into the PDF.

## Important Maintenance Notes

- Generated code is executed locally with the same Python interpreter as the server. Treat uploaded files, prompts, and generated code as trusted only in a controlled environment.
- Session persistence is file-based and has no locking. Concurrent writes to the same session can race.
- `execute_script` has a 30-second timeout.
- `run_execution` retries failed report scripts up to five total attempts.
- `calculate_metric_value` retries each metric up to three times and returns `"N/A"` if no valid value is produced.
- The default provider/model in backend and frontend is `deepseek` / `deepseek-v4-flash`.
- Manual artifact edits invalidate generated component code only when the artifact changed and no manual code override was supplied.
- Language changes call `translate_workspace`, which asks the LLM to preserve component IDs and Python logic while translating user-facing strings.

## Development Checks

There is no test suite in the repository right now. Basic sanity checks:

```bash
python -m compileall app
python list_models.py
```

`list_models.py` lists Gemini and DeepSeek models when `GEMINI_API_KEY` and `DEEPSEEK_API_KEY` are configured.

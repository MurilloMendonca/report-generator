# Ad-hoc explorer chart responses

## Goal

Allow natural-language explorer questions to return readable charts directly in the explorer chat, alongside a short data-grounded answer. Text-only questions continue to work. This feature does not add charts to the report or change its PDF, layout, artifacts, metrics, or refinement goal.

## User experience

- The existing explorer input accepts questions and chart requests, including follow-up requests about prior results.
- A response can contain text and up to three chart cards. Each card has a title, an accessible PNG image, an optional caption, and a PNG download link.
- Requests show a pending response and prevent accidental double submission. Errors appear in the conversation and preserve the question for retry.
- Saved explorer messages and chart attachments reload with the session. Polling must not overwrite a pending local request, and responses from another session must not appear in the current session.
- Images fit desktop and narrow screens without overflowing. A missing image has a readable unavailable state.
- Charts can be opened at full resolution for closer inspection. On narrow screens, the explorer focuses the main workspace and keeps its question input visible.
- No browser-local model, external chart service, model-selected filesystem path, SVG, or generated HTML is introduced.

## Generation and execution contract

The existing LLM generates analysis Python using the full loaded dataframes and metadata. It decides from meaning whether a chart is appropriate; no keyword-based intent check is added. It may print an answer and call a provided `show_chart(fig, title, caption)` helper for figures. The helper closes/saves each figure to a server-assigned per-turn directory and records a manifest; the model never chooses public URLs or output paths.

Use headless Matplotlib and existing chart theme/palette settings. Titles, axes, units, legends, aggregation, missing values, and displayed subsets must be honest and readable. Follow-ups receive recent conversation and attachment descriptions, not image bytes or arbitrary paths. Text-only scripts remain compatible.

Limits: three charts per response; each PNG at most 5 MiB, 2400 pixels per side and 5.76 megapixels; bounded manifest and answer sizes; existing sandbox CPU/memory/timeout restrictions remain in force. Invalid images, invalid manifest entries, oversized output, and script failures must not be presented as successful results.

## API and persistence

- `POST /api/sessions/{job_id}/explore` keeps its form `prompt` input and `answer` string; adds a unique turn ID, chart descriptors, and the persisted history. Empty prompts are rejected.
- Each assistant message stores its turn ID, text, status, timestamp, and validated chart metadata. Chart descriptors include ID, title, caption, dimensions, and a server-generated same-origin URL; no absolute filesystem paths are returned.
- `GET /api/sessions/{job_id}/explore/{turn_id}/charts/{chart_id}` serves only a validated PNG referenced by that session's history. An optional download flag returns an attachment.
- Both endpoints use existing authentication. Cross-session, unknown, traversal, symlink, or non-PNG asset access is rejected.
- Each turn has unique script/output paths, and history appends are atomic so simultaneous requests cannot overwrite previous turns. A second in-flight exploration for the same session receives a retryable conflict.
- An exploration error is recorded in explorer history without marking an otherwise completed report as failed.
- Session deletion and retention include explorer scripts and chart directories through the existing generated-artifact lifecycle.

## Acceptance criteria

1. A mocked model script calculating an aggregate and emitting a real Matplotlib figure produces a text answer and authenticated, downloadable PNG in chat.
2. A text-only question remains supported without chart attachments.
3. Follow-ups have prior answer/chart descriptions; reloading restores all successful chart cards.
4. Invalid paths, symlinks, invalid/non-PNG images, excessive chart output, execution failures, and model timeouts fail safely without changing report state.
5. Pending/error/retry states, safe text rendering, narrow layouts, download links, and session switching are tested.
6. All automated tests mock provider calls; at least one integration test runs the real sandbox and image validation, and a browser regression exercises actual image display.

## Implementation plan

1. Add the chart helper/runtime and bounded manifest validation, using unique outputs under `workspace/reports/explore_<session>_<turn>/` so existing cleanup remains applicable.
2. Extend explorer generation prompts and script setup with headless plotting, theme settings, helper injection, and compact chart-aware conversation context.
3. Extract exploration orchestration into a service; persist turns atomically, isolate failures from report state, and expose authenticated image/download routes.
4. Render persistent explorer history and responsive attachment cards; add loading, image-error, and retry behavior with safe DOM APIs.
5. Add unit/security tests, real sandbox/API integration tests, and frontend/browser regressions; run the full suite, compile checks, and restart the development server for testing.

## Implemented verification

- `python -m unittest discover -s tests -p 'test_*.py'`: 201 tests passed, including real PNG execution, authenticated downloads, data-grounded totals, follow-ups, output validation, concurrency, cleanup, and unchanged report state on failures.
- `python -m compileall -q app tests`, JavaScript syntax checks, and `git diff --check`: passed.
- `node tests/explorer_charts.browser.cjs`: passed desktop/mobile chart display, pending/double-submit prevention, PNG download, history reload, retry, and missing-image handling with no browser errors. The test bridge mocks model replies but executes real Python and serves real chart images.
- `node tests/layout_editors.browser.cjs`: both existing editors passed with no browser errors.
- Browser tests accept `PLAYWRIGHT_MODULE`, `CHROMIUM_EXECUTABLE`, and (for the explorer fixture) `PYTHON_EXECUTABLE` when these dependencies are not on the default paths.

Only the feature spec/plan is added as documentation; unrelated documentation is unchanged.

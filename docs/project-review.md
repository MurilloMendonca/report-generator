# Report pipeline and usability review

The review focused on data interpretation, narrative generation, and the browser file workflow. The changes below are implemented; the follow-ups describe further work.

## Fixed

| Finding | Change | Regression coverage |
| --- | --- | --- |
| Narrative generation omitted the accepted report goal. Generic section titles could lose the audience, requested decision, or accepted refinements. | Pass the current goal and a compact analysis plan into narrative generation. Identify planned analyses as scope rather than evidence. | LLM prompt contracts and a complete mocked pipeline run. |
| Dataset relationship summaries were stored but never supplied to the planner. Multi-sheet workbooks were summarized as one dataset, hiding relationships within the workbook. | Profile sheets separately using the exact runtime dataframe keys. Supply candidate relationships, row counts, key cardinalities, types, and null counts to the planner, with explicit join-verification guidance. | Real two-sheet Excel fixture, loader-key agreement, legacy metadata, and planner prompt tests. |
| Preprocessed Excel workbooks were served and downloaded with CSV filenames/content types, and the browser tried to display binary workbook bytes as text. | Derive download names and content types from the processed file's actual extension. Keep original and processed metadata separate. | Real workbook metadata and Chromium download checks. |
| Spreadsheet previews downloaded entire workbooks to display raw metadata JSON. Tab switches accumulated download handlers and could receive stale preview responses. | Render bounded sample tables for every sheet from existing metadata, without fetching workbook bytes. Replace download handlers and invalidate stale requests. Honor detected encodings in text previews. | Node workflow/XSS checks and Chromium checks for sheet samples, zero preview downloads, correct filenames, and one download per click. |

## Next priorities

1. **Verify numerical claims in narrative text.** `app/services/llm/texts.py` currently validates response structure and required component IDs. Evidence rules remain prompt instructions. A structured fact-reference contract, followed by validation against calculated metrics, would make unsupported claims detectable before publishing a PDF. Evaluate it against the canonical reports, including unavailable metrics and percentage-point changes.
2. **Reduce repeated metric setup.** `app/services/pipeline_service.py` calculates metrics sequentially; `app/services/llm/metrics.py` asks for code, starts a sandbox, and loads all tabular sources for each metric. Deterministic common aggregations and bounded batches could reduce provider calls and repeated parsing. Preserve per-metric errors, report paths, budgets, and sandbox isolation, and measure this with the existing isolated load-check runner.

Automated validation uses mocked model responses and real sandbox execution. It checks behavior and isolation; it does not establish a measured improvement in live model report quality. Compare canonical report outputs before and after the prompt changes when evaluating a particular provider/model.

The local validation environment has Python 3.14; the project pins Python 3.12 for CI and Docker. Re-run the usual CI checks in that pinned environment before release.

## Component editor follow-up

The components workspace now has search/type filters, guided source and column selection, aggregation/sorting controls, chart-specific display options, theme/color controls, and a plain-text editor with a word count and instant reading preview. JSON and Python fields are collapsed under advanced settings for visuals. Component drafts survive polling, and failed requests keep their instruction available for retry.

Calculated metric artifacts link to their results in **Métricas & Dados** and explain that calculation changes go through the report chat. They cannot open the visual editor or request a rendered component preview, including when stale state contains component code. The component creation form offers visual and text types only.

The new component edit endpoint queues a background job with an explicit component ID and checks the submitted PDF version. It preserves other components, calculated metrics, report settings, and layout. Standard visual options use the library renderer without model calls; AI refinements use the selected component's current code/text, calculated evidence and layout constraints. Invalid attempts to expand the AI scope are rejected. Interrupted operations can be retried after restart.

Backend regressions cover direct text edits, chart options, scoped AI requests, scope rejection/PDF preservation, stale and overlapping requests, restart recovery, empty text, and clearing a theme override. The Chromium workflow covers source roles, filters, local draft preservation, immediate reading previews, progress, completion, errors/retry, and a narrow mobile viewport.

Two explicitly authorized live DeepSeek smoke checks used synthetic revenue data: a text revision preserved the total of 30, and a chart revision applied red bars (`#c62828`) with value labels. Both preserved the layout and calculated metrics. These focused checks complement the mocked automated suite; they do not replace a wider report-quality evaluation.

## Table fitting

The shared table renderer previously used equal-width columns, equal-height rows and fixed character truncation. Long headers and body text crossed cell borders, and a two-line title could overlap the header. Tables now measure text at export resolution, wrap words and long identifiers, allocate widths from content, size rows from their wrapped text, and reserve the measured title height. Font fitting has a minimum size; dense text uses visible ellipses and manifest warnings. Numeric values and total rows are preserved, and an unusably small slot reports that more space is required. Generation guidance routes custom tables through the same helpers.

Regression checks reproduce the original overflow and verify actual text bounding boxes against every cell and the title at 100/300 DPI. They cover long descriptions, wide identifiers, explicit line breaks, formatted positive/negative numbers, literal dollar signs, missing/nested values, duplicate headers, totals and tiny slots. Synthetic before/after PDFs also confirm the visual result. No provider calls are needed for table fitting.

## Fitting for the remaining components

The follow-up audit reproduced distinct long bar categories collapsing into one position after name truncation, clipped chart titles and legends, crowded donut labels, oversized gauge values and inaccurate narrative line estimates. Bar positions now retain category identities independently of display labels. Shared measured chart layout reserves title, legend and axis space, keeps a readable plot area, wraps labels, retains identifying suffixes when shortening is necessary, and avoids overlapping optional annotations/ticks without dropping data points. Donuts use category/percentage legends, and central values fit by font size without cutting digits. Single-axis custom charts using the shared save helper receive layout fitting too.

Narrative generation guidance and PDF output now use the same font metrics, measured line breaks and heading reserve. Output can reduce the font within a bounded range and visibly mark any remaining truncation. Heading alignment and component clipping prevent text from crossing its slot. Unusable chart, table and text slots produce `layout_too_small` and resizing guidance, without model repair. The new helpers live in `chart_layout.py` and `pdf_text.py`; their source is bundled into the sandbox renderer.

Checks cover category identity and values, dense date ticks, long multi-series legends, tick/annotation collisions, donut slice totals, large formatted gauge values, tiny slots, several PDF fonts, report headings and real sandbox text rendering. Synthetic compact PDFs provide visual comparison with the audit examples. These fitting changes require no live provider calls.

## Read-only sharing

Owners can now create named bearer links with a 24-hour, 7-day, 30-day, or unlimited lifetime and revoke each link independently. The raw address is returned once; Postgres stores its SHA-256 hash in `report_share_links` (migration `87ba33034e39`). Each public request rechecks expiry, revocation, report existence and owner status. Existing mutation routes continue to require the report owner's account and CSRF token.

The separate public viewer exposes the report's PDF, components/code, layout, settings, metrics, file previews/downloads, historical source snapshots, processing logs, and prior explorer results. It uses only public GET routes, omits account/credential/connector/usage data, and does not execute generated code or make model calls. It polls every 15 seconds for updates and access changes, clearing displayed content when access is revoked. Public responses use `no-store`, `no-referrer`, and `noindex` headers; sharing tokens are also redacted from access logs.

Sharing regressions cover anonymous and signed-in recipients, owner-only link management, inclusive expiry boundaries, permanent links, independent revocation, report deletion, disabled owners, token hashing, private-field exclusion, token-log redaction, and symlink-safe file access. Chromium covers expiry choices, no-login viewing of every panel, original/preprocessed file previews, narrow mobile layout, and revocation clearing previously displayed content. The full local suite uses `MPLBACKEND=Agg` to keep Matplotlib headless.

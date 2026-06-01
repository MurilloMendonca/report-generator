# Canonical Session Test Loop

This folder defines repeatable end-to-end report-generation sessions. Each case uses the provider/model declared in `playbook.json` and can mix CSV, XLSX, and PDF uploads.

Canonical sessions are meant to catch regressions that normal unit tests miss: broken generated PDFs, layout overlap, missing data columns, preprocessing failures, chart annotation/legend collisions, and prompts that drift away from the expected report intent.

## Generate Fixtures

```bash
venv/bin/python tests/canonical/generate_fixtures.py
```

Fixtures are written under `tests/canonical/fixtures/`.

## Review the Playbook

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --dry-run
```

The playbook includes:

- input files
- initial report prompt
- optional refinement prompts
- expected columns and report keywords
- selected layout template
- optional preprocessing expectations, including computed columns

## Run Canonical Sessions

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py
```

Run one case:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --case sales_ops
```

Run the preprocessing case:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --case sales_ops_preprocessing
```

Run refinements after initial generation:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --with-refinements
```

## Validation

The runner validates:

- session reaches a completed status
- PDF exists and is larger than 20 KB
- PDF page count covers the selected template pages
- template blocks are within A4 bounds
- template blocks do not have major overlap
- metrics include at least one non-`N/A` value
- expected dataset columns appear in metadata
- expected report keywords appear in generated script or metadata
- PDF text can be extracted
- PDF text includes the generated title
- PDF text does not contain cut-off ellipses
- first page can be rasterized for manual visual review
- chart code avoids known annotation/legend overlap patterns
- preprocessing cases produce a derived file under `workspace/filtered`
- computed preprocessing columns appear in processed metadata

Results are saved to `tests/canonical/results/`.

Each normal run now creates a timestamped artifact folder:

```text
tests/canonical/results/{timestamp}/{case_id}/
```

Each case folder contains:

- `session.json`
- `report.pdf`
- `page-1.png`
- `pdftotext.txt`
- `checks.json`

For easier manual review, every generated canonical PDF is also copied to:

```text
tests/canonical/generated_pdfs/
```

This folder is intentionally review-friendly: open the PDFs there after a canonical run and inspect spacing, title wrapping, text fit, chart legends, annotations, and whether the report content matches the source data.

Skip artifact capture when needed:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --no-save-artifacts
```

## Current Cases

- `sales_ops`: standard sales report using CSV, XLSX, and PDF context.
- `sales_ops_filter`: verifies that requested row filtering happens before metrics/components.
- `sales_ops_preprocessing`: verifies general preprocessing by creating computed columns before analysis.
- `sales_ops_exploratory`: starts from a blank prompt and expects an exploratory report.
- `hr_retention`: people analytics report with refinements.
- `logistics_quality`: logistics report used heavily for chart-layout regression checks.
- `education_outcomes`: two-page education outcomes report.

## Notes

- These tests call live LLM providers and can vary between runs. The checks are intentionally structural and content-aware rather than pixel-perfect.
- The generated `session.json` is often the best debugging artifact when a case fails.
- If a PDF looks bad but checks pass, add a targeted validator to `run_canonical_sessions.py` and a unit test in `tests/test_canonical_runner.py`.

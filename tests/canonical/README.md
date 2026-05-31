# Canonical Session Test Loop

This folder defines repeatable end-to-end report-generation sessions. Each case uses DeepSeek and mixes CSV, XLSX, and PDF uploads.

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

## Run Canonical Sessions

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py
```

Run one case:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --case sales_ops
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

Skip artifact capture when needed:

```bash
venv/bin/python tests/canonical/run_canonical_sessions.py --no-save-artifacts
```

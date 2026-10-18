# web/: the demo page

A static multi-file app; any static server serves it (GitHub Actions publishes this folder to
GitHub Pages, `scripts/serve_demo.sh` serves it locally). The page fetches its data at load, so
it has to be served over http; opened straight from the file it shows a hint instead.

```
index.html      the shell: sidebar, main column, Ask dock
styles.css      tokens (light and dark), layout, components
strings.js      every piece of copy, Finnish and English
router.js       the rule-based router and guardrail (port of ai/router_rules.py)
app.js          the pure selection module (AEI) and the DOM shell
data/           data.json, alerts.json (built), router.json, illustrative.json, ai_eval.json (exported)
assets/brand/   logo.png (Tyks), region.png (Varha); see its README
```

## Layout

- **Sidebar**: brand block, the five views as a vertical list, and the global selections (role,
  care unit, language, theme, print). Folds to an icon rail; below 56 rem it becomes a top bar.
- **Main column**: the active view. Clinician (organism × antibiotic matrix with % R, n and the
  95 % interval, the last 4 weeks against the year, alerts on the row; collapses to the asked
  organisms after a question), infection control (graded alerts with timelines, cluster table,
  always-alert isolates, data quality), stewardship (year against year per county, group or
  unit), explore (every cell and every scope decision, filterable), lab IT (pipeline,
  configuration, provenance, data quality, the model evaluation).
- **Ask dock**: the question input, the example dropdown, the model selector and the
  interpretation block (verdict, one chip per field read with its source, what the page did, and
  a fold with the full table and raw data). Folds to an edge tab; below 72 rem it sits above the
  view. The view a question opened carries a result strip.

## Launch parameters

`?role=clinician|infection|steward|labit` narrows the views to the role; `unit=URO` and
`specimen=urine` preset the clinician view; `lang=fi|en`; `theme=light|dark`;
`ai=http://127.0.0.1:8765` names the Ask endpoint (required on the hosted https page);
`debug=1` shows the request trace in the lab IT view; the view as the hash (`#micro`).

## Rules the page keeps

Every number comes from `data/*.json`; the one exception is the trend indicator's illustrative
value, always labelled as an example. No percentage is shown without its n and interval. A
widened, lifted or suppressed cell says why. No patient-level field reaches this folder.

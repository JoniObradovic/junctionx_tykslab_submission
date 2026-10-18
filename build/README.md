# build/: from the CSVs to the page's data

```
data/*.csv --> build/build_cube.py --> web/data/data.json
               build/detect.py     --> web/data/alerts.json
               build/export_web.py --> web/data/router.json, illustrative.json, ai_eval.json
```

```bash
uv sync
uv run python build/build_cube.py
uv run python build/detect.py
uv run python build/export_web.py
```

The built files are committed, so the page works without running this.

- **config.py** holds every rule the laboratory would own: the intrinsic-resistance pairs, the
  unit hierarchy, the minimum sample (30 isolates), the window ladder (4, 8, 13, 26, 52 weeks and
  calendar years), the suppression threshold (cells under 5 isolates are never written), the
  detector thresholds, the always-alert phenotypes and the illustrative trend-indicator block. The
  other scripts import it; no constant is typed twice.
- **build_cube.py**: one isolate per patient, organism and specimen group per window; intrinsic
  resistance excluded; the cube over windows at unit, unit-group and county level; Wilson
  intervals; the honest-scope decision per cell (own window, widened, lifted to a higher level,
  suppressed) with the reason recorded; cells under the suppression threshold removed. Prints a
  summary with counts only.
- **detect.py**: scans the whole history in 14-day steps. Rise in resistance (last 4 weeks
  against the preceding year on the same unit, Fisher's exact test, lifted to a higher level when
  isolates are few), phenotype clusters (same organism and phenotype within 14 days against the
  unit's own expected count, Poisson tail), always-alert isolates, data-quality checks. Graded
  watch and alert, with an evidence sentence in Finnish and English per episode.
- **export_web.py**: writes the keyword tables of the rule-based router from `ai/router_rules.py`
  (so the browser and Python share one vocabulary), the illustrative block, and the recorded model
  evaluation from `ai/results/eval.json`.

Every number on the page comes from these files. The only exception is the trend indicator's
illustrative value, which lives in its own named block and is always labelled as an example.

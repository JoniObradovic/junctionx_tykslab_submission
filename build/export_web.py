"""Export the small JSON files the page fetches beside the cube and the alerts.

build_cube.py writes web/data/data.json and detect.py writes web/data/alerts.json. This script adds
the three files that come from code rather than from the dataset: the illustrative block from
config (the trend-indicator example, always labelled as such), the keyword tables of the rule-based
router (ai/router_rules.py, so nothing is typed twice between Python and the browser) and the
recorded model evaluation (ai/results/eval.json, an empty object when it has not been run).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # `python build/export_web.py`
from build import config  # noqa: E402
from ai import router_rules  # noqa: E402

AI_EVAL_JSON = config.ROOT / "ai" / "results" / "eval.json"


def _write(name: str, obj) -> None:
    path = config.WEB_DATA_DIR / name
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path.relative_to(config.ROOT)} ({path.stat().st_size / 1024:.0f} kB)")


def main() -> None:
    config.WEB_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write("illustrative.json", config.ILLUSTRATIVE)
    _write("router.json", router_rules.tables())
    _write("ai_eval.json", json.loads(AI_EVAL_JSON.read_text(encoding="utf-8")) if AI_EVAL_JSON.exists() else {})


if __name__ == "__main__":
    main()

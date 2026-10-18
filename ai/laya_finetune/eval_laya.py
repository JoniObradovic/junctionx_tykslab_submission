"""Score a Laya guard (zero-shot or fine-tuned) on the labelled requests.

    uv run --group laya python ai/laya_finetune/eval_laya.py --device cpu --tag zero-shot
    uv run --group laya python ai/laya_finetune/eval_laya.py --model-dir ai/laya_finetune/model --device cuda --tag finetuned

Writes ai/results/laya_<tag>.json (counts and rates: intent and slice accuracy, redirect recall,
false-redirect rate, latency per request on the device, calibration of P(patient_specific) on
the held-out ids) and the per-request predictions beside it. Nothing from data/ is read.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ai import laya_guard, router_rules  # noqa: E402
from ai.laya_finetune import eval_calibration, make_dataset  # noqa: E402
from build import config  # noqa: E402

RESULTS = config.ROOT / "ai" / "results"
REQUESTS = config.ROOT / "ai" / "requests.jsonl"


def score(rows, preds):
    n = len(rows)
    pos = [(r, p) for r, p in zip(rows, preds) if r["intent"] == "patient_specific"]
    neg = [(r, p) for r, p in zip(rows, preds) if r["intent"] != "patient_specific"]
    fields = {k: sum(1 for r, p in zip(rows, preds) if p["slice"][k] == r["slice"][k]) / n for k in router_rules.SLICE_FIELDS}
    return {"n": n,
            "intent_accuracy": round(sum(1 for r, p in zip(rows, preds) if p["intent"] == r["intent"]) / n, 4),
            "view_accuracy": round(sum(1 for r, p in zip(rows, preds) if p["view"] == r["view"]) / n, 4),
            "slice_accuracy": round(sum(1 for r, p in zip(rows, preds) if p["slice"] == r["slice"]) / n, 4),
            "field_accuracy": {k: round(v, 4) for k, v in fields.items()},
            "redirect_recall": round(sum(1 for r, p in pos if p["intent"] == "patient_specific") / max(len(pos), 1), 4),
            "false_redirect_rate": round(sum(1 for r, p in neg if p["intent"] == "patient_specific") / max(len(neg), 1), 4)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--model-dir", default=None, help="fine-tuned checkpoint folder; omit for the hub multilingual checkpoint")
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--tag", default="zero-shot")
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--holdout-only", action="store_true")
    ap.add_argument("--requests", default=str(REQUESTS), help="labelled JSONL to score (e.g. ai/requests_stress.jsonl, never trained on)")
    args = ap.parse_args()
    rows = [json.loads(l) for l in Path(args.requests).read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.holdout_only:
        rows = [r for r in rows if make_dataset.is_holdout(r["id"])]
    t0 = time.perf_counter()
    guard = laya_guard.LayaGuard(args.model_dir, device=args.device)
    load_s = time.perf_counter() - t0
    # single-request latency, the number a laptop user feels
    singles = []
    for r in rows[:20]:
        singles.append(guard.predict(r["text"])["latency_ms"])
    preds = guard.predict_many([r["text"] for r in rows], batch_size=args.batch_size)
    out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tag": args.tag, "model": guard.model,
           "device": args.device, "load_s": round(load_s, 1), "n_requests": len(rows),
           "latency_ms_single_p50": round(statistics.median(singles), 1), "latency_ms_single_p95": round(sorted(singles)[int(0.95 * len(singles)) - 1], 1),
           "latency_ms_batched_per_request": round(preds[0]["latency_ms"], 1) if preds else None,
           "all": score(rows, preds)}
    hold = [(r, p) for r, p in zip(rows, preds) if make_dataset.is_holdout(r["id"])]
    if hold:
        hr, hp = zip(*hold)
        out["holdout"] = score(list(hr), list(hp))
        probs = [p["p_intent"]["patient_specific"] for p in hp]
        labels = [1 if r["intent"] == "patient_specific" else 0 for r in hr]
        out["holdout"]["calibration"] = eval_calibration.summarise(probs, labels)
        (RESULTS / f"calibration_laya_{args.tag}.svg").write_text(eval_calibration.reliability_svg(
            out["holdout"]["calibration"]["reliability"], f"Laya {args.tag}: P(patient-specific), held-out n = {len(hold)}, ECE = {out['holdout']['calibration']['ece']:.3f}"), encoding="utf-8")
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"laya_{args.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (RESULTS / f"predictions_laya_{args.tag}.jsonl").write_text("".join(json.dumps({"id": r["id"], "label_intent": r["intent"], "label_slice": r["slice"], **{k: p[k] for k in ("intent", "view", "slice", "p_intent")}}, ensure_ascii=False) + "\n" for r, p in zip(rows, preds)), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "holdout"}, indent=1))
    if "holdout" in out:
        print("holdout:", json.dumps({k: v for k, v in out["holdout"].items() if k != "calibration"}), "| ECE", out["holdout"]["calibration"]["ece"])
    print("wrote", RESULTS / f"laya_{args.tag}.json")


if __name__ == "__main__":
    main()

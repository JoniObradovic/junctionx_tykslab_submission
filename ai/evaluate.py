"""Evaluate the router (rules, local language model, their combination) on the labelled requests.

Writes ai/results/eval.json (counts and rates only; embedded into the page as the "ai_eval"
block by build/embed.py), ai/results/predictions.jsonl (one row per request: id, labels and
predictions, no numbers from the cube) and ai/results/calibration.svg (reliability of the
model's P(patient-specific) on the held-out ids of ai/laya_finetune/make_dataset.py).

    uv run python ai/evaluate.py            # rules + model if the llama.cpp server answers
    uv run python ai/evaluate.py --no-llm   # rules only

Accuracy here is against a set written by the same people as the rules: a specification, not
an independent estimate. Say so wherever the numbers are shown.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai import nl2cube, router_rules  # noqa: E402
from ai.laya_finetune import eval_calibration, make_dataset  # noqa: E402
from build import config  # noqa: E402

RESULTS = config.ROOT / "ai" / "results"
REQUESTS = config.ROOT / "ai" / "requests.jsonl"


def _pct(x, n):
    return round(x / n, 4) if n else None


def _percentile(values, q):
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


def score(rows: list[dict], preds: list[dict], key: str) -> dict:
    n = len(rows)
    intent_ok = sum(1 for r, p in zip(rows, preds) if p[key]["intent"] == r["intent"])
    slice_ok = sum(1 for r, p in zip(rows, preds) if p[key]["slice"] == r["slice"])
    view_ok = sum(1 for r, p in zip(rows, preds) if p[key]["view"] == r["view"])
    pos = [(r, p) for r, p in zip(rows, preds) if r["intent"] == "patient_specific"]
    recall = sum(1 for r, p in pos if p[key]["intent"] == "patient_specific")
    neg = [(r, p) for r, p in zip(rows, preds) if r["intent"] != "patient_specific"]
    false_redirect = sum(1 for r, p in neg if p[key]["intent"] == "patient_specific")
    lat = [p[key]["latency_ms"] for p in preds if p[key].get("latency_ms") is not None]
    return {"n": n, "intent_accuracy": _pct(intent_ok, n), "view_accuracy": _pct(view_ok, n),
            "slice_accuracy": _pct(slice_ok, n), "redirect_recall": _pct(recall, len(pos)),
            "false_redirect_rate": _pct(false_redirect, len(neg)),
            "latency_ms_p50": _percentile(lat, 0.5), "latency_ms_p95": _percentile(lat, 0.95)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--no-llm", action="store_true", help="rules only")
    ap.add_argument("--profile", default=nl2cube.DEFAULT_PROFILE, choices=sorted(nl2cube.PROFILES) + ["laya"], help="model profile (ai/nl2cube.py PROFILES) or laya (in-process guard)")
    ap.add_argument("--laya-device", default="cpu")
    ap.add_argument("--holdout-only", action="store_true", help="score the model on the held-out ids only (slow profiles)")
    args = ap.parse_args()
    rows = [json.loads(l) for l in REQUESTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.holdout_only:
        rows = [r for r in rows if make_dataset.is_holdout(r["id"])]
    tab = router_rules.tables()
    guard = None
    if args.profile == "laya":
        from ai import laya_guard
        guard = laya_guard.LayaGuard(device=args.laya_device)
        prof = {"url": "in-process", "label": "Laya guard, " + guard.model + ", " + args.laya_device, "thinking": None}
        use_llm, model_name = not args.no_llm, guard.model
    else:
        prof = nl2cube.PROFILES[args.profile]
        use_llm = not args.no_llm and nl2cube.health(prof["url"])["ok"]
        model_name = nl2cube.health(prof["url"])["model"] if use_llm else None
    suffix = "_" + args.profile
    preds = []
    t_start = time.perf_counter()
    for i, r in enumerate(rows):
        t0 = time.perf_counter()
        rules = router_rules.route(r["text"], r["lang"], tab)
        rules_ms = (time.perf_counter() - t0) * 1000
        rec = {"id": r["id"], "lang": r["lang"], "label_intent": r["intent"], "label_view": r["view"], "label_slice": r["slice"],
               "holdout": make_dataset.is_holdout(r["id"]),
               "rules": {"intent": rules["intent"], "view": rules["view"], "slice": rules["slice"], "latency_ms": rules_ms}}
        if use_llm:
            llm = guard.predict(r["text"]) if guard else nl2cube.extract(r["text"], r["lang"], tab=tab, profile=args.profile)
            if llm.get("ok"):
                comb = nl2cube.combine(rules, llm)
                rec["llm"] = {"intent": llm["intent"], "view": llm["view"], "slice": llm["slice"], "p_intent": llm.get("p_intent"),
                              "latency_ms": llm["latency_ms"], "model": llm.get("model"), "reasoning_chars": llm.get("reasoning_chars", 0),
                              "completion_tokens": (llm.get("usage") or {}).get("completion_tokens")}
                rec["combined"] = {"intent": comb["intent"], "view": comb["view"], "slice": comb["slice"],
                                   "latency_ms": llm["latency_ms"] + rules_ms}
            else:
                rec["llm"] = {"error": llm.get("error"), "latency_ms": llm.get("latency_ms")}
        preds.append(rec)
        if (i + 1) % 20 == 0:
            print(f"{i + 1}/{len(rows)} done, {time.perf_counter() - t_start:.0f} s", flush=True)

    # eval.json accumulates one entry per model profile; the rules block is refreshed on every run
    # over the full set. "llm" and "combined" stay as aliases of the default profile for readers
    # that predate profiles.
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = json.loads((RESULTS / "eval.json").read_text(encoding="utf-8")) if (RESULTS / "eval.json").exists() else {}
    out.update({"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "labelled_set": "ai/requests.jsonl",
                "note": "labelled by the authors of the rules: a specification of behaviour, not an independent estimate"})
    if not args.holdout_only:
        out["n_requests"] = len(rows)
        out["n_holdout"] = sum(1 for p in preds if p["holdout"])
        out["rules"] = score(rows, preds, "rules")
    out.setdefault("models", {})
    with_llm = [(r, p) for r, p in zip(rows, preds) if "llm" in p and "intent" in p["llm"]]
    if with_llm:
        rr, pp = zip(*with_llm)
        m = dict(score(list(rr), list(pp), "llm"), model=model_name or pp[0]["llm"].get("model"), label=prof["label"],
                 thinking=prof.get("thinking"), subset="holdout" if args.holdout_only else "all",
                 failed=sum(1 for p in preds if "llm" in p and "intent" not in p["llm"]),
                 reasoning_chars_mean=round(statistics.mean(p["llm"].get("reasoning_chars", 0) for _, p in with_llm), 0),
                 completion_tokens_mean=round(statistics.mean(p["llm"].get("completion_tokens") or 0 for _, p in with_llm), 0),
                 combined=score(list(rr), list(pp), "combined"),
                 agreement={"intent": _pct(sum(1 for _, p in with_llm if p["llm"]["intent"] == p["rules"]["intent"]), len(with_llm)),
                            "slice": _pct(sum(1 for _, p in with_llm if p["llm"]["slice"] == p["rules"]["slice"]), len(with_llm))})
        hold = [(r, p) for r, p in with_llm if p["holdout"] and p["llm"].get("p_intent")]
        if hold:
            probs = [p["llm"]["p_intent"]["patient_specific"] for _, p in hold]
            labels = [1 if r["intent"] == "patient_specific" else 0 for r, _ in hold]
            m["calibration"] = eval_calibration.summarise(probs, labels)
            (RESULTS / f"predictions_holdout{suffix}.jsonl").write_text(
                "".join(json.dumps({"id": r["id"], "p": p, "y": y}) + "\n" for (r, _), p, y in zip(hold, probs, labels)), encoding="utf-8")
            (RESULTS / f"calibration{suffix}.svg").write_text(eval_calibration.reliability_svg(
                m["calibration"]["reliability"], f"{prof['label']}: P(patient-specific), held-out n = {len(hold)}, ECE = {m['calibration']['ece']:.3f}"), encoding="utf-8")
        out["models"][args.profile] = m
    default = out["models"].get(nl2cube.DEFAULT_PROFILE)
    if default:
        out["llm"] = {k: v for k, v in default.items() if k not in ("combined", "agreement")}
        out["combined"] = default["combined"]
        out["agreement"] = default["agreement"]
    (RESULTS / "eval.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (RESULTS / f"predictions{suffix}.jsonl").write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in preds), encoding="utf-8")
    shown = {k: (v if k != "models" else {n: {kk: vv for kk, vv in mm.items() if kk != "calibration"} for n, mm in v.items()}) for k, v in out.items() if k in ("n_requests", "rules", "models")}
    print(json.dumps(shown, indent=1, default=str)[:4000])
    print("wrote", RESULTS / "eval.json")


if __name__ == "__main__":
    main()

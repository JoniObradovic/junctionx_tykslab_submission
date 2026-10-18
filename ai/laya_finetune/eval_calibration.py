"""Calibration of a binary "patient-specific?" probability: ECE, Brier and a reliability plot.

Used by ai/evaluate.py on the language model's intent probability and meant for the Laya
guardrail once it is fine-tuned. Input is a JSONL of
{"p": probability of patient_specific, "y": 0 or 1}; the plot is an inline-style SVG with no
external dependency so it drops into the pitch.

    uv run python ai/laya_finetune/eval_calibration.py ai/results/predictions_holdout.jsonl \\
        --svg ai/results/calibration.svg
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def brier(probs: list[float], labels: list[int]) -> float:
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / max(len(probs), 1)


def reliability(probs: list[float], labels: list[int], bins: int = 10) -> list[dict]:
    """Equal-width bins over [0, 1]: count, mean predicted probability, observed frequency."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, p in enumerate(probs) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if idx:
            out.append({"lo": lo, "hi": hi, "n": len(idx),
                        "confidence": sum(probs[i] for i in idx) / len(idx),
                        "accuracy": sum(labels[i] for i in idx) / len(idx)})
        else:
            out.append({"lo": lo, "hi": hi, "n": 0, "confidence": None, "accuracy": None})
    return out


def ece(probs: list[float], labels: list[int], bins: int = 10) -> float:
    n = max(len(probs), 1)
    return sum(r["n"] / n * abs(r["accuracy"] - r["confidence"]) for r in reliability(probs, labels, bins) if r["n"])


def reliability_svg(rows: list[dict], title: str = "Reliability") -> str:
    """A 10-bin reliability diagram: diagonal, bars of observed frequency, counts under the axis.

    Standalone asset for the pitch, so the colours are the page's light-theme token values typed
    here; the page itself draws the same diagram from the embedded JSON with its CSS tokens.
    """
    W, H, pad = 360, 300, 40
    plot_w, plot_h = W - 2 * pad, H - 2 * pad - 20
    x = lambda v: pad + v * plot_w
    y = lambda v: pad + (1 - v) * plot_h
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
             'font-family="IBM Plex Sans, system-ui, sans-serif" font-size="11">',
             f'<title>{title}</title>',
             f'<rect x="0" y="0" width="{W}" height="{H}" fill="#ffffff"/>',
             f'<text x="{pad}" y="{pad - 18}" font-size="13" font-weight="600" fill="#1b1f3a">{title}</text>',
             f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(1)}" stroke="#aeb8c6" stroke-dasharray="4 3"/>']
    for r in rows:
        bw = (r["hi"] - r["lo"]) * plot_w
        if r["n"]:
            parts.append(f'<rect x="{x(r["lo"]) + 1:.1f}" y="{y(r["accuracy"]):.1f}" width="{bw - 2:.1f}" '
                         f'height="{y(0) - y(r["accuracy"]):.1f}" fill="#7cc0dc" opacity="0.85"/>')
            parts.append(f'<circle cx="{x(r["confidence"]):.1f}" cy="{y(r["accuracy"]):.1f}" r="3" fill="#104f92"/>')
        parts.append(f'<text x="{x(r["lo"]) + bw / 2:.1f}" y="{y(0) + 14}" text-anchor="middle" fill="#58637a">{r["n"]}</text>')
    parts.append(f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(1)}" y2="{y(0)}" stroke="#363d52"/>')
    parts.append(f'<line x1="{x(0)}" y1="{y(0)}" x2="{x(0)}" y2="{y(1)}" stroke="#363d52"/>')
    for v in (0, 0.5, 1):
        parts.append(f'<text x="{x(v):.1f}" y="{y(0) + 28}" text-anchor="middle" fill="#363d52">{v:g}</text>')
        parts.append(f'<text x="{x(0) - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#363d52">{v:g}</text>')
    parts.append(f'<text x="{x(0.5):.1f}" y="{H - 4}" text-anchor="middle" fill="#363d52">predicted P(patient-specific); n per bin above</text>')
    parts.append(f'<text transform="translate(12 {y(0.5):.1f}) rotate(-90)" text-anchor="middle" fill="#363d52">observed frequency</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def summarise(probs: list[float], labels: list[int], bins: int = 10) -> dict:
    return {"n": len(probs), "ece": round(ece(probs, labels, bins), 4), "brier": round(brier(probs, labels), 4),
            "positive_rate": round(sum(labels) / max(len(labels), 1), 4), "bins": bins,
            "reliability": [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}
                            for r in reliability(probs, labels, bins)]}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("predictions", help="JSONL with p (P(patient_specific)) and y (0/1)")
    ap.add_argument("--svg", help="write the reliability diagram here")
    ap.add_argument("--bins", type=int, default=10)
    args = ap.parse_args()
    rows = [json.loads(l) for l in Path(args.predictions).read_text(encoding="utf-8").splitlines() if l.strip()]
    probs = [float(r["p"]) for r in rows]
    labels = [int(r["y"]) for r in rows]
    s = summarise(probs, labels, args.bins)
    print(json.dumps({k: v for k, v in s.items() if k != "reliability"}))
    if args.svg:
        Path(args.svg).write_text(reliability_svg(s["reliability"], f"Reliability, n = {s['n']}, ECE = {s['ece']:.3f}"), encoding="utf-8")
        print("wrote", args.svg)


if __name__ == "__main__":
    main()

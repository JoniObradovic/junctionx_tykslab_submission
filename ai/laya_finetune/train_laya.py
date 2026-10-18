"""Fine-tune the Laya multilingual checkpoint as the Ask-box guardrail.

Laya (Convai Innovations, Apache 2.0) ships a training loop, `laya.train.finetune`, that takes
rows of {state, questions, gold} and trains the encoder and the decision head with its RLCD
objective (a proper scoring rule), then fits one temperature per question type on a slice of the
training rows. The rows come from ai/laya_finetune/make_dataset.py through
`ai/laya_guard.training_rows` (one row per labelled request, nine `choice` questions whose
options are the cube's vocabulary; the `patient_specific` option's probability is the gate).

    uv run --group laya python ai/laya_finetune/train_laya.py --dry-run          # dataset and heads only
    uv run --group laya python ai/laya_finetune/train_laya.py --device cuda      # one GPU, fp16 autocast
    uv run --group laya python ai/laya_finetune/eval_laya.py --model-dir ai/laya_finetune/model --device cuda --tag finetuned

The `laya` dependency group pins torch 2.7.1, the build the shipped checkpoint was trained with. Calibration is reported
on the held-out split by eval_laya.py, not from the training rows.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from ai import laya_guard, router_rules  # noqa: E402

DATASET = HERE / "dataset"
SUFFIX = [""]   # set from --split so the row files of different runs do not overwrite each other
MODEL_DIR = HERE / "model"
BASE_DIR = HERE / "base"
HEADS = ("intent", "view") + router_rules.SLICE_FIELDS


def load_split(name: str) -> list[dict]:
    path = DATASET / f"{name}.jsonl"
    if not path.exists():
        sys.exit(f"{path} missing: run ai/laya_finetune/make_dataset.py first")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def describe(train: list[dict], hold: list[dict], qs: dict) -> dict:
    summary = {"train": len(train), "holdout": len(hold), "heads": {}}
    for head in HEADS:
        golds = Counter(laya_guard.gold_for(r)[head] for r in train if head != "view" or r["intent"] == "aggregate")
        options = list(qs[head]["criteria"])
        summary["heads"][head] = {"options": len(options), "train_support_min": min(golds.values()),
                                  "options_without_train_examples": [o for o in options if o not in golds]}
    return summary


def write_rows(train: list[dict], hold: list[dict], qs: dict) -> tuple[Path, Path]:
    paths = []
    for name, recs in (("laya_train" + SUFFIX[0], train), ("laya_holdout" + SUFFIX[0], hold)):
        rows = laya_guard.training_rows(recs, qs)
        p = DATASET / f"{name}.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        paths.append(p)
    return paths[0], paths[1]


def prepare_base(base_dir: Path) -> Path:
    """The multilingual checkpoint as a local folder laya.train can read."""
    target = base_dir / laya_guard.HUB_SUBFOLDER
    if (target / "model.safetensors").exists():
        return target
    from huggingface_hub import snapshot_download
    from laya.agent import _fix_tokenizer_config
    snapshot_download(laya_guard.HUB_ID, local_dir=str(base_dir), allow_patterns=[f"{laya_guard.HUB_SUBFOLDER}/*"])
    _fix_tokenizer_config(str(target))
    return target


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--micro-batch", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--output-dir", default=str(MODEL_DIR))
    ap.add_argument("--base-dir", default=str(BASE_DIR))
    ap.add_argument("--encoder-lr", type=float, default=2.5e-5)
    ap.add_argument("--head-lr", type=float, default=1e-4)
    ap.add_argument("--label-smoothing", type=float, default=0.0)
    ap.add_argument("--shuffle-options", action="store_true", help="shuffle option order of the slice questions during training (position robustness)")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--dry-run", action="store_true", help="check the dataset and label heads only")
    ap.add_argument("--split", default="", help='"" for make_dataset.py splits, "aug" for augment.py splits (train_aug, holdout_aug)')
    args = ap.parse_args()
    qs = laya_guard.questions()
    suffix = f"_{args.split}" if args.split else ""
    SUFFIX[0] = suffix
    train_rows, hold_rows = load_split("train" + suffix), load_split("holdout" + suffix)
    print(json.dumps(describe(train_rows, hold_rows, qs), ensure_ascii=False, indent=1))
    train_path, hold_path = write_rows(train_rows, hold_rows, qs)
    print(f"rows: {train_path.name} ({len(train_rows)}), {hold_path.name} ({len(hold_rows)})")
    if args.dry_run:
        print("dry run only: no training")
        return
    from laya.train import TrainConfig, finetune
    base = prepare_base(Path(args.base_dir))
    shuffle = ("choice",) if args.shuffle_options else ()   # laya shuffles by question type, all ours are choice
    cfg = TrainConfig(epochs=args.epochs, micro_batch=args.micro_batch, grad_accum=args.grad_accum,
                      encoder_lr=args.encoder_lr, head_lr=args.head_lr, label_smoothing=args.label_smoothing,
                      shuffle_options=shuffle, seed=args.seed, calib_seed=args.seed, eval_data=str(hold_path), log_every=20)
    t0 = time.perf_counter()
    summary = finetune(data=str(train_path), model_dir=str(base), output_dir=args.output_dir, config=cfg, device=args.device)
    minutes = (time.perf_counter() - t0) / 60
    keep = {k: v for k, v in summary.items() if k not in ("history",)}
    (Path(args.output_dir) / "train_summary.json").write_text(json.dumps({"minutes": round(minutes, 1), "args": vars(args), **keep}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"trained in {minutes:.1f} min; items {summary.get('train_items')}, calibration items {summary.get('calibration_items')}")
    print("temperatures (choice, score, noul):", summary.get("temperature"))
    print("saved to", args.output_dir)


if __name__ == "__main__":
    main()

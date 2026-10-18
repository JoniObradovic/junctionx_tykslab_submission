# Laya guardrail: dataset, fine-tune, calibration, laptop deployment

Laya (Convai Innovations, Apache 2.0) is a
non-autoregressive decision model: it answers typed questions over a text with probabilities in
one forward pass and generates nothing, so it cannot express a number or a patient. Here the
intent and every slice field are `choice` questions whose options are the cube's own vocabulary
(`ai/laya_guard.py: questions()`), and the probability of the `patient_specific` option is the
guard's gate. The multilingual checkpoint (mmBERT-base, 322M parameters) reads Finnish.

## What is here

| File | Purpose |
|---|---|
| `make_dataset.py` | Builds `dataset/train.jsonl`, `dataset/holdout.jsonl` and `dataset/schema.json` from the hand-written requests in `ai/requests.jsonl` plus templated FI/EN requests. Deterministic split by id, one in five held out. |
| `../laya_guard.py` | The questions, the gold format, `LayaGuard` (load once, `predict` and `predict_many`), `training_rows` for Laya's trainer. |
| `train_laya.py` | Downloads the base checkpoint, writes `dataset/laya_train.jsonl` and `dataset/laya_holdout.jsonl`, runs `laya.train.finetune` (RLCD objective, temperature fit) and saves `model/`. `--dry-run` checks the data side only. |
| `eval_laya.py` | Scores a checkpoint (zero-shot or `--model-dir model`) on the labelled requests: intent and slice accuracy, redirect recall, false-redirect rate, single and batched latency on the chosen device, calibration of P(patient-specific) on the held-out ids. Writes `ai/results/laya_<tag>.json` and a reliability SVG. |
| `eval_calibration.py` | ECE, Brier and the reliability diagram, shared with the language-model evaluation. |

## Measured (`ai/results/laya_zero-shot.json`, `laya_finetuned.json`, `laya_stress.json`, `eval.json`)

On the 170 labelled requests (`ai/requests.jsonl`); counts first, percentages after:

| | intent correct | full slice correct | patient-specific redirected | CPU latency per request |
|---|---|---|---|---|
| rules in the browser | 170 of 170 | 170 of 170 | 36 of 36 | under 1 ms |
| Laya zero-shot | 31 of 170 (18 %) | 36 of 170 (21 %) | 9 of 36 | about 650 ms |
| Laya first fine-tune (367 hand-written rows, 4 min, one GPU; `model_v1/`) | 163 of 170 (95.9 %) | 157 of 170 (92.4 %) | 33 of 36 | about 620 ms |
| Laya final (regime v2: 4 562 rows, 4 epochs, 23 min, one GPU; `model/`) | 169 of 170 (99.4 %) | 168 of 170 (98.8 %) | 35 of 36 | about 640 ms idle, p95 770 ms |
| rules + Laya final (the endpoint) | 170 of 170 | 169 of 170 (99.4 %) | 36 of 36 | about 640 ms |

Held-out 41 of the 170 (never trained on, nor anything derived from them): intent 40 of 41, slice
39 of 41, ECE of P(patient-specific) 0.024. The 60 stress requests (`ai/requests_stress.jsonl`,
written after training, never used in it): intent 57 of 60, slice 49 of 60, redirected 10 of 12,
false redirects 0 of 48; the first fine-tune scored 53, 45, 9 and 2 on the same set. The two
patient-specific misses ask for ”all patients' records for a thesis”, which the rules call
off-topic, so neither side answers them. The slice misses are mostly the ward field on bare
keyword queries and the period on ”this year” and ”last year” phrasings.

### The sweep that chose the final model (six runs in parallel, one GPU each)

All on `--split aug`, micro-batch 64 unless stated; scored on the stress set (intent / slice /
redirected of 12 / false redirects) and on all 170 (intent / slice):

| run | regime | stress | all 170 |
|---|---|---|---|
| A | 3 epochs | 54 / 47 / 9 / 1 | 167 / 166 |
| B | 5 epochs | 56 / 49 / 10 / 1 | 169 / 169 |
| C | 4 epochs, micro-batch 32 x 2, encoder lr 4e-5 | 57 / 50 / 9 / 0 | 169 / 166 |
| D | 4 epochs, option shuffling (in Docker) | 57 / 48 / 10 / 0 | 169 / 168 |
| **E** | **4 epochs, label smoothing 0.05** | **57 / 49 / 10 / 0** | **169 / 168** |
| F | 6 epochs, seed 7 | 58 / 46 / 10 / 0 | 169 / 166 |

E was installed as `model/` for the best balance on the never-trained set with no false
redirects; B is the strongest on the labelled set alone. The other checkpoints stay in
`model_A/` to `model_F/` outside the repository.

## Training regime v2: more data, same recipe

`augment.py` grows the training set from 470 to about 4 500 labelled records, all synthetic and
all labelled by construction: a second, larger set of templates (colloquial frames, inflections,
bare keyword queries, two-year comparisons), surface noise (missing diacritics, a swapped or
dropped letter, lowercase, stray punctuation) on half the records, and about four paraphrases of
every base record written by the local llama.cpp model under a strict JSON schema, kept only when
the rule router does not contradict the label on any field (116 of 1 876 were rejected). A
derived record inherits its source's holdout decision, so nothing derived from a held-out request
is trained on. `ai/requests_stress.jsonl` (60 hand-written requests: slang, typos, indirect
patient questions, mixed phrasing) is never used in training and is the honest yardstick between
regimes: `eval_laya.py --requests ai/requests_stress.jsonl`.

## Running it

```bash
uv sync --group ai                                                # laya, torch 2.7.1
uv run --group ai python ai/laya_finetune/eval_laya.py --device cpu --tag zero-shot
uv run python ai/laya_finetune/augment.py --templates 700 --paraphrases 4           # needs the llama.cpp server on :8081
CUDA_VISIBLE_DEVICES=6 uv run --group ai python ai/laya_finetune/train_laya.py --device cuda --split aug --epochs 3 --output-dir ai/laya_finetune/model
uv run --group ai python ai/laya_finetune/eval_laya.py --model-dir ai/laya_finetune/model --device cpu --tag finetuned
uv run --group ai python ai/evaluate.py --profile laya          # into ai/results/eval.json for the lab IT card
uv run --group ai uvicorn ai.serve:app --host 127.0.0.1 --port 8765   # the Ask box offers the "laya" profile
```

The dependency group pins torch 2.7.1, the build the shipped checkpoint was trained with. On a machine without a GPU everything runs on CPU.

## On a reviewer's laptop

Feasible. The checkpoint is about 650 MB, loads in well under a minute and needs a few GB of RAM;
one request takes well under a second on CPU (measured on this machine's CPU, see
`ai/results/laya_*.json`). The steps a reviewer needs:

1. `uv sync --group ai` (CPU torch is enough; on a laptop `uv` will pick the CPU wheel when no
   CUDA torch index is configured, or set `UV_TORCH_BACKEND=cpu`).
2. The fine-tuned checkpoint: either the `model/` folder copied next to this file, or a published
   copy on the Hugging Face Hub named in `AEI_LAYA_HUB` (the folder is too large for the repo).
3. `uv run --group ai uvicorn ai.serve:app --host 127.0.0.1 --port 8765`, then open
   the page over `python3 -m http.server -d web 8080`; the Ask box lists
   "Laya guard" among the models. No GPU, no llama.cpp needed for this path.

## Record format

Labelled record (`dataset/*.jsonl`):

```json
{"id": "r001", "lang": "fi", "source": "hand", "text": "E. coli virtsassa meidän osastolla, naiset, 4 viikkoa",
 "intent": "aggregate", "view": "clinician",
 "slice": {"organism": "Escherichia coli", "specimen": "urine", "unit": "context", "sex": "F", "onset": null, "window": "4w", "agent": null}}
```

Laya training row (`dataset/laya_*.jsonl`, one per record): `{"state": {"text": ...},
"questions": {...}, "gold": {"intent": {"probabilities": {"aggregate": 1.0}}, ...}}`; a null slice
value is the explicit option `none`; `view` is omitted off the aggregate path.

## Honesty notes

- The hand-written requests were written by the same people as the rules. Accuracy against them
  is a specification of behaviour, not an independent estimate. Templated requests are labelled by
  construction and share vocabulary with the rules, so they test robustness to phrasing only.
- Calibration is reported on the held-out split (41 hand-written requests, 6 of them
  patient-specific), a small set: indicative only.
- The texts are synthetic requests. The one identifier-shaped string in the set (`000000-000A`) is
  an impossible date, included so the guardrail's identifier pattern is exercised; nothing from
  `data/` appears here.

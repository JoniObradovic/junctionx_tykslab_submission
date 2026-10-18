# ai/: the Ask box behind the page

Models route, explain and guard; they never compute a number. The answer to a question is always
an existing cell or view of the statistic.

- **router_rules.py**: the always-on rule-based router and guardrail. Token matching over a closed
  vocabulary (organisms, specimen groups, units, strata, windows, antibiotics) gives an intent
  (aggregate, patient-specific, off-topic), a view (clinician, infection control, stewardship) and
  a slice of the cube. `web/router.js` is its JavaScript port; both read the same tables.
- **laya_guard.py**: the Laya decision model (Convai Innovations, Apache 2.0) asks nine typed
  choice questions over that vocabulary in one forward pass: intent, view and the seven slice
  fields. Each answer is a probability over a closed list; the probability of ”patient-specific”
  is the gate. Loads the fine-tuned checkpoint from `laya_finetune/model/` (or `AEI_LAYA_HUB`).
- **nl2cube.py**: schema-constrained extraction through a local llama.cpp server (an optional
  comparison profile, not shipped) and `combine()`, which merges the rules and a model reading:
  patient-specific when either side says so or the gate probability is reached; the model fills
  only the fields the rules left empty and may rescue an off-topic verdict into an aggregate one;
  the model's view counts when the rules only defaulted to the clinician view.
- **serve.py**: the FastAPI endpoint. `GET /health` lists the profiles, `POST /ask` with
  `{"text": "...", "lang": "en"}` returns the interpretation with every field's source and a
  decision trace.
- **evaluate.py**: scores the rules, a model and their combination on the labelled requests and
  writes `results/eval.json`, which the page's lab IT view shows.
- **requests.jsonl**: 170 labelled requests in Finnish and English, written by the authors of the
  rules (a specification of behaviour, not an independent accuracy estimate).
  **requests_stress.jsonl**: 60 requests written after training (slang, typos, indirect patient
  questions), never used in training.
- **laya_finetune/**: dataset construction, augmentation, training and evaluation of the decision
  model; its README has the measured results and the training regime.

## Results

On the 170 labelled requests, counts first:

| interpreter | question type correct | all search terms correct | patient-specific redirected | per question |
|---|---|---|---|---|
| rules in the browser | 170 / 170 | 170 / 170 | 36 / 36 | under 1 ms |
| Laya before fine-tuning | 31 / 170 | 36 / 170 | 9 / 36 | 0.65 s CPU |
| Laya fine-tuned (shipped) | 169 / 170 | 168 / 170 | 35 / 36 | 0.64 s CPU |
| rules + Laya (the endpoint) | 170 / 170 | 169 / 170 | 36 / 36 | 0.64 s CPU |

On the 60 never-trained stress requests the fine-tuned model reads the question type in 57 of
60 and every search term in 49 of 60, redirects 10 of 12 patient-specific requests and never
redirects an aggregate one. Held-out 41 of the 170: type 40 of 41, slice 39 of 41, calibration
error of the gate probability 0.024.

## Retraining

```bash
uv run python ai/laya_finetune/make_dataset.py         # 470 labelled records -> dataset/train.jsonl, holdout.jsonl
uv run python ai/laya_finetune/augment.py               # templates, noise, paraphrases (needs a local llama.cpp server)
uv run python ai/laya_finetune/train_laya.py --device cuda --split aug --epochs 4 --label-smoothing 0.05
uv run python ai/laya_finetune/eval_laya.py --model-dir ai/laya_finetune/model --device cpu --tag finetuned
uv run python ai/laya_finetune/eval_laya.py --model-dir ai/laya_finetune/model --device cpu --tag stress --requests ai/requests_stress.jsonl
uv run python ai/evaluate.py --profile laya              # -> results/eval.json, then build/export_web.py
```

The augmented training set (4 562 records) is committed, so retraining does not need the
language model that wrote the paraphrases. Training needs a GPU (about 25 minutes on one data-centre GPU for the
shipped checkpoint) or patience on CPU; `docker/Dockerfile.laya` builds an image for both the
endpoint and the training tools.

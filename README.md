# Cumulative antibiogram for Varha wards

A continuously refreshed, ward-scoped cumulative antibiogram with anomaly detection and an Ask
box driven by a small decision model. Built for the JunctionX Turku 2026 - TYKS Laboratories
challenge (16 to 18 October 2026) by team *Clean Code, Dirty Wibes*. Every number in the demo is computed
from a synthetic dataset; no real patient is involved anywhere.

- **Demo page**: https://joniobradovic.github.io/junctionx_tykslab_submission/?role=clinician&unit=URO&specimen=urine (opens in the ward physician's view; drop the parameters for the role selector)
- **Pitch deck**: [docs/pitch.pdf](docs/pitch.pdf)
- **Run it with the decision model**: `scripts/setup.sh` (Linux, macOS) or `scripts\setup.ps1`
  (Windows), see below

## What it does

Our starting point: most problems in healthcare software are not hard, they are confusing. Systems
are hard to navigate, hide the relevant number and automate the wrong thing. In Donald Norman's
words, good design is invisible and bad design screams, and there is no ”human error”, only bad
design. So the page shows the relevant thing first without a query, makes every number's basis
legible, and automates the recomputation and the detection but never the clinical decision.

The annual antibiogram describes last year across the whole hospital. The ward physician choosing
an empirical antibiotic needs their own ward, the last few weeks, a percentage only when the
sample is large enough, and a notice when something has changed. The page gives exactly that:

- **Opens on the patient's care unit.** The launch context (role and care unit) selects the view;
  the ward can be changed in the sidebar.
- **Honest scope.** A percentage is shown only with at least 30 isolates after the one-isolate-
  per-patient rule. Below that the period is widened (8, 13, 26, 52 weeks), then the unit group or
  the whole county is used, and the cell says what was done. Every percentage carries its n and
  a 95 % Wilson interval. Cells under 5 isolates never reach the browser.
- **Two detectors against each ward's own baseline.** A rise in resistance (last 4 weeks against
  the preceding year) and a cluster (same organism and phenotype within 14 days against the
  ward's usual count), graded watch and alert, each with a one-sentence explanation in Finnish
  and English. Single CPE, VRE or carbapenem-resistant isolates always alert. Both test events
  planted in the synthetic data are found.
- **An Ask box that only selects.** Rules in the browser read the request first; a fine-tuned
  Laya decision model (322 M parameters, CPU, about 0.6 s) reads it as nine fixed questions over
  the statistic's own vocabulary. The answer is always an existing cell or view. A patient-
  specific question is redirected and never answered; no model ever computes a number.

Five views: ward physician, infection control, stewardship, explore (every cell and every scope
decision, filterable) and lab IT (pipeline, configuration, provenance, data quality, the model
evaluation). The page is a surveillance tool on the same footing as the annual report: output
aggregated, no patient-level field in the browser. A patient-specific recommendation would be a
medical device; it is drawn apart in the architecture and not built.

## A two-minute tour

1. **Ward physician**: [urology, urine](https://joniobradovic.github.io/junctionx_tykslab_submission/?role=clinician&unit=URO&specimen=urine). Every cell carries % R, n and the 95 % interval; the
   badge says whether the figure is the ward's own 4 weeks, a widened period or the inpatient wards'
   shared figure. The year's figure stands beside it.
2. **Infection control**: [alerts](https://joniobradovic.github.io/junctionx_tykslab_submission/?role=infection#micro). The urology episode of June 2025 is
   seen twice, as a rise in ceftriaxone resistance and as an ESBL cluster, each with its evidence
   sentence; the ICU MRSA cluster of March 2026 is visible to the count-based detector only.
3. **Ask**: pick an example from the dropdown or type `ecoli virtsa uro naiset 4vk`. The page opens
   the right view on the right ward, collapses the matrix to the asked organism, and shows the
   interpretation beside the prompt with every field and its source.
4. **The guard**: ask “my patient has E. coli in urine, what should I start?”. The question is
   redirected to the physician's judgement and nothing is answered.

The lab IT view holds the pipeline, every threshold, the data-quality checks and the recorded model
evaluation.

## Three ways to run it

| mode | what answers the Ask box | how |
|---|---|---|
| Hosted demo | the rules in the browser | open https://joniobradovic.github.io/junctionx_tykslab_submission/?role=clinician&unit=URO&specimen=urine |
| Hosted demo + local model | rules + the Laya decision model on your machine | start the endpoint (below), then open the hosted page with `?ai=http://127.0.0.1:8765` |
| Fully local | rules + the Laya decision model | `scripts/setup.sh` or `scripts\setup.ps1`, then http://localhost:8080/ |

The hosted page is static and cannot run the model itself. Browsers treat `http://127.0.0.1` as a
trustworthy origin even from an https page, and the endpoint answers cross-origin requests, so
the second mode uses your local model from the hosted page; the selector in the Ask box shows
which interpreter answered.

## Quick install

One script installs everything, including `uv` when it is missing. No Python installation is
required: `uv` fetches the pinned interpreter (Python 3.13, see `.python-version`) and ignores
whatever Python the machine already has. The script ends by starting the demo (page on
http://localhost:8080/, endpoint on http://127.0.0.1:8765; Ctrl+C stops both).

Linux and macOS:

```bash
git clone https://github.com/JoniObradovic/junctionx_tykslab_submission.git
cd junctionx_tykslab_submission
scripts/setup.sh
```

Windows (PowerShell 5.1 or 7, no administrator rights needed):

```powershell
git clone https://github.com/JoniObradovic/junctionx_tykslab_submission.git
cd junctionx_tykslab_submission
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

Later starts: `scripts/serve_demo.sh` or `scripts\serve_demo.ps1`. `--no-run` / `-NoRun`
installs without starting. Without `git`, the repository's ZIP download works the same way; the model comes from the
v1.0.0 release as a plain download.

What it needs:

| | minimum | notes |
|---|---|---|
| CPU | any x86-64 or arm64 laptop, 2 cores | about 0.6 s per question; no GPU, no CUDA |
| RAM | 3 GB free | the endpoint measures about 1.9 GB resident with the model loaded |
| Disk | 2.5 GB | 1.1 GB environment (CPU torch), 650 MB checkpoint, the repository about 30 MB |
| Start-up | 10 s | the checkpoint loads once |
| Network | install only | nothing leaves the machine afterwards; the model runs offline |

Alternatives: `docker build -f docker/Dockerfile.laya -t aei-laya .` then
`docker run --rm -p 127.0.0.1:8765:8765 -v "$PWD/ai/laya_finetune/model:/app/ai/laya_finetune/model:ro" aei-laya`
runs the endpoint in a container; `AEI_LAYA_HUB=<org>/<repo>` loads the checkpoint from the
Hugging Face Hub instead of the release asset.

## Where things are

- `web/`: the page ([README](web/README.md): files, layout, launch parameters)
- `build/`: the cube and detector builders and the laboratory's rules in one file ([README](build/README.md), with a [Methods](build/README.md#methods-what-is-computed-and-why) section on what is computed and why)
- `ai/`: the router, the decision model, the endpoint and the evaluations ([README](ai/README.md); training in [ai/laya_finetune/README.md](ai/laya_finetune/README.md))
- `data/`: the synthetic dataset ([README](data/README.md))
- `docs/pitch.pdf`: the deck; `docs/sources.md`: sources and references; `docker/`: the endpoint image; `scripts/`: setup and run scripts

## License

Apache License 2.0 (see `LICENSE`), the same license as the Laya decision model the checkpoint is
derived from. The synthetic dataset was supplied for the challenge and is included for
reproducibility only.

## Sources and references

Every fact and term in the project traces to a public document: the Finnish surveillance reports
whose vocabulary the page prints (Tyks ”Antibioottiresistenssitilanne Varsinais-Suomessa”, HUSRES,
THL Finres), the CLSI M39 and EUCAST conventions behind the statistics, the public pages that
describe the Tyks and Varha systems, the regulation the design position rests on, and the models
and libraries used. The full list with links is in [docs/sources.md](docs/sources.md); the
dataset's own sources are in [data/README.md](data/README.md).

"""Laya guardrail: typed decisions over a request in one forward pass.

Laya (Convai Innovations, Apache 2.0, `pip install laya`) is a non-autoregressive decision model:
it answers typed questions (`choice`, `noul`, `score`) over a text with calibrated probabilities
and generates nothing, so it cannot express a number or a patient. Here every slice field and the
intent are `choice` questions whose options are the cube's own vocabulary; the probability of the
`patient_specific` option is the guard's gate (config.AI_GATE_P_PATIENT_SPECIFIC).

The multilingual checkpoint (mmBERT-base, 322M) handles Finnish. Zero-shot it is weak, as its
authors say; `ai/laya_finetune/train_laya.py` fine-tunes it on ai/laya_finetune/dataset/ and the
result is loaded from `model_dir`. Nothing here computes a statistic.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai import router_rules  # noqa: E402
from build import config  # noqa: E402

HUB_ID = "convaiinnovations/laya"
HUB_SUBFOLDER = "multilingual"
DEFAULT_MODEL_DIR = Path(os.environ.get("AEI_LAYA_MODEL_DIR", str(config.ROOT / "ai" / "laya_finetune" / "model")))
HUB_FINETUNED = os.environ.get("AEI_LAYA_HUB")   # a published fine-tuned checkpoint, e.g. "<org>/aei-laya-guard"
NONE = "none"   # the explicit null class of every slice question

ORGANISM_HINTS = {
    "Escherichia coli": "E. coli, kolibakteeri, ESBL E. coli",
    "Klebsiella pneumoniae": "Klebsiella, K. pneumoniae",
    "Klebsiella oxytoca": "K. oxytoca",
    "Proteus mirabilis": "Proteus",
    "Pseudomonas aeruginosa": "Pseudomonas, pseudomonaksen",
    "Staphylococcus aureus": "S. aureus, Staph aureus, stafylokokki, MRSA",
    "Staphylococcus saprophyticus": "S. saprophyticus",
    "Enterococcus faecalis": "Enterococcus, enterokokki, VRE",
    "Citrobacter freundii": "Citrobacter",
}
UNIT_HINTS = {
    "ED": "päivystys, emergency department, ER", "MED-A": "sisätaudit A, internal medicine A",
    "MED-B": "sisätaudit B, internal medicine B", "SURG": "kirurgia, surgery", "URO": "urologia, urology",
    "ICU": "teho, teho-osasto, intensive care, ICU", "GER": "geriatria, geriatrics",
    "PHC-N": "Terveysasema Pohjoinen, health centre north", "PHC-S": "Terveysasema Etelä, health centre south",
    "inpatient": "vuodeosastot, inpatient wards", "ed": "the emergency group", "primary": "terveysasemat, perusterveydenhuolto, primary care",
    "ALL": "koko Varha, koko alue, whole county, all units", "context": "our own ward: meidän osastolla, omalla osastolla, meillä, our ward, here",
}
WINDOW_HINTS = {"4w": "4 weeks, 4 viikkoa, kuukausi, last month", "8w": "8 weeks", "13w": "13 weeks, 3 months, quarter",
                "26w": "26 weeks, half a year, puoli vuotta", "52w": "52 weeks, a year, 12 months, viimeisen vuoden aikana",
                "annual": "last calendar year, viime vuonna", "current_year": "this year, tänä vuonna"}
INTENT_CRITERIA = {
    "aggregate": "a question about resistance statistics, alerts, clusters or data quality for a ward, a group of wards or the whole region; counts or shares of patients are aggregate",
    "patient_specific": "about one person's treatment: my patient, this patient, he or she, hän, an age, a weight, a dose, an allergy, pregnancy, symptoms, a lab value, a room or bed, an identifier, what should I give",
    "off_topic": "not about antibiotics, bacteria or resistance at all",
}
VIEW_CRITERIA = {"clinician": "the ward's resistance table for choosing an antibiotic",
                 "micro": "alerts, clusters, outbreaks, ESBL, MRSA, VRE, CPE, data quality, EUCAST",
                 "steward": "calendar years, annual trends, comparisons between years"}


def questions(tab: dict | None = None) -> dict:
    """The typed questions: intent, view and one choice per slice field, options = the vocabulary."""
    tab = tab or router_rules.tables()
    v = tab["vocab"]
    agents_fi = {}
    with open(config.DATA_DIR / "antibiotics.csv", encoding="utf-8", newline="") as f:
        import csv
        for row in csv.DictReader(f):
            agents_fi[row["antibiotic_code"]] = f'{row["antibiotic_fi"]}, {row["antibiotic"]}'
    with_none = lambda d, none_text: dict(d, **{NONE: none_text})
    return {
        "intent": {"type": "choice", "instructions": "What kind of request is this?", "criteria": INTENT_CRITERIA},
        "view": {"type": "choice", "instructions": "Which view answers an aggregate question?", "criteria": VIEW_CRITERIA},
        "organism": {"type": "choice", "instructions": "Which organism is named?",
                     "criteria": with_none({o: ORGANISM_HINTS.get(o, "") for o in v["organisms"]}, "no organism named")},
        "specimen": {"type": "choice", "instructions": "Which specimen type is named?",
                     "criteria": with_none({"urine": "virtsa, urine, UTI, VTI, cystitis, pyelonephritis", "wound": "haava, wound",
                                            "blood": "veri, veriviljely, blood, sepsis, bacteraemia"}, "no specimen named")},
        "unit": {"type": "choice", "instructions": "Which ward, ward group or region is named?",
                 "criteria": with_none({u: UNIT_HINTS.get(u, "") for u in list(v["units"]) + list(v["groups"]) + [tab["all_code"], "context"]}, "no unit named")},
        "sex": {"type": "choice", "instructions": "Is a sex named?",
                "criteria": with_none({"F": "naiset, women, female", "M": "miehet, men, male"}, "none or both")},
        "onset": {"type": "choice", "instructions": "Is hospital- or community-onset named?",
                  "criteria": with_none({"hospital": "sairaalasyntyinen, hospital-onset, nosocomial", "community": "avohoitosyntyinen, community-onset"}, "not named")},
        "window": {"type": "choice", "instructions": "Which period is named?",
                   "criteria": with_none({w: WINDOW_HINTS.get(w, w) for w in router_rules.schema_windows()}, "no period named")},
        "agent": {"type": "choice", "instructions": "Which antibiotic is named?",
                  "criteria": with_none({a: agents_fi.get(a, a) for a in v["agents"]}, "no antibiotic named")},
    }


def gold_for(record: dict) -> dict:
    """Gold answers for one labelled record, in the questions' option keys."""
    s = record["slice"]
    return {"intent": record["intent"], "view": record["view"] or "clinician",
            **{k: (s[k] if s[k] is not None else NONE) for k in router_rules.SLICE_FIELDS}}


def training_rows(records: list[dict], qs: dict | None = None) -> list[dict]:
    """Rows for laya.train.finetune: {state, questions, gold} per labelled request."""
    qs = qs or questions()
    rows = []
    for r in records:
        gold = gold_for(r)
        if r["intent"] != "aggregate":
            gold = {k: v for k, v in gold.items() if k != "view"}   # view is undefined off the aggregate path
        # laya.train reads gold as a probability over the option keys; a label becomes a one-hot.
        rows.append({"state": {"text": r["text"]}, "questions": {k: qs[k] for k in gold},
                     "gold": {k: {"probabilities": {v: 1.0}} for k, v in gold.items()}})
    return rows


class LayaGuard:
    """Loads a checkpoint once and answers the typed questions for one request."""

    def __init__(self, model_dir: str | Path | None = None, device: str | None = None):
        import laya  # heavy import kept local: torch
        self.questions = questions()
        path = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
        if path.exists() and (path / "model.safetensors").exists():
            self.agent = laya.load(str(path), device=device) if device else laya.load(str(path))
            self.model = f"laya fine-tuned ({path.name})"
        elif HUB_FINETUNED:
            self.agent = laya.load(HUB_FINETUNED, device=device) if device else laya.load(HUB_FINETUNED)
            self.model = f"laya fine-tuned ({HUB_FINETUNED})"
        else:
            self.agent = laya.load(HUB_ID, subfolder=HUB_SUBFOLDER, device=device) if device else laya.load(HUB_ID, subfolder=HUB_SUBFOLDER)
            self.model = f"{HUB_ID}/{HUB_SUBFOLDER} (zero-shot)"

    def predict(self, text: str) -> dict:
        """Same shape as nl2cube.extract so nl2cube.combine can take it: intent, view, slice, p_intent."""
        t0 = time.perf_counter()
        answers = self.agent.predict({"text": text or ""}, self.questions)["answers"]
        latency = (time.perf_counter() - t0) * 1000
        pick = lambda k: (None if answers[k]["choice"] == NONE else answers[k]["choice"])
        intent = answers["intent"]["choice"]
        slice_ = {k: pick(k) for k in router_rules.SLICE_FIELDS}
        return {"ok": True, "intent": intent, "view": answers["view"]["choice"] if intent == "aggregate" else None,
                "slice": slice_, "p_intent": {k: round(float(p), 4) for k, p in answers["intent"]["probabilities"].items()},
                "latency_ms": latency, "model": self.model, "profile": "laya",
                "confidence": {k: round(float(a.get("answer_confidence", 0)), 4) for k, a in answers.items()}}

    def predict_many(self, texts: list[str], batch_size: int = 32) -> list[dict]:
        t0 = time.perf_counter()
        results = self.agent.predict_batch([{"text": t or ""} for t in texts], self.questions, batch_size=batch_size)
        per = (time.perf_counter() - t0) * 1000 / max(len(texts), 1)
        out = []
        for res in results:
            answers = res["answers"]
            pick = lambda k: (None if answers[k]["choice"] == NONE else answers[k]["choice"])
            intent = answers["intent"]["choice"]
            out.append({"ok": True, "intent": intent, "view": answers["view"]["choice"] if intent == "aggregate" else None,
                        "slice": {k: pick(k) for k in router_rules.SLICE_FIELDS},
                        "p_intent": {k: round(float(p), 4) for k, p in answers["intent"]["probabilities"].items()},
                        "latency_ms": per, "model": self.model, "profile": "laya"})
        return out


if __name__ == "__main__":   # manual check: uv run --group laya python ai/laya_guard.py "text"
    import json
    g = LayaGuard()
    for arg in sys.argv[1:]:
        print(json.dumps(g.predict(arg), ensure_ascii=False))

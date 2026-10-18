"""Grow the Laya training set: richer templates, surface noise, and label-preserving paraphrases
from the local language model.

Three sources, all labelled by construction and all synthetic:
  1. the base dataset of make_dataset.py (hand-written plus the first templates);
  2. more templates: colloquial frames, inflections, mixed Finnish and English, bare keyword
     queries, questions with typos and missing diacritics, requests that name two things in
     odd orders;
  3. paraphrases of every base record written by the local llama.cpp model, asked to keep every
     fact (organism, specimen, unit, sex, onset, period, agent, and whether it is about one
     patient) and change only the wording. A paraphrase is kept only when the rule router
     agrees with the label on every field the rules can see (a rule that finds nothing is fine,
     a rule that finds something else is a rejection), so no wrong label enters training.

The split stays the one of make_dataset.py: a paraphrase or noisy copy inherits its source id's
holdout decision, so nothing derived from a held-out request is ever trained on. The stress set
ai/requests_stress.jsonl is never used here.

    uv run python ai/laya_finetune/augment.py --paraphrases 4      # needs the llama.cpp server
    uv run python ai/laya_finetune/augment.py --no-llm              # templates and noise only
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ai import nl2cube, router_rules  # noqa: E402
from ai.laya_finetune import make_dataset  # noqa: E402

DATASET = Path(__file__).resolve().parent / "dataset"
SEED = 2027
M = make_dataset   # phrase tables live there

# --- more frames ---------------------------------------------------------------------------
AGG_FRAMES_2 = {
    "fi": ["{org} {unit} {spec}", "mikä on {org} {agent}-resistenssi {spec} {unit} {win}", "resistenssi {unit}: {org}, {agent}",
           "{spec} {org} {sex} {win}", "paljonko {org} on resistentti {agent} {unit}?", "miten {agent} toimii {org} {spec} {unit}?",
           "näytä {unit} herkkyystilanne {win}", "{org} {agent} {win}", "onko {org} {agent}-resistenssi noussut {unit}?",
           "{unit} {org} {spec}: {agent}, {sex}", "herkkyydet {org} {unit}", "mikä on tilanne {org} {spec} {unit} {win}",
           "{org}, {spec}, {unit}, {win}, {sex}", "kuinka suuri osa {org} kannoista on {agent}-resistenttejä {unit}?",
           "{agent} ja {org}: {unit} {win}", "paljonko kantoja {org} {spec} {unit} {win}?"],
    "en": ["{org} {unit} {spec}", "what is the {agent} resistance of {org} {spec} {unit} {win}", "resistance {unit}: {org}, {agent}",
           "{spec} {org} {sex} {win}", "how resistant is {org} to {agent} {unit}?", "does {agent} work for {org} {spec} {unit}?",
           "show the susceptibility picture {unit} {win}", "{org} {agent} {win}", "has {org} {agent} resistance risen {unit}?",
           "{unit} {org} {spec}: {agent}, {sex}", "susceptibilities {org} {unit}", "what is the situation for {org} {spec} {unit} {win}",
           "{org}, {spec}, {unit}, {win}, {sex}", "what share of {org} isolates are {agent} resistant {unit}?",
           "{agent} and {org}: {unit} {win}", "how many {org} isolates {spec} {unit} {win}?"],
}
MICRO_FRAMES_2 = {"fi": ["rypäät {unit} {win}", "onko {unit} poikkeamia?", "hälytykset {win}", "ESBL {unit}", "mikä hälyttää {unit}?", "epidemiaepäilyt {win} {unit}",
                         "tiedon laatu {unit}", "näytä {unit} hälytyshistoria", "MRSA-rypäs {unit}?", "VRE {unit} {win}"],
                  "en": ["clusters {unit} {win}", "any anomalies {unit}?", "alerts {win}", "ESBL {unit}", "what is alerting {unit}?", "outbreak suspicions {win} {unit}",
                         "data quality {unit}", "show the alert history {unit}", "MRSA cluster {unit}?", "VRE {unit} {win}"]}
STEWARD_FRAMES_2 = {"fi": ["{org} {agent} vuonna {year}", "vuosivertailu {org} {spec} {unit}", "trendi {agent} {org}", "miten {org} {agent} on kehittynyt vuosina {year} ja {year2}?",
                           "{unit} {org} vuositasolla", "kehityssuunta {org} {spec}"],
                    "en": ["{org} {agent} in {year}", "year comparison {org} {spec} {unit}", "trend {agent} {org}", "how has {org} {agent} developed in {year} and {year2}?",
                           "{unit} {org} by year", "trend {org} {spec}"]}
PATIENT_FRAMES_2 = {
    "fi": ["potilaani sai {org} {spec}, mitä annan?", "{age}v {sexword}, {spec} {org}, lääke?", "hänellä on {org} {spec} ja allergia penisilliinille, mitä nyt?",
           "mikä annostus {agent} {kg} kg?", "potilaalle {agent} vai jotain muuta, kun {org} {spec}?", "voinko aloittaa {agent} raskaana olevalle?",
           "huoneen {room} potilaalla {org} {spec}, mitä hoidoksi?", "lapselle {agent}? {org} {spec}", "minulla on {spec} oireita, mitä otan?",
           "potilaan kreatiniini {crp}, sopiiko {agent}?", "kuumeinen potilas, {org} {spec}, aloitanko {agent}?", "näytä potilaan {hetu} tulokset",
           "mitä määrään tälle potilaalle {org} {spec}?", "{age}-vuotias {sexword} {unit}, {org}, hoito?"],
    "en": ["my patient grew {org} {spec}, what do I give?", "{age}yo {sexword}, {spec} {org}, drug?", "she has {org} {spec} and a penicillin allergy, what now?",
           "what dose of {agent} at {kg} kg?", "{agent} or something else for the patient with {org} {spec}?", "can I start {agent} in pregnancy?",
           "the patient in room {room} has {org} {spec}, treatment?", "{agent} for a child? {org} {spec}", "I have {spec} symptoms, what should I take?",
           "patient creatinine {crp}, is {agent} ok?", "febrile patient, {org} {spec}, do I start {agent}?", "show results of patient {hetu}",
           "what do I prescribe for this patient with {org} {spec}?", "{age} year old {sexword} {unit}, {org}, treatment?"],
}
OFF_TOPIC_2 = {"fi": ["montako kahvia on jäljellä", "milloin kokous alkaa", "varaa auto huomiseksi", "mikä on wifi-salasana", "kerro vitsi", "paljonko on 2+2",
                      "soita hoitajalle", "missä on ruokala", "mikä päivä tänään on", "tilaa lisää käsineitä", "miten tulostan", "kiitos ja hyvää päivänjatkoa"],
               "en": ["how much coffee is left", "when does the meeting start", "book a car for tomorrow", "what is the wifi password", "tell me a joke", "what is 2+2",
                      "call the nurse", "where is the canteen", "what day is it", "order more gloves", "how do I print", "thanks and have a nice day"]}
EXTRA_UNIT_PHRASES = {"ED": {"fi": ["ensiavussa", "päivystyksen"], "en": ["in the ER", "at the emergency dept"]}, "ICU": {"fi": ["tehon", "tehohoidossa"], "en": ["ICU", "intensive care"]},
                      "URO": {"fi": ["urologian", "urologian osastolla"], "en": ["urology ward", "in urology"]}, "SURG": {"fi": ["kirurgian", "kirurgisella osastolla"], "en": ["surgical ward"]},
                      "GER": {"fi": ["geriatrian osastolla"], "en": ["geriatric ward"]}, "context": {"fi": ["täällä", "meidän yksikössä", "omassa yksikössä"], "en": ["our unit", "my unit", "this ward"]},
                      "ALL": {"fi": ["Varhassa", "koko hyvinvointialueella", "kaikissa yksiköissä"], "en": ["Varha-wide", "whole region", "all wards"]},
                      "inpatient": {"fi": ["osastoilla", "sairaalan osastoilla"], "en": ["hospital wards", "inpatients"]}, "primary": {"fi": ["terveyskeskuksissa"], "en": ["health centres"]}}
YEARS = ("2024", "2025")


def _phr(rng, table, key, lang, extra=None):
    opts = list(table[key][lang]) + (list(extra.get(key, {}).get(lang, [])) if extra else [])
    return rng.choice(opts)


def noisy(text: str, rng: random.Random) -> str:
    """Surface noise: missing diacritics, a dropped or swapped letter, lowercase, trailing punctuation."""
    out = text
    r = rng.random()
    if r < 0.3:
        out = out.replace("ä", "a").replace("ö", "o").replace("Ä", "A").replace("Ö", "O")
    elif r < 0.5 and len(out) > 12:
        i = rng.randrange(3, len(out) - 3)
        if out[i].isalpha() and out[i + 1].isalpha():
            out = out[:i] + out[i + 1] + out[i] + out[i + 2:]
    elif r < 0.65:
        out = out.lower()
    elif r < 0.8 and len(out) > 12:
        i = rng.randrange(3, len(out) - 3)
        if out[i].isalpha() and out[i - 1].isalpha() and out[i + 1].isalpha():
            out = out[:i] + out[i + 1:]
    if rng.random() < 0.3:
        out = out.rstrip("?.!") + rng.choice(["", "?", "??", ".", " ?"])
    return out


def templated_2(n_per_lang: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for lang in ("fi", "en"):
        for i in range(n_per_lang):
            kind = rng.choices(["agg", "micro", "steward", "patient", "off"], weights=[6, 2, 2, 4, 1])[0]
            s = M.empty_slice()
            rid = f"u{lang}{i:04d}"
            if kind == "off":
                out.append({"id": rid, "lang": lang, "source": "template2", "text": rng.choice(OFF_TOPIC_2[lang]), "intent": "off_topic", "view": None, "slice": s})
                continue
            org = rng.choice(list(M.ORGANISM_PHRASES)); spec = rng.choice(list(M.SPECIMEN_PHRASES)); unit = rng.choice(list(M.UNIT_PHRASES))
            win = rng.choice(list(M.WINDOW_PHRASES)); agent = rng.choice(list(M.AGENT_PHRASES)); sex = rng.choice(list(M.SEX_PHRASES))
            year, year2 = rng.sample(YEARS, 2)
            fill = {"org": _phr(rng, M.ORGANISM_PHRASES, org, lang), "spec": _phr(rng, M.SPECIMEN_PHRASES, spec, lang),
                    "unit": _phr(rng, M.UNIT_PHRASES, unit, lang, EXTRA_UNIT_PHRASES), "win": _phr(rng, M.WINDOW_PHRASES, win, lang),
                    "agent": _phr(rng, M.AGENT_PHRASES, agent, lang), "sex": _phr(rng, M.SEX_PHRASES, sex, lang),
                    "year": year, "year2": year2, "age": rng.choice([3, 27, 45, 68, 79, 88]), "kg": rng.choice([55, 70, 90, 110]),
                    "room": rng.choice([2, 7, 14]), "crp": rng.choice([140, 210, 95]), "hetu": "000000-000A", "sexword": M.SEXWORD[lang][sex]}
            if kind == "patient":
                frame = rng.choice(PATIENT_FRAMES_2[lang])
                s.update(organism=org if "{org}" in frame else None, specimen=spec if "{spec}" in frame else None,
                         agent=agent if "{agent}" in frame else None, sex=sex if "{sexword}" in frame else None,
                         unit=unit if "{unit}" in frame else None)
                out.append({"id": rid, "lang": lang, "source": "template2", "text": frame.format(**fill), "intent": "patient_specific", "view": None, "slice": s})
                continue
            frames = {"agg": AGG_FRAMES_2, "micro": MICRO_FRAMES_2, "steward": STEWARD_FRAMES_2}[kind][lang]
            frame = rng.choice(frames)
            text = " ".join(frame.format(**fill).split())
            s.update(organism=org if "{org}" in frame else None, specimen=spec if "{spec}" in frame else None,
                     unit=unit if "{unit}" in frame else None, window=win if "{win}" in frame else None,
                     agent=agent if "{agent}" in frame else None, sex=sex if "{sex}" in frame else None)
            view = {"agg": "clinician", "micro": "micro", "steward": "steward"}[kind]
            if kind == "steward":
                if "{year}" in frame:
                    s["window"] = "Y" + (max(year, year2) if "{year2}" in frame else year)
            if kind == "agg" and s["window"] in ("Y2024", "Y2025"):
                view = "steward"
            if kind == "micro":
                s["organism"] = "Staphylococcus aureus" if "MRSA" in frame else ("Enterococcus faecalis" if "VRE" in frame else None)
                s["specimen"] = None
            out.append({"id": rid, "lang": lang, "source": "template2", "text": text, "intent": "aggregate", "view": view, "slice": s})
    return out


def with_noise(records: list[dict], seed: int, fraction: float = 0.5) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for r in records:
        if rng.random() < fraction and r["text"]:
            out.append(dict(r, id=r["id"] + "-n", source=r["source"] + "+noise", text=noisy(r["text"], rng)))
    return out


# --- paraphrases from the local language model -----------------------------------------------
PARA_SCHEMA = {"type": "object", "properties": {"paraphrases": {"type": "array", "items": {"type": "string", "maxLength": 240}, "minItems": 1, "maxItems": 8}},
               "required": ["paraphrases"], "additionalProperties": False}
PARA_SYSTEM = ("You rewrite short requests typed into a hospital antibiotic-resistance statistics tool. Write natural paraphrases a "
               "clinician, a microbiologist or a pharmacist might type: colloquial, terse or full sentences, with the same language as the "
               "original (Finnish stays Finnish, English stays English). Keep every fact exactly: the organism, the specimen type, the ward or "
               "region, the sex, hospital/community onset, the time period and the antibiotic named, and whether the request is about ONE "
               "patient (then keep it about one patient) or about statistics (then never make it about a patient). Do not add facts, do not "
               "drop any, do not answer the request. Return JSON only.")


def paraphrase(text: str, lang: str, n: int, url: str) -> list[str]:
    body = {"model": "router", "temperature": 0.9, "top_p": 0.95, "max_tokens": 600,
            "messages": [{"role": "system", "content": PARA_SYSTEM},
                         {"role": "user", "content": f"Language: {'Finnish' if lang == 'fi' else 'English'}. Write {n} paraphrases of: {text}"}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "paraphrases", "schema": PARA_SCHEMA}}}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        payload = json.load(resp)
    try:
        return [p.strip() for p in json.loads(payload["choices"][0]["message"]["content"])["paraphrases"] if p.strip()]
    except (KeyError, json.JSONDecodeError, TypeError):
        return []


def consistent(record: dict, text: str, tab: dict) -> bool:
    """Keep a paraphrase only when the rules do not contradict the label."""
    out = router_rules.route(text, record["lang"], tab)
    if record["intent"] == "patient_specific" and out["intent"] == "off_topic":
        return False
    if record["intent"] != "patient_specific" and out["intent"] == "patient_specific":
        return False
    if record["intent"] == "aggregate" and out["intent"] == "off_topic":
        return False
    for k, v in record["slice"].items():
        got = out["slice"][k]
        if got is not None and got != v:
            return False
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--templates", type=int, default=600, help="template2 records per language")
    ap.add_argument("--paraphrases", type=int, default=4, help="paraphrases per base record (0 to skip)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--llm-url", default=nl2cube.LLM_URL)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    tab = router_rules.tables()
    base = [json.loads(l) for name in ("train", "holdout") for l in (DATASET / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    extra = templated_2(args.templates, args.seed)
    noise = with_noise(base + extra, args.seed + 1)
    paras, tried, rejected = [], 0, 0
    if not args.no_llm and args.paraphrases > 0 and nl2cube.health(args.llm_url)["ok"]:
        t0 = time.perf_counter()
        for i, r in enumerate(base):
            if not r["text"]:
                continue
            try:
                cands = paraphrase(r["text"], r["lang"], args.paraphrases, args.llm_url)
            except (OSError, TimeoutError) as err:
                print("paraphrase call failed:", type(err).__name__); cands = []
            for j, c in enumerate(cands):
                tried += 1
                if c.lower() == r["text"].lower() or not consistent(r, c, tab):
                    rejected += 1
                    continue
                paras.append(dict(r, id=f'{r["id"]}-p{j}', source="paraphrase", text=c))
            if (i + 1) % 50 == 0:
                print(f"{i + 1}/{len(base)} paraphrased, kept {len(paras)}, rejected {rejected}, {time.perf_counter() - t0:.0f} s", flush=True)
    else:
        print("paraphrases skipped (no server or --no-llm)")
    rows = base + extra + noise + paras
    train = [r for r in rows if not M.is_holdout(r["id"].split("-")[0])]
    hold = [r for r in rows if M.is_holdout(r["id"].split("-")[0])]
    (DATASET / "train_aug.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in train), encoding="utf-8")
    (DATASET / "holdout_aug.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in hold), encoding="utf-8")
    by = {}
    for r in rows:
        by[r["source"]] = by.get(r["source"], 0) + 1
    print(f"records {len(rows)}: {by}; train {len(train)}, holdout {len(hold)}; paraphrases tried {tried}, rejected {rejected}")


if __name__ == "__main__":
    main()

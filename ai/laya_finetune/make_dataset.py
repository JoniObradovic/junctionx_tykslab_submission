"""Build the labelled FI/EN request dataset for the Laya guardrail.

Two sources, both synthetic and free of any patient data:
  1. ai/requests.jsonl: hand-written requests with full labels (the specification the rule
     router is tested against);
  2. templated requests generated here from the cube's vocabulary (organism x specimen x unit x
     window phrasings, patient-specific frames, off-topic frames), labelled by construction.

Every record carries the Laya target heads: intent (choice), view (choice or null) and the seven
slice fields (choice or null). The split is deterministic by id (one in five held out), so a
fine-tune and ai/evaluate.py agree on which requests are unseen.

    uv run python ai/laya_finetune/make_dataset.py        # writes ai/laya_finetune/dataset/
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from ai import router_rules  # noqa: E402
from build import config  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "dataset"
HAND = config.ROOT / "ai" / "requests.jsonl"
HOLDOUT_EVERY = 5
SEED = 2026

ORGANISM_PHRASES = {
    "Escherichia coli": {"fi": ["E. coli", "E. colin", "kolibakteerin", "Escherichia coli"], "en": ["E. coli", "Escherichia coli", "E coli"]},
    "Klebsiella pneumoniae": {"fi": ["Klebsiella pneumoniae", "Klebsiellan"], "en": ["Klebsiella pneumoniae", "Klebsiella"]},
    "Klebsiella oxytoca": {"fi": ["Klebsiella oxytoca"], "en": ["Klebsiella oxytoca", "K. oxytoca"]},
    "Proteus mirabilis": {"fi": ["Proteus mirabilis", "Proteuksen"], "en": ["Proteus mirabilis", "Proteus"]},
    "Pseudomonas aeruginosa": {"fi": ["Pseudomonas aeruginosa", "pseudomonaksen"], "en": ["Pseudomonas aeruginosa", "Pseudomonas"]},
    "Staphylococcus aureus": {"fi": ["S. aureus", "Staphylococcus aureus", "stafylokokki aureus"], "en": ["S. aureus", "Staph aureus", "Staphylococcus aureus"]},
    "Staphylococcus saprophyticus": {"fi": ["S. saprophyticus"], "en": ["S. saprophyticus"]},
    "Enterococcus faecalis": {"fi": ["Enterococcus faecalis", "enterokokin"], "en": ["Enterococcus faecalis", "Enterococcus"]},
    "Citrobacter freundii": {"fi": ["Citrobacter freundii"], "en": ["Citrobacter freundii", "Citrobacter"]},
}
SPECIMEN_PHRASES = {"urine": {"fi": ["virtsassa", "virtsanäytteissä", "virtsatieinfektioissa"], "en": ["in urine", "urine", "in UTIs"]},
                    "wound": {"fi": ["haavoissa", "haavanäytteissä"], "en": ["in wounds", "wound isolates"]},
                    "blood": {"fi": ["veressä", "veriviljelyissä", "sepsiksessä"], "en": ["in blood", "blood cultures", "bacteraemia"]}}
UNIT_PHRASES = {"ED": {"fi": ["päivystyksessä"], "en": ["in the emergency department", "ED"]},
                "MED-A": {"fi": ["sisätaudit A:lla", "Sisätaudit A"], "en": ["internal medicine A", "MED-A"]},
                "MED-B": {"fi": ["sisätaudit B:llä", "Sisätaudit B"], "en": ["internal medicine B", "MED-B"]},
                "SURG": {"fi": ["kirurgialla"], "en": ["in surgery", "SURG"]},
                "URO": {"fi": ["urologialla"], "en": ["in urology", "URO"]},
                "ICU": {"fi": ["teholla", "teho-osastolla"], "en": ["in the ICU", "in intensive care"]},
                "GER": {"fi": ["geriatrialla"], "en": ["in geriatrics", "GER"]},
                "PHC-N": {"fi": ["Terveysasema Pohjoisessa"], "en": ["at health centre north"]},
                "PHC-S": {"fi": ["Terveysasema Etelässä"], "en": ["at health centre south"]},
                "inpatient": {"fi": ["vuodeosastoilla"], "en": ["on the inpatient wards"]},
                "primary": {"fi": ["terveysasemilla", "perusterveydenhuollossa"], "en": ["in primary care"]},
                "ALL": {"fi": ["koko Varhassa", "koko alueella"], "en": ["across the whole county", "in all units"]},
                "context": {"fi": ["meidän osastolla", "omalla osastolla", "meillä"], "en": ["on our ward", "here", "on my ward"]}}
WINDOW_PHRASES = {"4w": {"fi": ["4 viikkoa", "viimeisen kuukauden aikana", "4 vk"], "en": ["last 4 weeks", "in the last month"]},
                  "8w": {"fi": ["8 viikkoa"], "en": ["last 8 weeks"]},
                  "13w": {"fi": ["13 viikkoa", "3 kk"], "en": ["last 13 weeks", "3 months"]},
                  "26w": {"fi": ["puolen vuoden ajalta", "26 viikkoa"], "en": ["half a year", "26 weeks"]},
                  "52w": {"fi": ["viimeisen vuoden aikana", "52 vk"], "en": ["over the last year", "12 months"]},
                  "Y2025": {"fi": ["vuonna 2025"], "en": ["in 2025"]},
                  "Y2024": {"fi": ["vuonna 2024"], "en": ["in 2024"]}}
AGENT_PHRASES = {"CIP": {"fi": ["siprofloksasiini"], "en": ["ciprofloxacin"]}, "CRO": {"fi": ["keftriaksoni"], "en": ["ceftriaxone"]},
                 "TMP": {"fi": ["trimetopriimi"], "en": ["trimethoprim"]}, "NIT": {"fi": ["nitrofurantoiini"], "en": ["nitrofurantoin"]},
                 "MEC": {"fi": ["mesillinaami"], "en": ["mecillinam"]}, "CXM": {"fi": ["kefuroksiimi"], "en": ["cefuroxime"]},
                 "MEM": {"fi": ["meropeneemi"], "en": ["meropenem"]}, "AMP": {"fi": ["ampisilliini"], "en": ["ampicillin"]},
                 "VAN": {"fi": ["vankomysiini"], "en": ["vancomycin"]}, "CLI": {"fi": ["klindamysiini"], "en": ["clindamycin"]},
                 "CAZ": {"fi": ["keftatsidiimi"], "en": ["ceftazidime"]}, "TZP": {"fi": ["piperasilliini-tatsobaktaami"], "en": ["piperacillin-tazobactam"]}}
SEX_PHRASES = {"F": {"fi": ["naiset", "naisilla"], "en": ["women"]}, "M": {"fi": ["miehet", "miehillä"], "en": ["men"]}}

AGG_FRAMES = {"fi": ["{org} {spec} {unit}, {win}", "Mikä on {org} {agent}resistenssi {unit}?", "{org} {spec} {unit} {sex}",
                     "Näytä {org} {spec} {win}", "{agent} ja {org} {spec} {unit}", "Resistenssitilanne {unit} {win}",
                     "Paljonko {org} -kantoja on testattu {unit} {win}?"],
              "en": ["{org} {spec} {unit}, {win}", "What is the {agent} resistance of {org} {unit}?", "{org} {spec} {unit} {sex}",
                     "Show {org} {spec} {win}", "{agent} and {org} {spec} {unit}", "Resistance overview {unit} {win}",
                     "How many {org} isolates were tested {unit} {win}?"]}
MICRO_FRAMES = {"fi": ["Onko {unit} hälytyksiä {win}?", "ESBL-rypäät {unit}", "MRSA-tilanne {unit} {win}", "Näytä hälytykset {unit}"],
                "en": ["Any alerts {unit} {win}?", "ESBL clusters {unit}", "MRSA situation {unit} {win}", "Show the alerts {unit}"]}
STEWARD_FRAMES = {"fi": ["{org} {agent} {spec} vuosittain", "Resistenssin kehitys {unit}: {org}", "Vertaa 2024 ja 2025: {org} {agent}"],
                  "en": ["{org} {agent} {spec} annual trend", "Resistance trend {unit}: {org}", "Compare 2024 and 2025: {org} {agent}"]}
PATIENT_FRAMES = {"fi": ["Potilaallani on {org} {spec}, mitä antibioottia aloitan?", "{age}-vuotias {sexword}, {spec} {org}, mitä annan?",
                         "Mikä annos {agent}a {kg} kg potilaalle?", "Potilas on allerginen penisilliinille, mitä vaihtoehtoja {org} -infektioon?",
                         "Hänellä on {org} {spec}, miten hoidan?", "Sopiiko {agent} potilaalle, jonka kreatiniini on koholla?",
                         "Mitä antibioottia määrään tälle potilaalle, {org} {spec}?"],
                  "en": ["My patient has {org} {spec}, which antibiotic should I start?", "{age}-year-old {sexword} with {org} {spec}, what do I give?",
                         "What dose of {agent} for an {kg} kg patient?", "The patient is allergic to penicillin, alternatives for {org}?",
                         "She has {org} {spec}, how do I treat her?", "Is {agent} ok for a patient with raised creatinine?",
                         "Which antibiotic should I prescribe for this patient, {org} {spec}?"]}
OFF_TOPIC = {"fi": ["Mikä on tämän päivän lounas?", "Paljonko kello on?", "Kirjoita runo kesästä", "Missä on lähin parkkipaikka?",
                    "Milloin seuraava bussi lähtee?", "Käännä tämä englanniksi", "Mikä on Suomen pääkaupunki?", "Sääennuste huomiselle",
                    "Varaa minulle kokoushuone", "Miten vaihdan salasanan?", "Soita äidille", "Mikä on paras pizzeria Turussa?"],
             "en": ["What's for lunch today?", "Tell me a joke", "What is the capital of Finland?", "Weather forecast for tomorrow",
                    "Translate good morning into Finnish", "Who won the hockey game?", "How do I reset my password?", "Book me a meeting room",
                    "Call my mother", "What is the best pizzeria in Turku?", "Play some music", "Set a timer for ten minutes"]}
SEXWORD = {"fi": {"F": "nainen", "M": "mies"}, "en": {"F": "woman", "M": "man"}}


def is_holdout(record_id: str) -> bool:
    return int(hashlib.md5(record_id.encode("utf-8")).hexdigest(), 16) % HOLDOUT_EVERY == 0


def empty_slice() -> dict:
    return {k: None for k in router_rules.SLICE_FIELDS}


def _pick(rng, table, key, lang):
    return rng.choice(table[key][lang])


def templated(n_per_lang: int = 150, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for lang in ("fi", "en"):
        for i in range(n_per_lang):
            kind = rng.choices(["agg", "micro", "steward", "patient", "off"], weights=[5, 2, 2, 4, 2])[0]
            s = empty_slice()
            rid = f"t{lang}{i:03d}"
            if kind == "off":
                out.append({"id": rid, "lang": lang, "source": "template", "text": rng.choice(OFF_TOPIC[lang]),
                            "intent": "off_topic", "view": None, "slice": s})
                continue
            org = rng.choice(list(ORGANISM_PHRASES))
            spec = rng.choice(list(SPECIMEN_PHRASES))
            unit = rng.choice(list(UNIT_PHRASES))
            win = rng.choice(list(WINDOW_PHRASES))
            agent = rng.choice(list(AGENT_PHRASES))
            sex = rng.choice(list(SEX_PHRASES))
            fill = {"org": _pick(rng, ORGANISM_PHRASES, org, lang), "spec": _pick(rng, SPECIMEN_PHRASES, spec, lang),
                    "unit": _pick(rng, UNIT_PHRASES, unit, lang), "win": _pick(rng, WINDOW_PHRASES, win, lang),
                    "agent": _pick(rng, AGENT_PHRASES, agent, lang), "sex": _pick(rng, SEX_PHRASES, sex, lang),
                    "age": rng.choice([34, 58, 72, 81]), "kg": rng.choice([60, 80, 95]), "sexword": SEXWORD[lang][sex]}
            if kind == "patient":
                frame = rng.choice(PATIENT_FRAMES[lang])
                text = frame.format(**fill)
                s.update(organism=org if "{org}" in frame else None, specimen=spec if "{spec}" in frame else None,
                         agent=agent if "{agent}" in frame else None, sex=sex if "{sexword}" in frame else None)
                out.append({"id": rid, "lang": lang, "source": "template", "text": text, "intent": "patient_specific",
                            "view": None, "slice": s})
                continue
            frames = {"agg": AGG_FRAMES, "micro": MICRO_FRAMES, "steward": STEWARD_FRAMES}[kind][lang]
            frame = rng.choice(frames)
            text = " ".join(frame.format(**fill).split())
            s.update(organism=org if "{org}" in frame else None, specimen=spec if "{spec}" in frame else None,
                     unit=unit if "{unit}" in frame else None, window=win if "{win}" in frame else None,
                     agent=agent if "{agent}" in frame else None, sex=sex if "{sex}" in frame else None)
            if kind == "steward" and "2025" in frame:
                s["window"] = "Y2025"
            view = {"agg": "clinician", "micro": "micro", "steward": "steward"}[kind]
            if kind == "agg" and s["window"] in ("Y2024", "Y2025"):
                view = "steward"
            if kind == "micro":
                s["organism"] = "Staphylococcus aureus" if frame.startswith("MRSA") else None
                s["specimen"] = None
            out.append({"id": rid, "lang": lang, "source": "template", "text": text, "intent": "aggregate",
                        "view": view, "slice": s})
    return out


def label_schema() -> dict:
    tab = router_rules.tables()
    v = tab["vocab"]
    return {"heads": {
        "intent": {"type": "choice", "classes": v["intents"]},
        "view": {"type": "choice", "classes": v["views"] + [None]},
        "organism": {"type": "choice", "classes": v["organisms"] + [None]},
        "specimen": {"type": "choice", "classes": v["specimens"] + [None]},
        "unit": {"type": "choice", "classes": v["units"] + v["groups"] + [tab["all_code"], "context", None]},
        "sex": {"type": "choice", "classes": v["sexes"] + [None]},
        "onset": {"type": "choice", "classes": v["onsets"] + [None]},
        "window": {"type": "choice", "classes": router_rules.schema_windows() + [None]},
        "agent": {"type": "choice", "classes": v["agents"] + [None]},
    }, "guardrail": "P(intent = patient_specific) is the calibrated gate; a redirect missed is the costly error."}


def main():
    hand = [dict(json.loads(l), source="hand") for l in HAND.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = hand + templated()
    OUT.mkdir(parents=True, exist_ok=True)
    train = [r for r in rows if not is_holdout(r["id"])]
    hold = [r for r in rows if is_holdout(r["id"])]
    for name, part in (("train", train), ("holdout", hold)):
        (OUT / f"{name}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part), encoding="utf-8")
    (OUT / "schema.json").write_text(json.dumps(label_schema(), ensure_ascii=False, indent=1), encoding="utf-8")
    by_intent = {}
    for r in rows:
        by_intent[r["intent"]] = by_intent.get(r["intent"], 0) + 1
    print(f"records {len(rows)} (hand {len(hand)}, template {len(rows) - len(hand)}); train {len(train)}, holdout {len(hold)}")
    print("by intent", by_intent)


if __name__ == "__main__":
    main()

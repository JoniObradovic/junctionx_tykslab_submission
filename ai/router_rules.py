"""Rule-based intent router and guardrail for the Ask box.

`route(text, lang=None)` classifies a Finnish or English request as an aggregate antibiogram
question, a patient-specific request (Tier B: redirected to the physician's judgement) or
off-topic, picks the view (clinician, infection control, stewardship) and extracts a cube slice
{organism, specimen, unit, sex, onset, window, agent}. The output is a closed vocabulary: the
page renders it as a selection of the cube, never as free text. Nothing here computes a number.

Keyword tables are built once from `build/config.py`, `data/wards.csv` and
`data/antibiotics.csv` plus the inflection stems below, and exported by `tables()` so that the
JS port inside web/index.html (between the ROUTER markers) runs the same logic over the same
tables. Matching is deliberately regex-light so the two ports agree: text is lowercased, every
character outside [a-z0-9äöå] becomes a space, and tokens are matched as whole words or word
prefixes on that padded string. The few regexes are ASCII-only (digits and Latin letters).

The always-on fallback of the AI layer: when the local endpoint (ai/serve.py) is not
reachable the page runs this router alone. A missed redirect is the failure that matters, so
patient cues win over everything else.
"""

from __future__ import annotations

import csv
import re
import sys
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # `python ai/router_rules.py`
from build import config  # noqa: E402

INTENTS = ("aggregate", "patient_specific", "off_topic")
VIEWS = ("clinician", "micro", "steward")
# Window sentinels the page resolves: "annual" is the latest complete calendar year,
# "current_year" the running one (web/data.json meta names both).
WINDOW_SENTINELS = ("annual", "current_year")
YEARS = tuple(f"Y{y}" for y in range(2020, 2040))
SLICE_FIELDS = ("organism", "specimen", "unit", "sex", "onset", "window", "agent")

# ---------------------------------------------------------------------------
# Keyword tables. Entry: (value, token, match) with match "word" (whole token or token phrase),
# "prefix" (token starts with) or "any" (substring). Optional flags follow as a dict.
# ---------------------------------------------------------------------------

def _e(value, token, match="prefix", **flags):
    d = {"value": value, "token": token, "match": match}
    d.update(flags)
    return d


ORGANISMS = [
    _e("Escherichia coli", "escherichia"), _e("Escherichia coli", "e coli"),
    _e("Escherichia coli", "ecoli"), _e("Escherichia coli", "coli"),
    _e("Escherichia coli", "kolibakteer"),
    _e("Klebsiella oxytoca", "oxytoca"),
    _e("Klebsiella pneumoniae", "pneumoniae"),
    # A bare genus is a weak match: it yields to a species of the same genus in the text.
    _e("Klebsiella pneumoniae", "klebsiell", weak=True),
    _e("Proteus mirabilis", "proteus"), _e("Proteus mirabilis", "mirabilis"),
    _e("Pseudomonas aeruginosa", "pseudomona"), _e("Pseudomonas aeruginosa", "aeruginosa"),
    _e("Staphylococcus saprophyticus", "saprophyt"), _e("Staphylococcus saprophyticus", "saprofyyt"),
    _e("Staphylococcus aureus", "aureu"), _e("Staphylococcus aureus", "mrsa"),
    _e("Staphylococcus aureus", "staphylococ", weak=True),
    _e("Staphylococcus aureus", "stafylokok", weak=True),
    _e("Staphylococcus aureus", "staph", "word", weak=True),
    _e("Enterococcus faecalis", "enterococ"), _e("Enterococcus faecalis", "enterokok"),
    _e("Enterococcus faecalis", "faecalis"), _e("Enterococcus faecalis", "vre", "word"),
    _e("Citrobacter freundii", "citrobacter"), _e("Citrobacter freundii", "freundii"),
]

SPECIMENS = [
    _e("urine", "virtsa"), _e("urine", "vti", "word"), _e("urine", "urine"), _e("urine", "urinary"),
    _e("urine", "uti", "word"), _e("urine", "utis", "word"), _e("urine", "cystit"), _e("urine", "kystiit"),
    _e("urine", "pyelonef"), _e("urine", "pyeloneph"), _e("urine", "prostatit"), _e("urine", "prostatiit"),
    _e("urine", "bacteriur"), _e("urine", "bakteriur"),
    _e("wound", "haava"), _e("wound", "haavo"), _e("wound", "wound"),
    _e("blood", "veri", "word"), _e("blood", "veressä", "word"), _e("blood", "verestä", "word"),
    _e("blood", "veren", "word"), _e("blood", "verivilj"), _e("blood", "blood"),
    _e("blood", "bakteremi"), _e("blood", "bacteraem"), _e("blood", "bacterem"),
    _e("blood", "sepsi"), _e("blood", "septi"),
]

# Inflection stems and short forms the ward names in wards.csv do not cover. Values are unit
# codes, group codes, "ALL" or "context" (the launch context: the care location of the view).
# "PHC" resolves to PHC-N or PHC-S with a direction word, else to the primary care group.
UNIT_STEMS = [
    _e("ED", "päivysty"), _e("ED", "ensiap"), _e("ED", "emergency"),
    _e("MED-A", "sisätautien a", "word"), _e("MED-A", "medicine a", "word"),
    _e("MED-B", "sisätautien b", "word"), _e("MED-B", "medicine b", "word"),
    _e("SURG", "kirurgi"), _e("SURG", "surg"),
    _e("URO", "urolog"),
    _e("ICU", "teho", "word"), _e("ICU", "teholla", "word"), _e("ICU", "tehon", "word"),
    _e("ICU", "teholle", "word"), _e("ICU", "teholta", "word"), _e("ICU", "tehossa", "word"),
    _e("ICU", "teho osasto"), _e("ICU", "tehohoi"), _e("ICU", "intensive"),
    _e("GER", "geriatri"),
    _e("PHC", "terveysasem"), _e("PHC", "terveyskesku"), _e("PHC", "health centre"),
    _e("PHC", "health center"),
    _e("inpatient", "vuodeosasto"), _e("inpatient", "osastoilla", "word"),
    _e("inpatient", "osastoille", "word"), _e("inpatient", "osastot", "word"),
    _e("inpatient", "osastojen", "word"), _e("inpatient", "inpatient"), _e("inpatient", "hospital ward"),
    _e("primary", "perusterveydenhuol"), _e("primary", "primary care"),
    _e("ALL", "koko varha"), _e("ALL", "varha", "word"), _e("ALL", "varhassa", "word"),
    _e("ALL", "varhan", "word"), _e("ALL", "varsinais"), _e("ALL", "koko alue"),
    _e("ALL", "alueella", "word"), _e("ALL", "koko aineisto"), _e("ALL", "whole county"),
    _e("ALL", "whole region"), _e("ALL", "all units"), _e("ALL", "all wards"),
    _e("ALL", "county", "word"), _e("ALL", "region", "word"), _e("ALL", "kaikki yksiköt"),
    _e("ALL", "kaikissa yksiköissä"), _e("ALL", "entire"),
    _e("context", "meidän osasto"), _e("context", "osastollamme", "word"),
    _e("context", "osastolla", "word"), _e("context", "omalla osastolla"),
    _e("context", "omassa yksikössä"), _e("context", "yksikössämme", "word"),
    _e("context", "meillä", "word"), _e("context", "täällä", "word"),
    _e("context", "our ward"), _e("context", "my ward"), _e("context", "this ward"),
    _e("context", "here", "word"), _e("context", "our unit"), _e("context", "my unit"),
]
PHC_DIRECTION = [_e("PHC-N", "pohjoi"), _e("PHC-N", "north"), _e("PHC-S", "etelä"), _e("PHC-S", "south")]

SEX = [
    _e("F", "nais"), _e("F", "nainen", "word"), _e("F", "wom"), _e("F", "female"),
    _e("M", "mieh"), _e("M", "mies"), _e("M", "men", "word"), _e("M", "man", "word"), _e("M", "male"),
]
ONSET = [
    _e("hospital", "sairaalasyntyi"), _e("hospital", "sairaalaperäi"), _e("hospital", "hospital onset"),
    _e("hospital", "nosocomial"), _e("hospital", "hospital acquired"),
    _e("community", "avohoi"), _e("community", "community"),
]
AGENT_SHORT_FORMS = {
    "TZP": ("tatsobak", "tazobac", "piptaz", "pip taz", "tatso"),
    "CIP": ("sipro", "cipro"), "VAN": ("vanko",), "MEM": ("merop",), "MEC": ("pivmes",),
}

VIEW_MICRO = [
    _e("micro", t) for t in ("hälyty", "rypä", "epidemi", "alert", "cluster", "outbreak", "mrsa",
                             "esbl", "poikkeam", "tiedon laatu", "data quality", "eucast")
] + [_e("micro", t, "word") for t in ("vre", "cpe", "laatu", "quality", "tapaukset", "tapauksia", "cases")]
VIEW_STEWARD = [
    _e("steward", t) for t in ("vuosittain", "vuosivertail", "trendi", "trend", "kehitys", "vertail",
                               "annual", "yearly", "compar", "year on year", "over the years",
                               "viime vuonna", "viime vuosi")
] + [_e("steward", t, "word") for t in ("vuonna", "vuosina", "vertaa")]

# Phrase windows, first match in table order wins; numeric forms are matched by regex first.
WINDOW_PHRASES = [
    _e("52w", t) for t in ("over the last year", "the last year", "past year", "viimeisen vuoden",
                           "vuoden aikana", "viimeinen vuosi", "viimeisen 12", "viimeiset 12")
] + [_e("annual", t) for t in ("viime vuonna", "viime vuosi", "viime vuoden", "last year")
] + [_e("current_year", t) for t in ("tänä vuonna", "this year", "kuluva vuosi", "kuluvana vuonna",
                                     "year to date", "ytd")
] + [_e("26w", t) for t in ("puoli vuotta", "puolen vuoden", "puolivuo", "half a year", "half year",
                            "six months")
] + [_e("13w", t) for t in ("kvartaali", "neljännes", "quarter")
] + [_e("4w", t) for t in ("kuukausi", "kuukauden", "kuukautta", "viime kuukau", "last month",
                           "past month", "this month", "tässä kuussa")]

DOMAIN = [
    _e("domain", t) for t in ("resisten", "herkk", "antibioot", "mikrobilääk", "bakteer", "mikrob",
                              "viljel", "kanta", "kannat", "kanto", "hälyty", "rypä", "epidemi",
                              "susceptib", "resistan", "antibiotic", "antimicrob", "bacteri",
                              "culture", "isolate", "alert", "cluster", "outbreak", "esbl", "mrsa",
                              "eucast", "infekti", "infection", "antibiogram", "sepsi", "tulok",
                              "result", "vastau")
] + [_e("domain", t, "word") for t in ("vre", "cpe")]

# Patient cues. weak: a bare patient word that a count phrase (OVERRIDE) discounts.
# clinical: the cue itself makes the request clinical, so it counts as domain context.
# lang: pronouns apply only in that language (Finnish "he" means "they").
PATIENT = (
    [_e("patient", t, "word", weak=True) for t in ("potilas", "potilaan", "potilaalle", "potilaalla",
                                                   "potilaasta", "potilasta", "potilaaseen", "patient")]
    + [_e("patient", "patient s")]
    + [_e("patient", t, "word") for t in ("potilaani", "potilaallani", "potilaalleni", "potilaastani",
                                          "minulla", "minulle", "minun", "minua", "itselleni",
                                          "annan", "annanko", "aloitan", "aloitanko", "määrään",
                                          "määräänkö", "jatkan", "jatkanko", "hoidan", "hoidanko",
                                          "otan", "otanko", "vaihdan", "vaihdanko", "child", "infant",
                                          "baby", "bed", "mg", "kg", "crp", "kipu", "kipua", "pain",
                                          "ssn", "sotu")]
    + [_e("patient", t) for t in ("tälle potilaalle", "minun potila", "laps", "huone", "vuodepaik",
                                  "my patient", "this patient", "the patient", "for a patient",
                                  "a patient", "i have", "i am", "i m", "should i", "do i", "can i",
                                  "could i", "prescrib")]
    + [_e("patient", t, "word", lang="fi") for t in ("hän", "hänellä", "hänelle", "hänen", "hänet",
                                                     "häneltä", "hänestä")]
    + [_e("patient", t, "word", lang="en") for t in ("she", "he", "her", "him", "his", "hers")]
    + [_e("patient", t, clinical=True) for t in ("allergi", "raskaa", "annos", "annostu", "kreatiniin",
                                                 "munuais", "oire", "kuume", "korvatulehdu", "allerg",
                                                 "pregnan", "dose", "dosing", "creatinine", "renal",
                                                 "kidney", "fever", "febrile", "symptom", "otitis",
                                                 "date of birth", "identifier")]
    + [_e("patient", "tunnus", "any", clinical=True)]
)
OVERRIDE = [_e("override", t) for t in ("kuinka mon", "how many", "osuus", "prosent", "percent",
                                        "proportion", "number of")
            ] + [_e("override", t, "word") for t in ("määrä", "share", "count")]

# ASCII-only regexes shared with the JS port. "norm" ones run on the normalised padded text,
# "raw" ones on the lowercased original (the identifier shape needs its hyphen).
PATTERNS = {
    "weeks": {"on": "norm", "re": r"\b(\d{1,3}) ?(vk|viikkoa|viikon|viikkoon|viikossa|viikko|wks|wk|weeks|week|w)\b"},
    "months": {"on": "norm", "re": r"\b(\d{1,3}) ?(kk|kuukautta|kuukauden|kuukausi|months|month|mo)\b"},
    "year": {"on": "norm", "re": r"\b(20[2-3][0-9])\b"},
    "age": {"on": "norm", "re": r"\b\d{1,3} (vuotiaa|vuotias|vuotta|v|years old|year old|yo)\b"},
    "room": {"on": "norm", "re": r"\b(room|huone|huoneen|huoneessa) \d{1,3}\b"},
    "identifier": {"on": "raw", "re": r"\d{6}[-+a-z]\d{3}[0-9a-z]"},
}
# Language markers for detection when the caller does not say; Finnish wins ties.
LANG_MARKERS = {
    "fi": ("ja", "on", "mikä", "mitä", "onko", "miten", "kuinka", "paljonko", "näytä", "vai", "koko",
           "viime", "meidän", "oma", "omalla", "nyt", "tässä", "tämä", "tämän", "tälle", "vuonna",
           "viikkoa", "vk", "kk", "oli", "ovat", "olen", "ei", "se", "että", "kun", "jos", "myös", "tai",
           "missä", "milloin", "kuka", "hei", "kiitos", "hän", "he"),
    "en": ("the", "what", "is", "and", "in", "of", "for", "on", "any", "show", "how", "last", "our",
           "my", "this", "with", "to", "which", "or", "she", "her", "his", "him", "i", "do", "does",
           "did", "has", "have", "are", "was", "were", "be", "a", "an", "at", "by", "from", "as"),
}
LADDER = [f"{w}w" for w in config.WINDOW_LADDER_WEEKS]
NORM_RE = re.compile(r"[^a-z0-9äöå]+")


def _norm_token(s: str) -> str:
    return NORM_RE.sub(" ", s.lower()).strip()


def _units_from_csv() -> list[dict]:
    """Ward codes and names from wards.csv, as word (codes) and prefix (names) entries."""
    out = []
    with open(config.DATA_DIR / "wards.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = row["ward_code"]
            out.append(_e(code, _norm_token(code), "word"))
            out.append(_e(code, _norm_token(row["ward_fi"])))
            out.append(_e(code, _norm_token(row["ward_en"])))
    return out


def _agents_from_csv() -> tuple[list[dict], list[str]]:
    """Agent codes (word, must also appear upper-case in the raw text) and name stems (prefix)."""
    out, codes = [], []
    with open(config.DATA_DIR / "antibiotics.csv", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            code = row["antibiotic_code"]
            codes.append(code)
            out.append(_e(code, code.lower(), "word", code=True))
            for name in (row["antibiotic_fi"], row["antibiotic"]):
                first = _norm_token(name).split(" ")[0]
                out.append(_e(code, first))
                if len(first) > 6:
                    out.append(_e(code, first[:6]))
            for short in AGENT_SHORT_FORMS.get(code, ()):
                out.append(_e(code, short))
    return out, codes


def data_years() -> list[str]:
    """Calendar-year windows the cube actually holds (web/data.json meta), else the open range."""
    if config.DATA_JSON.exists():
        import json
        with open(config.DATA_JSON, encoding="utf-8") as f:
            meta = json.load(f)["meta"]
        return sorted(w for w in meta["windows"] if w.startswith("Y"))
    return list(YEARS)


def schema_windows() -> list[str]:
    """The window values a model or a label may take: ladder rungs, sentinels, the data's years."""
    return LADDER + list(WINDOW_SENTINELS) + data_years()


@lru_cache(maxsize=1)
def tables() -> dict:
    """Everything the JS port needs: entries, patterns, vocabularies, ladder, language markers."""
    agents, agent_codes = _agents_from_csv()
    entries = {
        "organism": ORGANISMS, "specimen": SPECIMENS,
        "unit": _units_from_csv() + UNIT_STEMS, "phc_direction": PHC_DIRECTION,
        "sex": SEX, "onset": ONSET, "agent": agents,
        "view_micro": VIEW_MICRO, "view_steward": VIEW_STEWARD, "window": WINDOW_PHRASES,
        "domain": DOMAIN, "patient": PATIENT, "override": OVERRIDE,
    }
    vocab = {
        "organisms": sorted({e["value"] for e in ORGANISMS}),
        "specimens": list(config.SPECIMENS),
        "units": list(config.GROUP_OF_UNIT),
        "groups": list(config.GROUPS),
        "agents": agent_codes,
        "windows": LADDER + list(WINDOW_SENTINELS) + list(YEARS),
        "sexes": list(config.SEX_STRATA), "onsets": list(config.ONSET_STRATA),
        "intents": list(INTENTS), "views": list(VIEWS),
    }
    return {"entries": entries, "patterns": PATTERNS, "vocab": vocab,
            "ladder_weeks": list(config.WINDOW_LADDER_WEEKS), "lang_markers": LANG_MARKERS,
            "all_code": config.ALL_UNITS["code"], "endpoint": config.AI_ENDPOINT,
            "gate_p_patient_specific": config.AI_GATE_P_PATIENT_SPECIFIC}


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """Lowercase, non-alphanumerics to single spaces, padded with one space each side."""
    return " " + NORM_RE.sub(" ", (text or "").lower()).strip() + " "


def _position(norm: str, entry: dict) -> int:
    tok = entry["token"]
    if entry["match"] == "word":
        return norm.find(" " + tok + " ")
    if entry["match"] == "prefix":
        return norm.find(" " + tok)
    return norm.find(tok)


def _matches(norm: str, entries: list[dict], lang: str | None = None) -> list[dict]:
    out = []
    for i, e in enumerate(entries):
        if e.get("lang") and e["lang"] != lang:
            continue
        pos = _position(norm, e)
        if pos >= 0:
            out.append({"pos": pos, "order": i, "entry": e})
    out.sort(key=lambda m: (m["pos"], m["order"]))
    return out


def detect_lang(norm: str, markers: dict | None = None) -> str:
    markers = markers or LANG_MARKERS
    if any(c in norm for c in "äöå"):
        return "fi"
    fi = sum(1 for t in markers["fi"] if (" " + t + " ") in norm)
    en = sum(1 for t in markers["en"] if (" " + t + " ") in norm)
    return "en" if en > fi else "fi"


def _rung(weeks: int, ladder: list[int]) -> str:
    for w in ladder:
        if weeks <= w:
            return f"{w}w"
    return f"{ladder[-1]}w"


def _first(matches: list[dict]):
    return (matches[0]["entry"]["value"], matches[0]["entry"]["token"]) if matches else (None, None)


def _resolve_organism(matches: list[dict]):
    strong = [m for m in matches if not m["entry"].get("weak")]
    genera = {m["entry"]["value"].split(" ")[0] for m in strong}
    kept = strong + [m for m in matches if m["entry"].get("weak")
                     and m["entry"]["value"].split(" ")[0] not in genera]
    kept.sort(key=lambda m: (m["pos"], m["order"]))
    return _first(kept)


def _resolve_unit(norm: str, matches: list[dict], tab: dict):
    """Explicit unit beats group beats ALL beats launch context; earliest explicit unit wins."""
    units = set(tab["vocab"]["units"])
    groups = set(tab["vocab"]["groups"])
    all_code = tab["all_code"]
    resolved = []
    for m in matches:
        value, token = m["entry"]["value"], m["entry"]["token"]
        if value == "PHC":
            direction = _matches(norm, tab["entries"]["phc_direction"])
            value = direction[0]["entry"]["value"] if direction else "primary"
        resolved.append((m["pos"], m["order"], value, token))
    for pick in (lambda v: v in units, lambda v: v in groups, lambda v: v == all_code, lambda v: v == "context"):
        hits = [r for r in resolved if pick(r[2])]
        if hits:
            hits.sort()
            return hits[0][2], hits[0][3]
    return None, None


def _resolve_window(norm: str, tab: dict):
    pats = tab["patterns"]
    ladder = tab["ladder_weeks"]
    m = re.search(pats["weeks"]["re"], norm)
    if m:
        return _rung(int(m.group(1)), ladder), m.group(0).strip()
    m = re.search(pats["months"]["re"], norm)
    if m:
        return _rung(int(m.group(1)) * 4, ladder), m.group(0).strip()
    years = re.findall(pats["year"]["re"], norm)
    if years:
        y = max(years)
        return "Y" + y, y
    phrases = _matches(norm, tab["entries"]["window"])
    if phrases:
        best = min(phrases, key=lambda p: p["order"])   # table order, not position
        return best["entry"]["value"], best["entry"]["token"]
    return None, None


def route(text: str, lang: str | None = None, tab: dict | None = None, debug: bool = False) -> dict:
    """Classify one request. Returns intent, view, slice, lang, matched tokens and the cues.

    debug=True adds the normalised text and every candidate match per field with its position,
    for the lab IT debug view; the JS port does not carry this and the parity test ignores it.
    """
    tab = tab or tables()
    ent = tab["entries"]
    raw = (text or "").lower()
    norm = normalise(text)
    if lang not in ("fi", "en"):
        lang = detect_lang(norm, tab["lang_markers"])
    matched, cues = {}, []

    organism, matched["organism"] = _resolve_organism(_matches(norm, ent["organism"]))
    specimen, matched["specimen"] = _first(_matches(norm, ent["specimen"]))
    unit, matched["unit"] = _resolve_unit(norm, _matches(norm, ent["unit"]), tab)
    sexes = _matches(norm, ent["sex"])
    sex_values = {m["entry"]["value"] for m in sexes}
    sex, matched["sex"] = _first(sexes) if len(sex_values) == 1 else (None, None)
    onset, matched["onset"] = _first(_matches(norm, ent["onset"]))
    agent_hits = [m for m in _matches(norm, ent["agent"])
                  if not m["entry"].get("code") or re.search(r"\b" + m["entry"]["value"] + r"\b", text or "")]
    agent, matched["agent"] = _first(agent_hits)
    window, matched["window"] = _resolve_window(norm, tab)

    # Guardrail: strong cues count alone; weak ones only without a count phrase.
    override = _matches(norm, ent["override"])
    patient = _matches(norm, ent["patient"], lang)
    strong = [m for m in patient if not m["entry"].get("weak")]
    weak = [m for m in patient if m["entry"].get("weak")]
    clinical = any(m["entry"].get("clinical") for m in patient)
    for name in ("age", "room", "identifier"):
        p = tab["patterns"][name]
        if re.search(p["re"], raw if p["on"] == "raw" else norm):
            strong.append({"entry": {"token": name}})
            clinical = True
    cues += ["patient:" + m["entry"]["token"] for m in strong]
    if weak and not override:
        cues += ["patient:" + m["entry"]["token"] for m in weak]
    if weak and override:
        cues += ["override:" + override[0]["entry"]["token"]]
    domain_hits = _matches(norm, ent["domain"])
    cues += ["domain:" + m["entry"]["token"] for m in domain_hits[:3]]
    in_domain = bool(domain_hits) or clinical or any(v is not None for v in (organism, specimen, agent, unit))

    if (strong or (weak and not override)) and in_domain:
        intent, view = "patient_specific", None
    elif not in_domain:
        intent, view = "off_topic", None
    else:
        intent = "aggregate"
        micro = _matches(norm, ent["view_micro"])
        steward = _matches(norm, ent["view_steward"]) or re.findall(tab["patterns"]["year"]["re"], norm)
        view = "micro" if micro else ("steward" if steward else "clinician")
        if micro:
            cues.append("view:" + micro[0]["entry"]["token"])
        elif steward:
            cues.append("view:" + (steward[0]["entry"]["token"] if isinstance(steward[0], dict) else steward[0]))

    result = {"intent": intent, "view": view, "lang": lang,
              "slice": {"organism": organism, "specimen": specimen, "unit": unit, "sex": sex,
                        "onset": onset, "window": window, "agent": agent},
              "matched": {k: v for k, v in matched.items() if v}, "cues": cues}
    if debug:
        cand = lambda field: [{"value": m["entry"]["value"], "token": m["entry"]["token"], "pos": m["pos"]}
                              for m in _matches(norm, ent[field], lang)]
        result["debug"] = {"normalised": norm.strip(), "lang_detected": lang,
                           "candidates": {f: cand(f) for f in ("organism", "specimen", "unit", "sex", "onset", "agent", "patient", "override", "domain", "view_micro", "view_steward", "window")}}
    return result


if __name__ == "__main__":   # quick manual check: python ai/router_rules.py "text"
    import json
    for arg in sys.argv[1:]:
        print(json.dumps(route(arg), ensure_ascii=False))

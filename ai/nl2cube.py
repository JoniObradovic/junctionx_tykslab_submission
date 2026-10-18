"""NL-to-cube extraction with a local llama.cpp server.

The model fills a JSON object whose every field is an enumeration of the cube's vocabulary
(organisms, specimen groups, unit codes, strata, window ladder, agent codes), enforced by the
server's JSON-schema grammar. It cannot express a patient, a number or free text. The rule-based
router stays the guardrail: `combine()` lets the model refine the slice and the view, but a
patient-specific verdict from either side wins (fail closed).

Serving: any llama.cpp server with the OpenAI-compatible API, e.g.

    llama-server --model <instruct>.gguf --alias router --host 127.0.0.1 --port 8081 \
      --ctx-size 4096 --parallel 4

The model file is lab-owned configuration and is not part of this repository.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai import router_rules  # noqa: E402
from build import config  # noqa: E402

LLM_URL = os.environ.get("AEI_LLM_URL", "http://127.0.0.1:8081")
LLM_TIMEOUT = float(os.environ.get("AEI_LLM_TIMEOUT", "12"))
HEALTH_TIMEOUT = 2.0
MAX_TOKENS = 160          # the JSON object is about 120 tokens
TOP_LOGPROBS = 5          # enough to see all three intents at the intent token

# Model profiles: the servers the endpoint may ask (AEI_LLM_PROFILES overrides as JSON). Both are
# optional comparison profiles; the shipped interpreter is the Laya guard in laya_guard.py.
PROFILES = {
    "instruct": {"url": LLM_URL, "label": "Qwen3-30B-A3B Instruct 2507 (local llama.cpp)", "thinking": None,
                 "temperature": 0, "max_tokens": MAX_TOKENS, "timeout_s": LLM_TIMEOUT},
    "qwen36": {"url": "http://127.0.0.1:8082", "label": "Qwen3.6-27B (local llama.cpp), thinking off",
               "thinking": False, "temperature": 0, "max_tokens": MAX_TOKENS, "timeout_s": 40},
}
# A thinking profile (the same server with thinking on) was measured and dropped: about 90 s per
# question, too slow for the Ask box, and the schema already constrains the answer.
if os.environ.get("AEI_LLM_PROFILES"):
    PROFILES = json.loads(os.environ["AEI_LLM_PROFILES"])
DEFAULT_PROFILE = os.environ.get("AEI_LLM_PROFILE", "instruct")
INTENT_PREFIX = {"agg": "aggregate", "patient": "patient_specific", "off": "off_topic"}


def _nullable(values):
    return {"anyOf": [{"enum": list(values)}, {"type": "null"}]}


def schema(tab: dict | None = None) -> dict:
    """The strict output schema: closed enumerations only, every field required."""
    tab = tab or router_rules.tables()
    v = tab["vocab"]
    windows = router_rules.schema_windows()
    return {
        "type": "object",
        "properties": {
            "intent": {"enum": list(v["intents"])},
            "view": _nullable(v["views"]),
            "organism": _nullable(v["organisms"]),
            "specimen": _nullable(v["specimens"]),
            "unit": _nullable(list(v["units"]) + list(v["groups"]) + [tab["all_code"], "context"]),
            "sex": _nullable(v["sexes"]),
            "onset": _nullable(v["onsets"]),
            "window": _nullable(windows),
            "agent": _nullable(v["agents"]),
        },
        "required": ["intent", "view", "organism", "specimen", "unit", "sex", "onset", "window", "agent"],
        "additionalProperties": False,
    }


def _units_text() -> str:
    with open(config.DATA_DIR / "wards.csv", encoding="utf-8", newline="") as f:
        return "; ".join(f'{r["ward_code"]} = {r["ward_fi"]} / {r["ward_en"]}' for r in csv.DictReader(f))


def _agents_text() -> str:
    with open(config.DATA_DIR / "antibiotics.csv", encoding="utf-8", newline="") as f:
        return "; ".join(f'{r["antibiotic_code"]} = {r["antibiotic_fi"]} / {r["antibiotic"]}' for r in csv.DictReader(f))


def system_prompt(tab: dict | None = None) -> str:
    tab = tab or router_rules.tables()
    v = tab["vocab"]
    ladder = ", ".join(f'"{w}"' for w in router_rules.LADDER)
    groups = "; ".join(f'"{g}" = {config.GROUPS[g]["fi"]} / {config.GROUPS[g]["en"]}' for g in v["groups"])
    return (
        "You turn a Finnish or English request about hospital antibiotic resistance statistics into a "
        "JSON query over a precomputed cube. Never answer the question; only fill the fields.\n"
        'intent: "aggregate" = a question about resistance statistics, alerts, clusters or data quality '
        'for a unit, a group of units or the whole county; "patient_specific" = anything about one '
        "person (my patient, this patient, a pronoun such as he, she or hän, an age, a weight, a dose, "
        "an allergy, pregnancy, symptoms, a lab value, a room or bed, an identifier, what should I give); "
        '"off_topic" = not about antibiotic resistance, bacteria or antibiotics at all. A count or share '
        'of patients ("how many patients", "potilaiden osuus") is aggregate.\n'
        'view (aggregate only, else null): "micro" for alerts, clusters, outbreaks, ESBL, MRSA, VRE, CPE, '
        'data quality or EUCAST; "steward" for calendar years, annual trends or comparisons between '
        'years; otherwise "clinician".\n'
        f"organism: one of {json.dumps(v['organisms'])} or null. Finnish: kolibakteeri = Escherichia coli, "
        "stafylokokki = Staphylococcus aureus, enterokokki = Enterococcus faecalis. MRSA means "
        "Staphylococcus aureus, VRE means Enterococcus faecalis, a bare Klebsiella means Klebsiella "
        "pneumoniae. ESBL and CPE name no organism. If several are named, take the first.\n"
        'specimen: "urine" (virtsa, VTI, UTI, cystitis, pyelonephritis, prostatitis), "wound" (haava), '
        '"blood" (veri, veriviljely, sepsis, bacteraemia) or null.\n'
        f"unit: a unit code ({_units_text()}); a group code ({groups}); "
        f'"{tab["all_code"]}" = koko Varha, Varsinais-Suomi, koko alue, whole county, all units; '
        '"context" = the asker\'s own ward (meidän osastolla, omalla osastolla, meillä, our ward, my ward, '
        "here); or null when no unit, group, county or own-ward phrase is named (never default to the "
        f'county: "{tab["all_code"]}" only when the whole county or all units is asked for). Teho = ICU, '
        "urologia = URO, kirurgia = SURG, geriatria = GER, päivystys = ED, sisätaudit A/B = MED-A/MED-B, "
        "terveysasema pohjoinen/etelä = PHC-N/PHC-S, terveysasemat = primary.\n"
        'sex: "F" (naiset, women), "M" (miehet, men) or null; null when both are named.\n'
        'onset: "hospital" (sairaalasyntyinen, hospital-onset, nosocomial), "community" '
        "(avohoitosyntyinen, community-onset) or null.\n"
        f"window: trailing weeks rounded up to one of {ladder} (one month = 4w, 3 months = 13w, half a "
        'year = 26w, 12 months or over the last year = 52w); a calendar year as "Y2025"; "annual" for '
        'last year (viime vuonna); "current_year" for this year (tänä vuonna); null when no period is '
        "named. With two years take the later one.\n"
        f"agent: a code ({_agents_text()}) or null; sipro = CIP, pip-tazo = TZP. If several are named, "
        "take the first.\n"
        "Output only the JSON object."
    )


def _intent_probabilities(logprobs: dict | None) -> dict | None:
    """Probability of each intent from the top logprobs of the first token of the intent value."""
    content = (logprobs or {}).get("content") or []
    seen = ""
    for tok in content:
        if re.search(r'\{\s*"intent"\s*:\s*"$', seen):
            mass = {k: 0.0 for k in INTENT_PREFIX.values()}
            for alt in tok.get("top_logprobs", []):
                for prefix, intent in INTENT_PREFIX.items():
                    if alt["token"].strip().startswith(prefix):
                        mass[intent] += math.exp(alt["logprob"])
                        break
            total = sum(mass.values())
            return {k: round(m / total, 4) for k, m in mass.items()} if total > 0 else None
        seen += tok["token"]
    return None


def extract(text: str, lang: str | None = None, url: str | None = None, timeout: float | None = None,
            tab: dict | None = None, debug: bool = False, profile: str = DEFAULT_PROFILE) -> dict:
    """One schema-constrained chat completion. Returns ok=False with the error when the server is down.

    With debug=True the result also carries the exact request (system prompt, user message,
    schema) and the raw response (content, any reasoning content, finish reason, usage, llama.cpp
    timings, per-token logprobs with alternatives) for the lab IT debug view.
    """
    tab = tab or router_rules.tables()
    prof = PROFILES.get(profile) or PROFILES[DEFAULT_PROFILE]
    url = url or prof["url"]
    timeout = timeout or prof.get("timeout_s") or LLM_TIMEOUT
    body = {
        "model": profile, "temperature": prof.get("temperature", 0), "max_tokens": prof.get("max_tokens", MAX_TOKENS),
        "logprobs": True, "top_logprobs": TOP_LOGPROBS,
        "messages": [{"role": "system", "content": system_prompt(tab)},
                     {"role": "user", "content": (f"[{lang}] " if lang else "") + (text or "")}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "cube_query", "schema": schema(tab)}},
    }
    for key in ("top_p", "top_k", "min_p", "repeat_penalty"):
        if key in prof:
            body[key] = prof[key]
    if prof.get("thinking") is not None:
        body["chat_template_kwargs"] = {"enable_thinking": bool(prof["thinking"])}
    req = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as err:
        return {"ok": False, "error": type(err).__name__, "latency_ms": (time.perf_counter() - t0) * 1000, "profile": profile}
    latency = (time.perf_counter() - t0) * 1000
    choice = payload["choices"][0]
    reasoning = (choice.get("message") or {}).get("reasoning_content")
    dbg = None
    if debug:
        msg = choice.get("message", {})
        dbg = {"request": {"url": url.rstrip("/") + "/v1/chat/completions", "model": body["model"], "profile": profile,
                           "temperature": body["temperature"], "thinking": prof.get("thinking"),
                           "max_tokens": body["max_tokens"], "system_prompt": body["messages"][0]["content"],
                           "user_message": body["messages"][1]["content"], "schema": body["response_format"]["json_schema"]["schema"]},
               "response": {"content": msg.get("content"), "reasoning_content": msg.get("reasoning_content"),
                            "finish_reason": choice.get("finish_reason"), "usage": payload.get("usage"),
                            "timings": payload.get("timings"), "model": payload.get("model"),
                            "logprobs": [{"token": t_["token"], "logprob": round(t_["logprob"], 3),
                                          "top": [{"token": x["token"], "logprob": round(x["logprob"], 3)} for x in t_.get("top_logprobs", [])]}
                                         for t_ in ((choice.get("logprobs") or {}).get("content") or [])]}}
    try:
        out = json.loads(choice["message"]["content"] or "")
    except (KeyError, TypeError, json.JSONDecodeError) as err:
        # A thinking model that ran out of tokens inside its chain returns no content.
        return {"ok": False, "error": "bad_json:" + type(err).__name__ + (":truncated" if choice.get("finish_reason") == "length" else ""),
                "latency_ms": latency, "debug": dbg, "profile": profile, "reasoning_chars": len(reasoning or "")}
    slice_ = {k: out.get(k) for k in router_rules.SLICE_FIELDS}
    return {"ok": True, "intent": out.get("intent"), "view": out.get("view") if out.get("intent") == "aggregate" else None,
            "slice": slice_, "p_intent": _intent_probabilities(choice.get("logprobs")), "latency_ms": latency,
            "model": payload.get("model"), "usage": payload.get("usage"), "debug": dbg, "profile": profile,
            "reasoning": reasoning, "reasoning_chars": len(reasoning or "")}


def combine(rules: dict, llm: dict, gate: float = config.AI_GATE_P_PATIENT_SPECIFIC) -> dict:
    """Guard with the rules, refine with the model.

    Patient-specific wins when the rules say so, when the model's verdict says so, or when the
    model's probability of patient-specific reaches the gate. The model only fills slice fields the rules left
    empty: an explicit keyword beats a paraphrase, so the model adds coverage without overriding.
    It may also rescue an off-topic verdict when it recognises a paraphrase as an aggregate question.
    """
    out = {k: rules[k] for k in ("lang", "matched", "cues")}
    steps = [f"rules: intent={rules['intent']}, view={rules['view']}, cues={rules['cues']}"]
    if not llm.get("ok"):
        steps.append(f"model: not available ({llm.get('error')}); rules alone")
        out.update(intent=rules["intent"], view=rules["view"], slice=dict(rules["slice"]), source="rules-only",
                   llm={"ok": False, "error": llm.get("error"), "latency_ms": round(llm.get("latency_ms", 0), 1), "debug": llm.get("debug")})
        if rules.get("debug") or llm.get("debug"):
            out["debug"] = {"steps": steps, "rules": rules.get("debug")}
        return out
    p_patient = (llm.get("p_intent") or {}).get("patient_specific")
    gated = p_patient is not None and p_patient >= gate
    steps.append(f"model: intent={llm['intent']}, view={llm['view']}, p={llm.get('p_intent')}, {llm['latency_ms']:.0f} ms")
    if rules["intent"] == "patient_specific" or llm["intent"] == "patient_specific" or gated:
        intent = "patient_specific"
        steps.append("guard: patient_specific because " + ", ".join(w for w, on in (("rules said so", rules["intent"] == "patient_specific"),
                     ("model said so", llm["intent"] == "patient_specific"), (f"P(patient_specific) {p_patient} >= gate {gate}", gated)) if on))
    elif rules["intent"] == "off_topic" and llm["intent"] == "aggregate":
        intent = "aggregate"
        steps.append("guard: rules saw no vocabulary, model recognised an aggregate paraphrase; aggregate")
    else:
        intent = rules["intent"]
        steps.append(f"guard: gate not reached (P={p_patient} < {gate}); rules' intent {intent} kept")
    slice_ = {}
    for k in router_rules.SLICE_FIELDS:
        if rules["slice"][k] is not None:
            slice_[k] = rules["slice"][k]
            if llm["slice"].get(k) not in (None, rules["slice"][k]):
                steps.append(f"slice.{k}: rules {rules['slice'][k]!r} kept, model proposed {llm['slice'].get(k)!r}")
        else:
            slice_[k] = llm["slice"].get(k)
            if slice_[k] is not None:
                steps.append(f"slice.{k}: filled from model {slice_[k]!r} (rules found nothing)")
    # The rules name a view only on a keyword (cue "view:..."); otherwise "clinician" is a default,
    # and the model's reading of the view wins over a default.
    rules_named_view = any(str(c).startswith("view:") for c in rules.get("cues") or [])
    view = ((rules["view"] if rules_named_view else (llm.get("view") or rules["view"])) if intent == "aggregate" else None)
    if intent == "aggregate" and not rules_named_view and llm.get("view") and llm["view"] != rules["view"]:
        steps.append(f"view: rules defaulted to {rules['view']!r}, model proposed {llm['view']!r}; model's view used")
    steps.append(f"final: intent={intent}, view={view}, slice={ {k: v for k, v in slice_.items() if v is not None} }")
    if llm.get("debug") or rules.get("debug"):
        out["debug"] = {"steps": steps, "rules": rules.get("debug")}
    out.update(intent=intent, view=view, slice=slice_, source="rules+llm",
               gate={"p_patient_specific": p_patient, "threshold": gate, "fired": gated},
               llm={"ok": True, "intent": llm["intent"], "view": llm["view"], "slice": llm["slice"],
                    "p_intent": llm.get("p_intent"), "latency_ms": round(llm["latency_ms"], 1),
                    "model": llm.get("model"), "profile": llm.get("profile"), "agreed_intent": llm["intent"] == rules["intent"],
                    "agreed_slice": llm["slice"] == rules["slice"], "debug": llm.get("debug"),
                    "reasoning": llm.get("reasoning"), "reasoning_chars": llm.get("reasoning_chars", 0)})
    return out


def profiles_health(timeout: float = HEALTH_TIMEOUT) -> list[dict]:
    """Every profile with its reachability, for the endpoint's /health and the page's selector."""
    out, seen = [], {}
    for name, prof in PROFILES.items():
        if prof["url"] not in seen:
            seen[prof["url"]] = health(prof["url"], timeout)
        h = seen[prof["url"]]
        out.append({"name": name, "label": prof["label"], "url": prof["url"], "thinking": prof.get("thinking"),
                    "ok": h["ok"], "model": h["model"], "timeout_ms": int(prof.get("timeout_s", LLM_TIMEOUT) * 1000)})
    return out


def health(url: str = LLM_URL, timeout: float = HEALTH_TIMEOUT) -> dict:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout) as resp:
            status = json.load(resp)
        model = None
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/props", timeout=timeout) as resp:
                props = json.load(resp)
            model = Path(props.get("model_path") or "").name or None
        except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
            model = None
        return {"ok": status.get("status") == "ok", "url": url, "model": model}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {"ok": False, "url": url, "model": None}


if __name__ == "__main__":   # manual check: python ai/nl2cube.py "text"
    for arg in sys.argv[1:]:
        r = router_rules.route(arg)
        print(json.dumps(combine(r, extract(arg, r["lang"])), ensure_ascii=False, indent=1))

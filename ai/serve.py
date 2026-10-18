"""Tiny FastAPI endpoint for the Ask box.

    uv sync --group ai
    uv run uvicorn ai.serve:app --host 127.0.0.1 --port 8765   # host and port: config.AI_ENDPOINT

POST /ask   {"text": "...", "lang": "fi"|"en"|null, "unit": "URO"}  ->  the router result
            (intent, view, slice, lang, matched, cues) plus "source" ("rules+llm" when the local
            llama.cpp server answered, "rules-only" when it did not) and an "llm" block with the
            model's own verdict, latency and intent probabilities.
GET  /health -> {"status": "ok", "llm": {...}}

The page (web/index.html) calls this only when opened locally; a hosted https page cannot reach
an http endpoint, so the inline JS router is the always-on fallback. The endpoint never reads
the cube or the CSVs: it returns a selection, and the page renders the numbers. Requests are
not logged (no text is written anywhere).

Environment: AEI_LLM_URL (default http://127.0.0.1:8081), AEI_LLM_TIMEOUT seconds (default 12),
AEI_NO_LLM=1 to run the rules alone. With the `laya` dependency group installed
(`uv sync --group laya`) the profile "laya" runs the Laya guard in this process, on CPU by
default (AEI_LAYA_DEVICE), loading the fine-tuned checkpoint from AEI_LAYA_MODEL_DIR or the hub.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai import nl2cube, router_rules  # noqa: E402

NO_LLM = os.environ.get("AEI_NO_LLM") == "1"
LAYA_PROFILE = "laya"
_laya = {"guard": None, "error": None}


def laya_available() -> bool:
    try:
        import laya  # noqa: F401
        return True
    except ImportError:
        return False


def laya_guard():
    """Load the Laya guard once, on first use; None when the package or checkpoint is missing."""
    if _laya["guard"] is None and _laya["error"] is None:
        try:
            from ai import laya_guard as lg
            _laya["guard"] = lg.LayaGuard(device=os.environ.get("AEI_LAYA_DEVICE", "cpu"))
        except Exception as err:  # noqa: BLE001  (import, download or load failures all mean "not available")
            _laya["error"] = type(err).__name__
    return _laya["guard"]


def laya_profile_entry() -> dict:
    from ai import laya_guard as lg
    fine_tuned = (lg.DEFAULT_MODEL_DIR / "model.safetensors").exists()
    return {"name": LAYA_PROFILE, "label": "Laya guard (" + ("fine-tuned" if fine_tuned else "zero-shot") + ", " + os.environ.get("AEI_LAYA_DEVICE", "cpu") + ")",
            "url": "in-process", "thinking": None, "ok": laya_available() and _laya["error"] is None,
            "model": str(lg.DEFAULT_MODEL_DIR.name) if fine_tuned else f"{lg.HUB_ID}/{lg.HUB_SUBFOLDER}", "timeout_ms": 8000}
app = FastAPI(title="TyksLab AEI ask endpoint", version="0.1.0", docs_url=None, redoc_url=None)
# The page is opened from file:// (origin "null") or a local http server; allow both.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"], allow_headers=["*"])
TABLES = router_rules.tables()


class Ask(BaseModel):
    text: str = Field(default="", max_length=2000)
    lang: str | None = None
    unit: str | None = None
    debug: bool = False     # return the full model request and response plus the decision trace
    model: str | None = None   # a profile name from nl2cube.PROFILES; default profile when absent


@app.get("/health")
def health():
    if NO_LLM:
        return {"status": "ok", "llm": {"ok": False, "disabled": True}, "profiles": [], "default": None}
    profiles = nl2cube.profiles_health() + [laya_profile_entry()]
    # The shipped decision model is the fine-tuned Laya guard; the llama.cpp profiles are optional.
    default_name = LAYA_PROFILE if laya_available() else nl2cube.DEFAULT_PROFILE
    default = next((p for p in profiles if p["name"] == default_name), profiles[0] if profiles else None)
    return {"status": "ok", "llm": default or {"ok": False}, "profiles": profiles, "default": default_name}


@app.post("/ask")
def ask(body: Ask):
    rules = router_rules.route(body.text, body.lang, TABLES, debug=body.debug)
    if NO_LLM:
        return dict(rules, source="rules-only", llm={"ok": False, "disabled": True})
    if body.model == LAYA_PROFILE or (body.model is None and laya_available()):
        guard = laya_guard()
        if guard is None:
            return nl2cube.combine(rules, {"ok": False, "error": "laya_unavailable:" + str(_laya["error"]), "latency_ms": 0})
        return nl2cube.combine(rules, guard.predict(body.text))
    profile = body.model if body.model in nl2cube.PROFILES else nl2cube.DEFAULT_PROFILE
    return nl2cube.combine(rules, nl2cube.extract(body.text, rules["lang"], tab=TABLES, debug=body.debug, profile=profile))

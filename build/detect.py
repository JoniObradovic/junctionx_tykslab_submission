"""Detectors over the whole dataset: data/*.csv -> web/alerts.json.

The scan walks the 156 weeks in 14-day steps so the timeline has
history. At every step end t:

- proportion drift: % R of each unit x organism x specimen x agent cell in the 4-week window
  (t - 4 wk, t] against the 52 weeks before the window, one-sided exact binomial test; the
  baseline comes from the unit when its count allows, else from the parent level;
- phenotype cluster: patients with the same organism and phenotype in one unit in the 14-day
  step window, Poisson excess over the unit's own rate in the preceding 52 weeks;
- always-alert: any CPE, VRE or meropenem-resistant Enterobacterales in the step window;
- data quality: EUCAST version mix, volume drop, AST completeness, unit-mapping gaps.

Every period is deduplicated to first isolates before counting (CLSI M39). Identifiers stay in
this process; alerts.json carries counts, rates, p-values and evidence sentences only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binom, poisson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # `python build/detect.py`
from build import config  # noqa: E402
from build.build_cube import (first_isolates, json_default, load_agents, load_ast,  # noqa: E402
                              load_isolates, reported_for, table)

DAY = pd.Timedelta(days=1)
WINDOW = pd.Timedelta(weeks=config.DETECTOR_WINDOW_WEEKS)
BASELINE = pd.Timedelta(weeks=config.BASELINE_WEEKS)
BASELINE_MIN = pd.Timedelta(weeks=config.BASELINE_MIN_WEEKS)
STEP = pd.Timedelta(days=config.SCAN_STEP_DAYS)
# The cluster window is evaluated once per step on the step grid, not as a sliding window; with
# CLUSTER_RULE["days"] equal to SCAN_STEP_DAYS the windows tile the timeline and every patient
# is counted once. A different value makes them overlap or leave gaps.
CLUSTER_DAYS = pd.Timedelta(days=config.CLUSTER_RULE["days"])
# p-values below the alert threshold print in scientific notation.
P_SCIENTIFIC_BELOW = min(config.DRIFT_ALERT["p_max"], config.CLUSTER_ALERT_P_MAX)
FIRST_ISOLATE = config.UI_LABELS["first_isolate_rule"]

CELL = ["unit", "organism", "specimen", "agent"]
PHENO = ["unit", "organism", "phenotype"]
EPISODE_KEYS = ["family", "unit", "organism", "specimen", "agent", "phenotype"]
GRADE_RANK = {g: i for i, g in enumerate(config.GRADES)}


# ---------------------------------------------------------------------------
# Scan grid and phenotypes
# ---------------------------------------------------------------------------

def scan_steps(data_start: pd.Timestamp, data_end: pd.Timestamp) -> list[pd.Timestamp]:
    """Step ends every SCAN_STEP_DAYS, anchored at the latest sample date and walking back
    to the first; the latest step is always the live one."""
    steps = []
    t = data_end
    while t > data_start:
        steps.append(t)
        t -= STEP
    return steps[::-1]


def phenotype_isolates(isolates: pd.DataFrame, ast: pd.DataFrame) -> pd.DataFrame:
    """One row per isolate and resistant phenotype it carries (config.PHENOTYPES)."""
    keep = ["isolate_id", "patient_id", "unit", "group", "organism", "specimen", "sample_date"]
    resistant = ast[ast["resistant"] == 1]
    parts = []
    for name, spec in config.PHENOTYPES.items():
        if "column" in spec:
            hit = isolates[isolates[spec["column"]] == 1]
        else:
            rule = spec["rule"]
            r_ids = resistant.loc[resistant["agent"] == rule["agent"], "isolate_id"]
            hit = isolates[isolates["organism"].isin(rule["organisms"])
                           & isolates["isolate_id"].isin(r_ids)]
        if "unless" in spec:
            hit = hit[hit[spec["unless"]] != 1]
        parts.append(hit[keep].assign(phenotype=name))
    return pd.concat(parts, ignore_index=True)


def _baseline_days(b0: pd.Timestamp, b1: pd.Timestamp, data_start: pd.Timestamp) -> int:
    """Days of data the baseline period (b0, b1] actually covers."""
    return max(0, (b1 - max(b0, data_start - DAY)).days)


def _rate(k, n):
    """Baseline rate with a Jeffreys pseudocount, so a zero baseline never gives p = 0."""
    pc = config.BASELINE_PSEUDOCOUNT
    return (k + pc) / (n + 2 * pc)


# ---------------------------------------------------------------------------
# Proportion drift
# ---------------------------------------------------------------------------

def _cell_counts(first: pd.DataFrame, ast: pd.DataFrame, reported: set, by: list[str]):
    joined = first.merge(ast, on="isolate_id", how="inner")
    pair = pd.MultiIndex.from_frame(joined[["agent", "specimen"]])
    joined = joined[pair.isin(reported)]
    return joined.groupby(by, observed=True).agg(n=("resistant", "size"),
                                                 n_r=("resistant", "sum")).reset_index()


def drift_step(isolates, ast, reported, step_end, data_start) -> pd.DataFrame:
    """Unit cells of the 4-week window ending at step_end with their baseline and test."""
    w0 = step_end - WINDOW
    window = first_isolates(isolates, start=w0, end=step_end)
    cells = _cell_counts(window, ast, reported, CELL)
    cells["pct_r"] = (100 * cells["n_r"] / cells["n"]).round(1)
    cells["step_end"] = step_end
    cells["period_start"], cells["period_end"] = w0 + DAY, step_end
    for col in ("baseline_level", "baseline_unit"):
        cells[col] = None
    for col in ("baseline_n", "baseline_n_r", "baseline_pct_r", "increase_pp", "p"):
        cells[col] = np.nan
    cells["grade"] = None
    if _baseline_days(w0 - BASELINE, w0, data_start) < BASELINE_MIN.days or cells.empty:
        return cells

    base = first_isolates(isolates, start=w0 - BASELINE, end=w0)
    at = {"unit": _cell_counts(base, ast, reported, CELL),
          "group": _cell_counts(base.assign(unit=base["group"]), ast, reported, CELL),
          "all": _cell_counts(base.assign(unit=config.ALL_UNITS["code"]), ast, reported, CELL)}
    cells["group"] = cells["unit"].map(config.GROUP_OF_UNIT)
    cells["all"] = config.ALL_UNITS["code"]
    for level, frame in at.items():
        frame = frame.rename(columns={"unit": level, "n": f"n_{level}", "n_r": f"n_r_{level}"})
        cells = cells.merge(frame, on=[level, "organism", "specimen", "agent"], how="left")
    # The baseline is the lowest level whose first-isolate count reaches DRIFT_BASELINE_N_MIN.
    enough = [cells[f"n_{lvl}"].fillna(0) >= config.DRIFT_BASELINE_N_MIN for lvl in at]
    levels = list(at)
    cells["baseline_level"] = np.select(enough, levels, default=None)
    for lvl in levels:
        pick = cells["baseline_level"] == lvl
        cells.loc[pick, "baseline_unit"] = cells.loc[pick, lvl]
        cells.loc[pick, "baseline_n"] = cells.loc[pick, f"n_{lvl}"]
        cells.loc[pick, "baseline_n_r"] = cells.loc[pick, f"n_r_{lvl}"]
    tested = cells["baseline_level"].notna()
    t = cells[tested]
    p0 = _rate(t["baseline_n_r"], t["baseline_n"])
    cells.loc[tested, "p"] = binom.sf(t["n_r"] - 1, t["n"], p0)      # P(X >= n_r)
    baseline_rate = t["baseline_n_r"] / t["baseline_n"]
    cells.loc[tested, "baseline_pct_r"] = (100 * baseline_rate).round(1)
    cells.loc[tested, "increase_pp"] = (100 * (t["n_r"] / t["n"] - baseline_rate)).round(1)
    alert = ((cells["n"] >= config.DRIFT_ALERT["n_min"]) & (cells["p"] < config.DRIFT_ALERT["p_max"])
             & (cells["increase_pp"] >= config.DRIFT_ALERT["min_increase_pp"]))
    watch = (cells["n"] >= config.DRIFT_WATCH["n_min"]) & (cells["p"] < config.DRIFT_WATCH["p_max"])
    cells["grade"] = np.select([alert, watch], ["alert", "watch"], default=None)
    drop = ["group", "all"] + [c for c in cells.columns if c.startswith(("n_unit", "n_group",
                                                                         "n_all", "n_r_"))]
    return cells.drop(columns=drop)


# ---------------------------------------------------------------------------
# Phenotype cluster and always-alert
# ---------------------------------------------------------------------------

def _patients(pheno: pd.DataFrame, start, end, name: str) -> pd.DataFrame:
    """Distinct patients per unit x organism x phenotype with a sample in (start, end]."""
    rows = pheno[(pheno["sample_date"] > start) & (pheno["sample_date"] <= end)]
    return rows.groupby(PHENO, observed=True)["patient_id"].nunique().rename(name).reset_index()


def cluster_step(pheno, step_end, data_start) -> pd.DataFrame:
    """Phenotype counts in the step window with the Poisson test and grade."""
    c0 = step_end - CLUSTER_DAYS
    counts = _patients(pheno, c0, step_end, "count")
    counts["step_end"] = step_end
    counts["period_start"], counts["period_end"] = c0 + DAY, step_end
    base_days = _baseline_days(c0 - BASELINE, c0, data_start)
    counts["baseline_count"] = np.nan
    counts["expected"], counts["p"] = np.nan, np.nan
    counts["grade"] = None
    if base_days < BASELINE_MIN.days or counts.empty:
        return counts
    base = _patients(pheno, c0 - BASELINE, c0, "baseline_count")
    counts = counts.drop(columns="baseline_count").merge(base, on=PHENO, how="left")
    counts["baseline_count"] = counts["baseline_count"].fillna(0)
    scale = CLUSTER_DAYS.days / base_days
    counts["expected"] = ((counts["baseline_count"] + config.BASELINE_PSEUDOCOUNT) * scale).round(2)
    counts["p"] = poisson.sf(counts["count"] - 1, counts["expected"])        # P(X >= count)
    alert = ((counts["count"] >= config.CLUSTER_RULE["min_count"])
             & (counts["p"] < config.CLUSTER_ALERT_P_MAX))
    watch = ((counts["count"] >= config.CLUSTER_WATCH["min_count"])
             & (counts["p"] < config.CLUSTER_WATCH["p_max"]))
    counts["grade"] = np.select([alert, watch], ["alert", "watch"], default=None)
    counts.loc[~counts["phenotype"].isin(config.CLUSTER_PHENOTYPES), "grade"] = None
    return counts


def always_alert(pheno_steps: pd.DataFrame) -> pd.DataFrame:
    """Every step window with at least one always-alert phenotype, graded always-alert.
    Counts are patients per window, so a carrier sampled in two windows is reported twice."""
    rows = pheno_steps[pheno_steps["phenotype"].isin(config.ALWAYS_ALERT)].copy()
    rows["grade"] = "always-alert"
    return rows


# ---------------------------------------------------------------------------
# Data quality (housekeeping)
# ---------------------------------------------------------------------------

def quality_step(isolates, ast, step_end, data_start) -> list[dict]:
    w0 = step_end - WINDOW
    window = first_isolates(isolates, start=w0, end=step_end)
    out = []
    common = {"step_end": step_end, "period_start": w0 + DAY, "period_end": step_end}

    results = window[["isolate_id", "organism"]].merge(ast, on="isolate_id", how="left")
    shares = results["eucast"].dropna().value_counts(normalize=True).sort_index()
    out.append({**common, "check": "eucast_mix", "unit": config.ALL_UNITS["code"],
                "value": len(shares), "expected": 1, "flag": len(shares) > 1,
                "detail": ", ".join(f"EUCAST {v} {100 * s:.0f} %" for v, s in shares.items())})

    with_ast = results.groupby("organism", observed=True)["agent"].agg(lambda s: s.notna().mean())
    low = with_ast[with_ast < config.DATA_QUALITY["ast_completeness_min"]]
    overall = results.drop_duplicates("isolate_id")["agent"].notna().mean() if len(results) else 1.0
    out.append({**common, "check": "ast_completeness", "unit": config.ALL_UNITS["code"],
                "value": round(100 * float(overall), 1),
                "expected": round(100 * config.DATA_QUALITY["ast_completeness_min"], 1),
                "flag": not low.empty,
                "detail": ", ".join(f"{o} {100 * v:.0f} %" for o, v in low.items())})

    base_days = _baseline_days(w0 - BASELINE, w0, data_start)
    if base_days >= 7 * config.DATA_QUALITY["volume_min_baseline_weeks"]:
        base = first_isolates(isolates, start=w0 - BASELINE, end=w0)
        scale = WINDOW.days / base_days
        units = list(config.GROUP_OF_UNIT) + [config.ALL_UNITS["code"]]
        for unit in units:
            n = len(window) if unit == config.ALL_UNITS["code"] else int((window["unit"] == unit).sum())
            b = len(base) if unit == config.ALL_UNITS["code"] else int((base["unit"] == unit).sum())
            expected = round(b * scale, 1)
            out.append({**common, "check": "volume_drop", "unit": unit, "value": n,
                        "expected": expected,
                        "flag": expected > 0 and n < (1 - config.DATA_QUALITY["volume_drop_fraction"]) * expected,
                        "detail": ""})
    return out


def unit_mapping_gap(data_dir=config.DATA_DIR, data_end=None) -> dict:
    """Ward codes in cultures.csv that the hierarchy in config does not know."""
    codes = pd.read_csv(data_dir / "cultures.csv", usecols=["ward_code"])["ward_code"]
    unknown = sorted(set(codes.dropna().unique()) - set(config.GROUP_OF_UNIT))
    return {"step_end": data_end, "period_start": None, "period_end": data_end,
            "check": "unit_mapping", "unit": config.ALL_UNITS["code"], "value": len(unknown),
            "expected": 0, "flag": bool(unknown), "detail": ", ".join(unknown)}


# ---------------------------------------------------------------------------
# Evidence sentences
# ---------------------------------------------------------------------------

def _num(x, digits=1, lang="en") -> str:
    s = f"{x:.{digits}f}"
    return s.replace(".", ",") if lang == "fi" else s


def _p(p, lang="en") -> str:
    if p is None or (isinstance(p, float) and np.isnan(p)):
        return ""
    s = f"{p:.1e}" if p < P_SCIENTIFIC_BELOW else f"{p:.3f}"
    return "p = " + (s.replace(".", ",") if lang == "fi" else s)


def _date(d, lang="en") -> str:
    d = pd.Timestamp(d)
    return f"{d.day}.{d.month}.{d.year}" if lang == "fi" else d.strftime("%Y-%m-%d")


def _period(start, end, lang="en") -> str:
    return (f"{_date(start, 'fi')}–{_date(end, 'fi')}" if lang == "fi"
            else f"{_date(start)} to {_date(end)}")


def _where(level, unit, lang) -> str:
    if level == "unit":
        return unit
    if level == "group":
        return config.GROUPS[unit][lang]
    return config.ALL_UNITS["short"]


def evidence_drift(r, agents: pd.DataFrame) -> tuple[str, str]:
    agent_fi, agent_en = agents.loc[r["agent"], "antibiotic_fi"], agents.loc[r["agent"], "antibiotic"]
    spec = config.SPECIMEN_LABELS[r["specimen"]]
    weeks = config.DETECTOR_WINDOW_WEEKS
    fi = (f"{r['unit']}, {r['organism']}, {spec['fi']}, {agent_fi}: resistenssi "
          f"{_num(r['pct_r'], 1, 'fi')} % R ({r['n_r']}/{r['n']} kantaa, {FIRST_ISOLATE['fi']}) "
          f"{weeks} viikon jaksolla {_period(r['period_start'], r['period_end'], 'fi')}, "
          f"edeltävien {config.BASELINE_WEEKS} viikon aikana {_num(r['baseline_pct_r'], 1, 'fi')} % "
          f"({_where(r['baseline_level'], r['baseline_unit'], 'fi')}, n = {int(r['baseline_n'])}); "
          f"nousu {_num(r['increase_pp'], 1, 'fi')} %-yksikköä, {_p(r['p'], 'fi')}.")
    en = (f"{r['unit']}, {r['organism']}, {spec['en']}, {agent_en}: "
          f"{_num(r['pct_r'])} % R ({r['n_r']} of {r['n']} isolates, {FIRST_ISOLATE['en']}) "
          f"in the {weeks}-week window {_period(r['period_start'], r['period_end'])}, "
          f"vs {_num(r['baseline_pct_r'])} % over the preceding {config.BASELINE_WEEKS} weeks "
          f"({_where(r['baseline_level'], r['baseline_unit'], 'en')}, n = {int(r['baseline_n'])}); "
          f"increase {_num(r['increase_pp'])} pp, {_p(r['p'])}.")
    return fi, en


def evidence_cluster(r) -> tuple[str, str]:
    ph = config.PHENOTYPES[r["phenotype"]]
    days = config.CLUSTER_RULE["days"]
    fi = (f"{r['unit']}: {config.FAMILY_LABELS['cluster']['fi']}, {ph['fi']}, {r['organism']}: "
          f"{int(r['count'])} kantaa ({FIRST_ISOLATE['fi']}) {days} päivässä {_period(r['period_start'], r['period_end'], 'fi')}, "
          f"odotettu {_num(r['expected'], 2, 'fi')} edeltävien {config.BASELINE_WEEKS} viikon "
          f"perusteella; {_p(r['p'], 'fi')}.")
    en = (f"{r['unit']}: suspected cluster, {ph['en']}, {r['organism']}: {int(r['count'])} "
          f"isolates ({FIRST_ISOLATE['en']}) in {days} days {_period(r['period_start'], r['period_end'])}, "
          f"{_num(r['expected'], 2)} expected from the preceding {config.BASELINE_WEEKS} weeks; "
          f"{_p(r['p'])}.")
    return fi, en


def evidence_always(r) -> tuple[str, str]:
    ph = config.PHENOTYPES[r["phenotype"]]
    n = int(r["count"])
    fi = (f"{r['unit']}: {ph['fi']}, {r['organism']}: {n} {'kanta' if n == 1 else 'kantaa'} "
          f"({FIRST_ISOLATE['fi']}) jaksolla {_period(r['period_start'], r['period_end'], 'fi')}; "
          f"{config.FAMILY_LABELS['always_alert']['fi']}, jokainen löydös hälyttää.")
    en = (f"{r['unit']}: {ph['en']}, {r['organism']}: {n} {'isolate' if n == 1 else 'isolates'} "
          f"({FIRST_ISOLATE['en']}) in {_period(r['period_start'], r['period_end'])}; "
          f"multidrug-resistant organism, every finding alerts.")
    return fi, en


def evidence_quality(r) -> tuple[str, str]:
    label = config.QUALITY_LABELS[r["check"]]
    where = r["unit"] if r["unit"] != config.ALL_UNITS["code"] else config.ALL_UNITS["short"]
    period_fi = _period(r["period_start"], r["period_end"], "fi") if r["period_start"] else ""
    period_en = _period(r["period_start"], r["period_end"]) if r["period_start"] else ""
    if r["check"] == "volume_drop":
        fi = (f"{where}: {label['fi']}: {int(r['value'])} kantaa jaksolla {period_fi}, odotettu noin "
              f"{_num(r['expected'], 0, 'fi')} edeltävien {config.BASELINE_WEEKS} viikon perusteella.")
        en = (f"{where}: {label['en']}: {int(r['value'])} isolates in {period_en}, about "
              f"{_num(r['expected'], 0)} expected from the preceding {config.BASELINE_WEEKS} weeks.")
    elif r["check"] == "eucast_mix":
        fi = f"{label['fi']} {period_fi}: {r['detail']}."
        en = f"{label['en']} {period_en}: {r['detail']}."
    elif r["check"] == "ast_completeness":
        fi = (f"{label['fi']} jaksolla {period_fi} {_num(r['value'], 1, 'fi')} %; alle "
              f"{_num(r['expected'], 0, 'fi')} %: {r['detail']}.")
        en = (f"{label['en']} in {period_en} {_num(r['value'])} %; below "
              f"{_num(r['expected'], 0)} %: {r['detail']}.")
    else:
        fi = f"{label['fi']}: {r['value']} ({r['detail']})." if r["value"] else f"{label['fi']}: 0."
        en = f"{label['en']}: {r['value']} ({r['detail']})." if r["value"] else f"{label['en']}: 0."
    return fi, en


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

ALERT_COLUMNS = ["id", "family", "grade", "step_end", "period_start", "period_end", "unit",
                 "organism", "specimen", "agent", "phenotype", "n", "n_r", "pct_r",
                 "baseline_level", "baseline_unit", "baseline_n", "baseline_pct_r",
                 "increase_pp", "count", "baseline_count", "expected", "p",
                 "evidence_fi", "evidence_en"]
EPISODE_COLUMNS = ["id", "family", "grade", "unit", "organism", "specimen", "agent", "phenotype",
                   "start", "end", "steps", "peak_step_end", "peak_n", "peak_pct_r",
                   "peak_count", "peak_p", "evidence_fi", "evidence_en"]
DRIFT_SERIES = ["step_end", "n", "n_r", "pct_r", "baseline_pct_r", "p", "grade"]
PHENO_SERIES = ["step_end", "count", "expected", "p", "grade"]
QUALITY_COLUMNS = ["step_end", "period_start", "period_end", "check", "unit", "value",
                   "expected", "flag", "detail", "evidence_fi", "evidence_en"]


class Scan:
    """Results of one scan: per-step findings, episodes, series for the episodes' keys, the
    data-quality rows, and the full per-step frames the findings were drawn from."""

    def __init__(self, alerts, episodes, series, quality, drift, pheno_steps, steps, data_start,
                 data_end):
        self.alerts = alerts
        self.episodes = episodes
        self.series = series
        self.quality = quality
        self.drift = drift          # every unit cell at every step, tested or not
        self.pheno_steps = pheno_steps    # every phenotype count at every step
        self.steps = steps
        self.data_start = data_start
        self.data_end = data_end


def _key_id(row) -> str:
    return "|".join(str(row[k]) if pd.notna(row[k]) else "" for k in EPISODE_KEYS)


def episodes(alerts: pd.DataFrame) -> pd.DataFrame:
    """Consecutive steps that fire for the same key are one episode; its grade is the highest
    and its evidence comes from the peak step (highest grade, then smallest p)."""
    if alerts.empty:
        return pd.DataFrame(columns=EPISODE_COLUMNS)
    a = alerts.sort_values(["id", "step_end"]).copy()
    a["rank"] = a["grade"].map(GRADE_RANK)
    gap = a.groupby("id")["step_end"].diff() > STEP
    a["episode"] = gap.cumsum() + (a["id"] != a["id"].shift()).cumsum()
    rows = []
    for _, g in a.groupby("episode", sort=False):
        peak = g.sort_values(["rank", "p"], ascending=[False, True]).iloc[0]
        rows.append({"id": peak["id"], "family": peak["family"], "grade": peak["grade"],
                     "unit": peak["unit"], "organism": peak["organism"],
                     "specimen": peak["specimen"], "agent": peak["agent"],
                     "phenotype": peak["phenotype"],
                     "start": g["period_start"].min(), "end": g["period_end"].max(),
                     "steps": len(g), "peak_step_end": peak["step_end"], "peak_n": peak["n"],
                     "peak_pct_r": peak["pct_r"], "peak_count": peak["count"],
                     "peak_p": peak["p"], "evidence_fi": peak["evidence_fi"],
                     "evidence_en": peak["evidence_en"]})
    out = pd.DataFrame(rows)
    for col in ("start", "end", "peak_step_end"):
        out[col] = out[col].astype(str).str[:10]
    return out.sort_values(["start", "id"]).reset_index(drop=True)[EPISODE_COLUMNS]


def series_for(episodes: pd.DataFrame, drift: pd.DataFrame, pheno_steps: pd.DataFrame,
               steps: list[pd.Timestamp]) -> list[dict]:
    """Per-step history for every episode key, so the UI can draw a timeline per alert."""
    out = []
    step_index = pd.DataFrame({"step_end": steps})
    for key_id, g in episodes.groupby("id", sort=False):
        head = g.iloc[0]
        if head["family"] == "drift":
            s = drift[(drift["unit"] == head["unit"]) & (drift["organism"] == head["organism"])
                      & (drift["specimen"] == head["specimen"]) & (drift["agent"] == head["agent"])]
            s = step_index.merge(s[DRIFT_SERIES], on="step_end", how="left")
            cols = DRIFT_SERIES
        else:
            s = pheno_steps[(pheno_steps["unit"] == head["unit"])
                            & (pheno_steps["organism"] == head["organism"])
                            & (pheno_steps["phenotype"] == head["phenotype"])]
            s = step_index.merge(s[PHENO_SERIES], on="step_end", how="left")
            s["count"] = s["count"].fillna(0).astype(int)
            if head["family"] == "always_alert":
                s["grade"] = np.where(s["count"] > 0, "always-alert", None)
            cols = PHENO_SERIES
        s = s.copy()
        s["step_end"] = s["step_end"].astype(str).str[:10]
        out.append({"id": key_id, "family": head["family"], **table(s, cols)})
    return out


def scan(isolates: pd.DataFrame, ast: pd.DataFrame, agents: pd.DataFrame | None = None,
         data_dir=config.DATA_DIR) -> Scan:
    agents = load_agents(data_dir) if agents is None else agents
    reported = reported_for(agents)
    data_start, data_end = isolates["sample_date"].min(), isolates["sample_date"].max()
    steps = scan_steps(data_start, data_end)
    phenotype_rows = phenotype_isolates(isolates, ast)

    drift_frames, pheno_frames, quality_rows = [], [], []
    for step_end in steps:
        drift_frames.append(drift_step(isolates, ast, reported, step_end, data_start))
        pheno_frames.append(cluster_step(phenotype_rows, step_end, data_start))
        quality_rows.extend(quality_step(isolates, ast, step_end, data_start))
    quality_rows.append(unit_mapping_gap(data_dir, data_end))
    drift = pd.concat(drift_frames, ignore_index=True)
    pheno_steps = pd.concat(pheno_frames, ignore_index=True)

    d = drift[drift["grade"].notna()].copy()
    d["family"] = "drift"
    d[["evidence_fi", "evidence_en"]] = [evidence_drift(r, agents) for _, r in d.iterrows()] \
        if not d.empty else None
    c = pheno_steps[pheno_steps["grade"].notna()].copy()
    c["family"] = "cluster"
    c[["evidence_fi", "evidence_en"]] = [evidence_cluster(r) for _, r in c.iterrows()] \
        if not c.empty else None
    a = always_alert(pheno_steps)
    a["family"] = "always_alert"
    a[["evidence_fi", "evidence_en"]] = [evidence_always(r) for _, r in a.iterrows()] \
        if not a.empty else None

    alerts = pd.concat([d, c, a], ignore_index=True)
    for col in ALERT_COLUMNS:
        if col not in alerts.columns:
            alerts[col] = np.nan
    alerts["id"] = alerts.apply(_key_id, axis=1)
    alerts = alerts[ALERT_COLUMNS].sort_values(["step_end", "family", "id"]).reset_index(drop=True)

    quality = pd.DataFrame(quality_rows)
    counts_not_shares = quality["check"] != "ast_completeness"
    quality["value"] = quality["value"].astype(object)
    quality.loc[counts_not_shares, "value"] = quality.loc[counts_not_shares, "value"].astype(int)
    flagged = quality["flag"]
    quality["evidence_fi"], quality["evidence_en"] = None, None
    texts = [evidence_quality(r) for _, r in quality[flagged].iterrows()]
    if texts:
        quality.loc[flagged, ["evidence_fi", "evidence_en"]] = texts
    quality = quality[QUALITY_COLUMNS]

    eps = episodes(alerts)
    return Scan(alerts, eps, series_for(eps, drift, pheno_steps, steps), quality, drift, pheno_steps,
                steps, data_start, data_end)


# ---------------------------------------------------------------------------
# web/alerts.json
# ---------------------------------------------------------------------------

INTEGER_COLUMNS = ("n", "n_r", "baseline_n", "count", "baseline_count", "peak_n", "peak_count",
                   "steps")


def _for_json(df: pd.DataFrame) -> pd.DataFrame:
    """Dates as ISO strings; counts as integers even where NaN made the column float."""
    df = df.copy()
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            df[col] = df[col].dt.strftime("%Y-%m-%d")
        elif col in INTEGER_COLUMNS:
            df[col] = df[col].astype("Int64")
    return df


def build_meta(result: Scan) -> dict:
    by = lambda df: {f: g["grade"].value_counts().to_dict()   # noqa: E731
                     for f, g in df.groupby("family", sort=False)} if not df.empty else {}
    return {
        "generated": pd.Timestamp.now(tz="Europe/Helsinki").isoformat(timespec="seconds"),
        "synthetic": True,
        "data_start": str(result.data_start)[:10],
        "data_end": str(result.data_end)[:10],
        "step_days": config.SCAN_STEP_DAYS,
        "steps": [str(s)[:10] for s in result.steps],
        "detector_window_weeks": config.DETECTOR_WINDOW_WEEKS,
        "baseline_weeks": config.BASELINE_WEEKS,
        "baseline_min_weeks": config.BASELINE_MIN_WEEKS,
        "baseline_pseudocount": config.BASELINE_PSEUDOCOUNT,
        "drift": {"baseline_n_min": config.DRIFT_BASELINE_N_MIN, "watch": config.DRIFT_WATCH,
                  "alert": config.DRIFT_ALERT, "test": "one-sided exact binomial, % R increase"},
        "cluster": {"rule": config.CLUSTER_RULE, "alert_p_max": config.CLUSTER_ALERT_P_MAX,
                    "watch": config.CLUSTER_WATCH, "phenotypes": list(config.CLUSTER_PHENOTYPES),
                    "test": f"Poisson excess of the {config.CLUSTER_RULE['days']}-day step count over "
                            f"the unit's preceding {config.BASELINE_WEEKS}-week rate; the count rule "
                            f"is the floor, the tail probability sets the grade"},
        "always_alert": list(config.ALWAYS_ALERT),
        "recent_weeks": config.RECENT_FINDING_WEEKS,
        "injected_events": [dict(e) for e in config.INJECTED_EVENTS],
        "phenotypes": {k: {"fi": v["fi"], "en": v["en"]} for k, v in config.PHENOTYPES.items()},
        "grades": config.GRADE_LABELS,
        "families": config.FAMILY_LABELS,
        "quality_checks": config.QUALITY_LABELS,
        "data_quality": config.DATA_QUALITY,
        "quality_not_computed": config.QUALITY_NOT_COMPUTED,
        "counts": {"alerts": by(result.alerts), "episodes": by(result.episodes),
                   "quality_flagged": int(result.quality["flag"].sum())},
    }


def write_alerts_json(result: Scan, path=config.ALERTS_JSON) -> dict:
    """Write findings, episodes, series and data-quality rows. Never an identifier."""
    payload = {
        "meta": build_meta(result),
        "alerts": table(_for_json(result.alerts), ALERT_COLUMNS),
        "episodes": table(_for_json(result.episodes), EPISODE_COLUMNS),
        "series": result.series,
        "quality": table(_for_json(result.quality), QUALITY_COLUMNS),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                               default=json_default), encoding="utf-8")
    return payload


def summary(result: Scan) -> str:
    """Counts only, for the terminal."""
    lines = [f"scan {str(result.data_start)[:10]} to {str(result.data_end)[:10]}: "
             f"{len(result.steps)} steps of {config.SCAN_STEP_DAYS} days"]
    for family, g in result.alerts.groupby("family", sort=False):
        grades = ", ".join(f"{k} {v}" for k, v in g["grade"].value_counts().items())
        lines.append(f"  {family:<13} step findings: {grades}")
    for family, g in result.episodes.groupby("family", sort=False):
        grades = ", ".join(f"{k} {v}" for k, v in g["grade"].value_counts().items())
        lines.append(f"  {family:<13} episodes: {grades}")
    e = result.episodes
    alerts = e[e["grade"] == "alert"]
    lines.append("alert-grade episodes (unit, start, key):")
    for _, r in alerts.iterrows():
        what = r["agent"] if r["family"] == "drift" else r["phenotype"]
        lines.append(f"  {r['family']:<8} {r['unit']:<6} {r['start']} to {r['end']}  "
                     f"{r['organism']} {r['specimen'] if pd.notna(r['specimen']) else ''} {what}")
    q = result.quality
    lines.append("data quality flags: " + ", ".join(
        f"{k} {v}" for k, v in q[q["flag"]]["check"].value_counts().items()))
    return "\n".join(lines)


def main():
    isolates = load_isolates()
    result = scan(isolates, load_ast())
    write_alerts_json(result)
    print(summary(result))
    print(f"wrote {config.ALERTS_JSON.relative_to(config.ROOT)} "
          f"({config.ALERTS_JSON.stat().st_size / 1024:.0f} kB)")


if __name__ == "__main__":
    main()

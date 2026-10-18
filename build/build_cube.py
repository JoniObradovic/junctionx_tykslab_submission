"""Build the antibiogram cube: data/*.csv -> web/data.json.

Steps: first isolate per patient x organism x specimen x window (CLSI M39),
intrinsic-R exclusion, reported-for filter, cells over windows 4/8/13/26/52 weeks and calendar
years at unit, group and all levels with Wilson intervals, honest scope (adaptive window plus
hierarchical fallback) with the reason recorded, small-cell suppression at n < k.

Patient and culture identifiers are used only inside this process. The JSON written to web/
holds counts and percentages only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # `python build/build_cube.py`
from build import config  # noqa: E402


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_isolates(data_dir=config.DATA_DIR) -> pd.DataFrame:
    """One row per isolate with its culture context, patient sex and onset class.

    Columns kept: isolate_id, patient_id, organism, specimen, unit, group, sample_date, sex,
    onset, plus the phenotype flags. Identifiers stay inside the process.
    """
    cultures = pd.read_csv(
        data_dir / "cultures.csv",
        usecols=["culture_id", "patient_id", "sample_date", "ward_code", "ward_type",
                 "admission_date", "specimen"],
        parse_dates=["sample_date", "admission_date"],
    )
    isolates = pd.read_csv(data_dir / "isolates.csv",
                           usecols=["isolate_id", "culture_id", "organism", "esbl", "mrsa",
                                    "vre", "cpe"])
    patients = pd.read_csv(data_dir / "patients.csv", usecols=["patient_id", "sex"])

    df = isolates.merge(cultures, on="culture_id", how="inner", validate="many_to_one")
    df = df.merge(patients, on="patient_id", how="left", validate="many_to_one")

    days_in = (df["sample_date"] - df["admission_date"]).dt.days
    hospital_onset = (df["ward_type"] == "in") & (days_in >= config.HOSPITAL_ONSET_MIN_DAYS)
    df["onset"] = hospital_onset.map({True: "hospital", False: "community"})
    df["unit"] = df["ward_code"]
    df["group"] = df["unit"].map(config.GROUP_OF_UNIT)
    if df["group"].isna().any():
        unknown = sorted(df.loc[df["group"].isna(), "unit"].unique())
        raise ValueError(f"units missing from config.GROUP_OF_UNIT: {unknown}")

    cols = ["isolate_id", "patient_id", "organism", "specimen", "unit", "group",
            "sample_date", "sex", "onset", "esbl", "mrsa", "vre", "cpe"]
    return df[cols].sort_values(["sample_date", "isolate_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Windows and the first-isolate rule
# ---------------------------------------------------------------------------

def window_bounds(end: pd.Timestamp, window_weeks: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Trailing window of whole weeks ending on `end`, inclusive: (end - 7w, end]."""
    return end - pd.Timedelta(weeks=window_weeks), end


def first_isolates(isolates: pd.DataFrame, window_weeks: int | None = None,
                   start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
                   ) -> pd.DataFrame:
    """Isolates in the analysis period reduced to the first per patient x organism x specimen.

    Give either `window_weeks` (trailing window ending at the latest sample date, or at `end`)
    or an explicit `start` (exclusive) and `end` (inclusive). The result's `attrs` carries
    `n_before_dedup`, `start` and `end`.
    """
    if end is None:
        end = isolates["sample_date"].max()
    if window_weeks is not None:
        start, end = window_bounds(end, window_weeks)
    if start is None:
        raise ValueError("give window_weeks or start")
    in_period = isolates[(isolates["sample_date"] > start) & (isolates["sample_date"] <= end)]
    first = (in_period.sort_values(["sample_date", "isolate_id"])
             .drop_duplicates(list(config.FIRST_ISOLATE_KEYS), keep="first"))
    first = first.reset_index(drop=True)
    first.attrs.update(n_before_dedup=len(in_period), start=start, end=end)
    return first


def load_ast(data_dir=config.DATA_DIR) -> pd.DataFrame:
    """AST results after the lab's exclusions: intrinsic-R pairs dropped, agents kept only for
    the specimen groups they are reported for. Columns: isolate_id, agent, resistant, eucast."""
    ast = pd.read_csv(data_dir / "susceptibility.csv",
                      usecols=["isolate_id", "organism", "antibiotic_code", "result", "guideline"])
    ast = ast.rename(columns={"antibiotic_code": "agent"})
    pair = pd.MultiIndex.from_frame(ast[["organism", "agent"]])
    ast = ast[~pair.isin(config.INTRINSIC_R)]
    ast["resistant"] = (ast["result"] == "R").astype("int64")
    ast["eucast"] = ast["guideline"].str.extract(r"(\d{4})")[0]
    return ast[["isolate_id", "agent", "resistant", "eucast"]].reset_index(drop=True)


def load_agents(data_dir=config.DATA_DIR) -> pd.DataFrame:
    """The lab's reporting list from antibiotics.csv, indexed by agent code."""
    agents = pd.read_csv(data_dir / "antibiotics.csv").rename(columns={"antibiotic_code": "agent"})
    return agents.set_index("agent")


def load_units(data_dir=config.DATA_DIR) -> pd.DataFrame:
    return pd.read_csv(data_dir / "wards.csv").rename(columns={"ward_code": "unit"}).set_index("unit")


def reported_for(agents: pd.DataFrame) -> set[tuple[str, str]]:
    """(agent, specimen) pairs the lab reports."""
    pairs = set()
    for agent, row in agents.iterrows():
        specimens = config.SPECIMENS if row["reported_for"] == "all" else (row["reported_for"],)
        pairs.update((agent, s) for s in specimens)
    return pairs


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def wilson(successes, n, level: float = config.CI_LEVEL) -> tuple:
    """Wilson score interval for a proportion; works on scalars or aligned arrays."""
    z = norm.ppf(1 - (1 - level) / 2)
    n = np.asarray(n, dtype=float)
    p = np.asarray(successes, dtype=float) / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = np.where(p == 0, 0.0, np.clip(centre - half, 0, 1))
    hi = np.where(p == 1, 1.0, np.clip(centre + half, 0, 1))
    if lo.ndim == 0:
        return float(lo), float(hi)
    return lo, hi


# ---------------------------------------------------------------------------
# Periods: the window ladder and calendar years
# ---------------------------------------------------------------------------

def windows(isolates: pd.DataFrame) -> dict[str, tuple[pd.Timestamp, pd.Timestamp]]:
    """Window bounds keyed by label: '4w' .. '52w' trailing from the latest sample date, and
    'Y2024' style calendar years for the annual view (the first and last year may be partial).
    Bounds are (start exclusive, end inclusive)."""
    end = isolates["sample_date"].max()
    out = {f"{w}w": window_bounds(end, w) for w in config.WINDOW_LADDER_WEEKS}
    for year in range(isolates["sample_date"].min().year, end.year + 1):
        y0 = pd.Timestamp(year=year, month=1, day=1) - pd.Timedelta(days=1)
        y1 = min(pd.Timestamp(year=year, month=12, day=31), end)
        out[f"Y{year}"] = (y0, y1)
    return out


# ---------------------------------------------------------------------------
# The cube
# ---------------------------------------------------------------------------

CELL_KEYS = ["window", "level", "unit", "stratum", "specimen", "organism"]


def _levelled(first: pd.DataFrame) -> pd.DataFrame:
    """Stack the first isolates once per hierarchy level with `unit` set to the level's code."""
    at_unit = first.assign(level="unit")
    at_group = first.assign(level="group", unit=first["group"])
    at_all = first.assign(level="all", unit=config.ALL_UNITS["code"])
    return pd.concat([at_unit, at_group, at_all], ignore_index=True)


def _stratified(levelled: pd.DataFrame) -> pd.DataFrame:
    """Stack once per stratum: all isolates, then by sex, then by onset.

    Stratification follows the first-isolate rule, so the stratum sees the patient's first
    isolate of the window and its onset class, not a later isolate of the same organism."""
    parts = [levelled.assign(stratum="all")]
    urine = levelled[levelled["specimen"].isin(config.SEX_STRATA_SPECIMENS)]
    for sex in config.SEX_STRATA:
        parts.append(urine[urine["sex"] == sex].assign(stratum=sex))
    for onset in config.ONSET_STRATA:
        parts.append(levelled[levelled["onset"] == onset].assign(stratum=onset))
    return pd.concat(parts, ignore_index=True)


class Cube:
    """Cells (organism x agent) and first-isolate counts (organism) for every window, level,
    unit and stratum. `cells` and `counts` are plain frames; `windows` maps labels to bounds."""

    def __init__(self, cells: pd.DataFrame, counts: pd.DataFrame, windows: dict,
                 dedup: dict, data_start: pd.Timestamp, data_end: pd.Timestamp,
                 panels: dict[str, list[str]]):
        self.cells = cells
        self.counts = counts
        self.windows = windows
        self.dedup = dedup
        self.data_start = data_start
        self.data_end = data_end
        self.panels = panels      # organism -> agents tested, after intrinsic-R exclusion


def build_cube(isolates: pd.DataFrame, ast: pd.DataFrame,
               agents: pd.DataFrame | None = None) -> Cube:
    if agents is None:
        agents = load_agents()
    reported = reported_for(agents)
    cell_frames, count_frames, dedup = [], [], {}
    bounds = windows(isolates)
    for label, (start, end) in bounds.items():
        first = first_isolates(isolates, start=start, end=end)
        dedup[label] = {"before": int(first.attrs["n_before_dedup"]), "after": int(len(first))}
        stacked = _stratified(_levelled(first))
        stacked["window"] = label

        counts = stacked.groupby(CELL_KEYS, observed=True).size().rename("n").reset_index()
        count_frames.append(counts)

        joined = stacked.merge(ast, on="isolate_id", how="inner")
        pair = pd.MultiIndex.from_frame(joined[["agent", "specimen"]])
        joined = joined[pair.isin(reported)]
        by_cell = joined.groupby(CELL_KEYS + ["agent"], observed=True)
        cells = by_cell.agg(n=("resistant", "size"), n_r=("resistant", "sum")).reset_index()
        versions = (by_cell["eucast"].agg(lambda s: sorted(set(s))).rename("eucast_versions")
                    .reset_index())
        cells = cells.merge(versions, on=CELL_KEYS + ["agent"])
        cell_frames.append(cells)

    cells = pd.concat(cell_frames, ignore_index=True)
    n_s = cells["n"] - cells["n_r"]
    lo, hi = wilson(n_s, cells["n"])
    cells["pct_s"] = (100 * n_s / cells["n"]).round(1)
    cells["pct_r"] = (100 * cells["n_r"] / cells["n"]).round(1)
    cells["ci_s_lo"], cells["ci_s_hi"] = (100 * lo).round(1), (100 * hi).round(1)
    cells["eucast_mixed"] = cells["eucast_versions"].str.len() > 1
    counts = pd.concat(count_frames, ignore_index=True)
    panels = (ast.merge(isolates[["isolate_id", "organism"]], on="isolate_id")
              .groupby("organism")["agent"].agg(lambda s: sorted(set(s))).to_dict())
    return Cube(cells, counts, bounds, dedup, isolates["sample_date"].min(),
                isolates["sample_date"].max(), panels)


# ---------------------------------------------------------------------------
# Honest scope: adaptive window, then hierarchical fallback
# ---------------------------------------------------------------------------

LADDER = [f"{w}w" for w in config.WINDOW_LADDER_WEEKS]
HEADLINE_WINDOW = f"{config.HEADLINE_WINDOW_WEEKS}w"

# Scope reasons and their one-line explanations. Placeholders: weeks, n, where (level name).
REASONS = {
    "own": ("oma yksikkö, {weeks} vk, n = {n}", "own unit, {weeks} wk, n = {n}"),
    "widened": ("laajennettu {weeks} viikkoon, n = {n}", "widened to {weeks} wk, n = {n}"),
    "fallback": ("{where}, {weeks} vk, n = {n}", "{where}, {weeks} wk, n = {n}"),
    "below_n_min": ("{where}, {weeks} vk, n = {n} alle N_min " + str(config.N_MIN),
                    "{where}, {weeks} wk, n = {n} below N_min " + str(config.N_MIN)),
    "suppressed": ("ei esitetä: testattujen kantojen määrä pieni (alle " + str(config.K_SUPPRESS)
                   + ") myös tasolla {where}, {weeks} vk",
                   "not shown: fewer than " + str(config.K_SUPPRESS)
                   + " tested isolates even at {where}, {weeks} wk"),
}


def _reason_text(reason: str, window: str, level: str, unit_used: str, n) -> tuple[str, str]:
    weeks = window.rstrip("w")
    where_fi, where_en = _where(level, unit_used)
    fi, en = REASONS[reason]
    return (fi.format(weeks=weeks, n=n, where=where_fi),
            en.format(weeks=weeks, n=n, where=where_en))


def _where(level: str, unit_used: str) -> tuple[str, str]:
    if level == "unit":
        return unit_used, unit_used
    if level == "group":
        g = config.GROUPS[unit_used]
        return g["fi"], g["en"]
    return config.ALL_UNITS["short"], config.ALL_UNITS["short"]


def honest_scope(cube: Cube) -> pd.DataFrame:
    """For every unit x specimen x organism x stratum, the smallest window and lowest level
    whose first-isolate count reaches N_min: unit 4 to 52 weeks, then group, then all.

    One row per combination with window_used, level_used, unit_used, n, reason and the
    reason in Finnish and English. Combinations are those where the organism occurs in that
    specimen group anywhere in the trailing 52 weeks.
    """
    counts = cube.counts
    ladder_counts = counts[counts["window"].isin(LADDER)]
    lookup = ladder_counts.set_index(CELL_KEYS)["n"].to_dict()   # key order is CELL_KEYS

    def n_at(window, level, code, stratum, specimen, organism):
        return lookup.get((window, level, code, stratum, specimen, organism), 0)

    whole_52w = counts[(counts["level"] == "all") & (counts["window"] == LADDER[-1])]
    present = whole_52w[["stratum", "specimen", "organism"]].drop_duplicates()

    rows = []
    for unit, group in config.GROUP_OF_UNIT.items():
        path = [("unit", unit), ("group", group), ("all", config.ALL_UNITS["code"])]
        for stratum, specimen, organism in present.itertuples(index=False):
            chosen = None
            for level, code in path:
                for window in LADDER:
                    n = n_at(window, level, code, stratum, specimen, organism)
                    if n >= config.N_MIN:
                        chosen = (level, code, window, n)
                        break
                if chosen:
                    break
            if chosen:
                level, code, window, n = chosen
                if level == "unit":
                    reason = "own" if window == HEADLINE_WINDOW else "widened"
                else:
                    reason = "fallback"
            else:
                level, code, window = path[-1][0], path[-1][1], LADDER[-1]
                n = n_at(window, level, code, stratum, specimen, organism)
                reason = "below_n_min" if n >= config.K_SUPPRESS else "suppressed"
            fi, en = _reason_text(reason, window, level, code, n)
            rows.append({"unit": unit, "specimen": specimen, "organism": organism,
                         "stratum": stratum, "window_used": window, "level_used": level,
                         "unit_used": code, "n": int(n), "reason": reason,
                         "reason_fi": fi, "reason_en": en})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# web/data.json
# ---------------------------------------------------------------------------

CELL_COLUMNS = ["window", "level", "unit", "stratum", "specimen", "organism", "agent",
                "n", "n_r", "pct_s", "pct_r", "ci_s_lo", "ci_s_hi", "eucast_versions",
                "eucast_mixed"]
SCOPE_COLUMNS = ["unit", "specimen", "organism", "stratum", "window_used", "level_used",
                 "unit_used", "n", "reason", "reason_fi", "reason_en"]
COUNT_COLUMNS = CELL_KEYS + ["n"]


def _suppress_cells(cells: pd.DataFrame) -> pd.DataFrame:
    """Cells with n < k are not written at all. The reader tells a suppressed cell from an
    unreported one by meta.panels (agents tested per organism) and meta.agents.reported_for:
    an agent on both lists with no row has fewer than k first isolates."""
    return cells[cells["n"] >= config.K_SUPPRESS]


def table(df: pd.DataFrame, columns: list[str]) -> dict:
    df = df[columns]
    rows = df.astype(object).where(df.notna(), None).values.tolist()
    return {"columns": columns, "rows": rows}


def coverage_table(cube: Cube) -> dict:
    """coverage table recomputed: ward x specimen x organism combos per window."""
    out = {}
    c = cube.counts
    ward = c[(c["level"] == "unit") & (c["stratum"] == "all")]
    for window in LADDER:
        w = ward[ward["window"] == window]
        out[window] = {"present": int(len(w)),
                       "n_ge_n_min": int((w["n"] >= config.N_MIN).sum())}
        for threshold in config.COVERAGE_THRESHOLDS:
            out[window][f"n_ge_{threshold}"] = int((w["n"] >= threshold).sum())
    return out


def cells_by_window(cube: Cube) -> dict:
    out = {}
    small = cube.cells["n"] < config.K_SUPPRESS
    for window, g in cube.cells.groupby("window", sort=False):
        out[window] = {}
        for level in config.LEVELS:
            at = g["level"] == level
            total = int(at.sum())
            sup = int((at & small).sum())
            out[window][level] = {"computed": total, "written": total - sup,
                                  "suppressed_pct": 100 * sup / total if total else 0.0}
    return out


def build_meta(cube: Cube, scopes: pd.DataFrame, agents: pd.DataFrame,
               units: pd.DataFrame) -> dict:
    years = sorted(label for label in cube.windows if label.startswith("Y"))
    complete_years = [y for y in years
                      if cube.windows[y][0] + pd.Timedelta(days=1) >= cube.data_start
                      and cube.windows[y][1] == pd.Timestamp(year=int(y[1:]), month=12, day=31)]
    return {
        "generated": pd.Timestamp.now(tz="Europe/Helsinki").isoformat(timespec="seconds"),
        "synthetic": True,
        "data_start": str(cube.data_start)[:10],
        "data_end": str(cube.data_end)[:10],
        "headline": config.HEADLINE,
        "n_min": config.N_MIN,
        "k_suppress": config.K_SUPPRESS,
        "ci_level": config.CI_LEVEL,
        "window_ladder": LADDER,
        "headline_window": HEADLINE_WINDOW,
        "annual_baseline_window": complete_years[-1] if complete_years else years[-1],
        "windows": {label: {"start": str(max(start + pd.Timedelta(days=1), cube.data_start))[:10],
                            "end": str(end)[:10], "complete": label in complete_years
                            or not label.startswith("Y")}
                    for label, (start, end) in cube.windows.items()},
        "levels": list(config.LEVELS),
        "units": {code: {"fi": units.loc[code, "ward_fi"], "en": units.loc[code, "ward_en"],
                         "group": group, "type": units.loc[code, "ward_type"]}
                  for code, group in config.GROUP_OF_UNIT.items()},
        "groups": config.GROUPS,
        "all": config.ALL_UNITS,
        "default_unit": config.DEFAULT_UNIT,
        "default_specimen": config.DEFAULT_SPECIMEN,
        "specimens": config.SPECIMEN_LABELS,
        "strata": config.STRATUM_LABELS,
        "labels": config.UI_LABELS,
        "sex_strata_specimens": list(config.SEX_STRATA_SPECIMENS),
        "sex_strata": list(config.SEX_STRATA),
        "coverage_thresholds": list(config.COVERAGE_THRESHOLDS),
        "hospital_onset_min_days": config.HOSPITAL_ONSET_MIN_DAYS,
        "agents": {code: {"fi": row["antibiotic_fi"], "en": row["antibiotic"],
                          "class": row["class"], "reported_for": row["reported_for"]}
                   for code, row in agents.iterrows()},
        "organisms": sorted(cube.counts["organism"].unique()),
        "panels": cube.panels,
        "intrinsic_r": [list(p) for p in config.INTRINSIC_R],
        "suppression_rule": {
            "fi": f"Solu, jonka mikrobin paneelissa ja näytetyypin raportointilistalla oleva "
                  f"mikrobilääke puuttuu taulukosta, ei esitetä: testattujen kantojen määrä "
                  f"pieni (alle {config.K_SUPPRESS}).",
            "en": f"A cell missing from the table while the agent is on the organism's panel "
                  f"and reported for the specimen group is suppressed: fewer than "
                  f"{config.K_SUPPRESS} first isolates."},
        "dedup": cube.dedup,
        "coverage": coverage_table(cube),
        "cells_total": int(len(cube.cells)),
        "cells_suppressed": int((cube.cells["n"] < config.K_SUPPRESS).sum()),
        "cells_by_window": cells_by_window(cube),
        "scopes_by_reason": scopes["reason"].value_counts().to_dict(),
    }


def write_data_json(cube: Cube, scopes: pd.DataFrame, path=config.DATA_JSON,
                    agents: pd.DataFrame | None = None, units: pd.DataFrame | None = None):
    """Write the cube as counts and percentages only. Never an identifier."""
    agents = load_agents() if agents is None else agents
    units = load_units() if units is None else units
    cells = _suppress_cells(cube.cells)
    counts = cube.counts[cube.counts["n"] >= config.K_SUPPRESS]
    scopes_out = scopes.astype({"n": "object"})
    scopes_out.loc[scopes_out["reason"] == "suppressed", "n"] = None
    payload = {
        "meta": build_meta(cube, scopes, agents, units),
        "cells": table(cells, CELL_COLUMNS),
        "scopes": table(scopes_out, SCOPE_COLUMNS),
        "counts": table(counts, COUNT_COLUMNS),
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                               default=json_default), encoding="utf-8")
    return payload


def json_default(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def summary(payload: dict) -> str:
    """The data.json summary: counts and aggregates only."""
    meta = payload["meta"]
    lines = [f"data {meta['data_start']} to {meta['data_end']}, N_min {meta['n_min']}, "
             f"k {meta['k_suppress']}, headline %{meta['headline']}",
             "dedup (isolates before -> first isolates):"]
    for label, d in meta["dedup"].items():
        lines.append(f"  {label:>6}: {d['before']:>6} -> {d['after']:>6}")
    lines.append("coverage, ward x specimen x organism:")
    t1, t2 = config.COVERAGE_THRESHOLDS
    lines.append(f"  window  present  n>={t1}  n>={t2}")
    for w, c in meta["coverage"].items():
        lines.append(f"  {w:>6}  {c['present']:>7}  {c[f'n_ge_{t1}']:>5}  {c[f'n_ge_{t2}']:>5}")
    lines.append("cells per window and level: written (suppressed share of all computed):")
    for window, levels in meta["cells_by_window"].items():
        parts = [f"{level} {c['written']} ({c['suppressed_pct']:.0f} %)"
                 for level, c in levels.items()]
        lines.append(f"  {window:>6}: " + ", ".join(parts))
    lines.append(f"cells computed {meta['cells_total']}, suppressed and not written "
                 f"{meta['cells_suppressed']} "
                 f"({100 * meta['cells_suppressed'] / meta['cells_total']:.1f} %)")
    lines.append("scopes by reason: " + ", ".join(f"{k} {v}" for k, v in
                                                   meta["scopes_by_reason"].items()))
    return "\n".join(lines)


def main():
    isolates = load_isolates()
    cube = build_cube(isolates, load_ast())
    scopes = honest_scope(cube)
    payload = write_data_json(cube, scopes)
    size_kb = config.DATA_JSON.stat().st_size / 1024
    print(summary(payload))
    print(f"wrote {config.DATA_JSON.relative_to(config.ROOT)} ({size_kb:.0f} kB)")


if __name__ == "__main__":
    main()

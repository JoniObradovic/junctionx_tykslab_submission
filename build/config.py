"""Lab-owned configuration for the antibiogram build.

Everything a microbiologist would sign off on lives here and nowhere else: the first-isolate
rule, intrinsic resistance, the unit hierarchy, N_min, the window ladder, the suppression
threshold k, detector thresholds and the always-alert list. Scripts import these; no constant is
typed twice. Every value here is one the laboratory would own and review.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
WEB_DATA_DIR = WEB_DIR / "data"          # the JSON files the page fetches
DATA_JSON = WEB_DATA_DIR / "data.json"
ALERTS_JSON = WEB_DATA_DIR / "alerts.json"

# ---------------------------------------------------------------------------
# Denominator and exclusions (CLSI M39, EUCAST expected resistant phenotypes)
# ---------------------------------------------------------------------------

# First isolate per patient x organism x specimen group x analysis period. The analysis period
# is the window being computed, so dedup runs once per window.
FIRST_ISOLATE_KEYS = ("patient_id", "organism", "specimen")

# Organism and agent pairs that are resistant by nature; never shown in the antibiogram.
# EUCAST Expected Resistant Phenotypes v1.2.
INTRINSIC_R = (
    ("Klebsiella pneumoniae", "AMP"),
    ("Klebsiella oxytoca", "AMP"),
    ("Citrobacter freundii", "AMP"),
    ("Proteus mirabilis", "NIT"),
)

# Hospital-onset: inpatient sample taken on day 3 of the admission or later, where the
# admission day is day 1. Expressed as whole days between admission and sample.
HOSPITAL_ONSET_MIN_DAYS = 2

# ---------------------------------------------------------------------------
# Unit hierarchy: unit -> group -> all
# ---------------------------------------------------------------------------

GROUPS = {
    "inpatient": {"fi": "Vuodeosastot", "en": "Inpatient wards"},
    "ed": {"fi": "Päivystys", "en": "Emergency department"},
    "primary": {"fi": "Perusterveydenhuolto", "en": "Primary care"},
}
GROUP_OF_UNIT = {
    "ED": "ed",
    "MED-A": "inpatient",
    "MED-B": "inpatient",
    "SURG": "inpatient",
    "URO": "inpatient",
    "ICU": "inpatient",
    "GER": "inpatient",
    "PHC-N": "primary",
    "PHC-S": "primary",
}
ALL_UNITS = {"code": "ALL", "fi": "Varsinais-Suomen hyvinvointialue (Varha)",
             "en": "Whole wellbeing services county (Varha)", "short": "Varha"}
LEVELS = ("unit", "group", "all")

# ---------------------------------------------------------------------------
# Cube and honest scope
# ---------------------------------------------------------------------------

WINDOW_LADDER_WEEKS = (4, 8, 13, 26, 52)
HEADLINE_WINDOW_WEEKS = 4           # the detector window and the first rung of the ladder
N_MIN = 30                          # CLSI M39 convention
K_SUPPRESS = 5                      # n < k is never shown
HEADLINE = "R"  # % R everywhere, %S only as a derived tooltip
CI_LEVEL = 0.95
SPECIMENS = ("urine", "wound", "blood")
SPECIMEN_LABELS = {"urine": {"fi": "virtsa", "en": "urine"},
                   "wound": {"fi": "haava", "en": "wound"},
                   "blood": {"fi": "veri", "en": "blood"}}
# Stratifiers are toggles applied one at a time: sex and onset.
SEX_STRATA = ("F", "M")
SEX_STRATA_SPECIMENS = ("urine",)
ONSET_STRATA = ("hospital", "community")
STRATA = ("all",) + SEX_STRATA + ONSET_STRATA
STRATUM_LABELS = {"all": {"fi": "kaikki", "en": "all"},
                  "F": {"fi": "naiset", "en": "women"}, "M": {"fi": "miehet", "en": "men"},
                  "hospital": {"fi": "sairaalasyntyinen", "en": "hospital-onset"},
                  "community": {"fi": "avohoitosyntyinen", "en": "community-onset"}}
# UI copy that the page reads from data.json meta, worded as Finnish labs print it
# Dataset-defined names stay as data/ gives them.
UI_LABELS = {
    "product": {"fi": "herkkyystilasto", "en": "cumulative antibiogram"},
    "rate": {"fi": "resistenssi (% R)", "en": "resistance (% R)"},
    "n": {"fi": "testattuja kantoja", "en": "tested isolates"},
    "first_isolate_rule": {"fi": "1 kanta/potilas", "en": "1 isolate per patient"},
    "window": {"fi": "tarkastelujakso", "en": "window"},
    "interval": {"fi": "95 %:n luottamusväli (Wilson)", "en": "95 % confidence interval (Wilson)"},
    "onset_rule": {"fi": "näyte otettu 3. hoitopäivänä tai myöhemmin",
                   "en": "sample taken on day 3 of admission or later"},
    "intrinsic": {"fi": "tulkitaan aina resistentiksi, ei esitetä",
                  "en": "intrinsically resistant, not shown"},
    "suppressed": {"fi": "ei esitetä: testattujen kantojen määrä pieni",
                   "en": "not shown: too few tested isolates"},
    "trend_indicator": {"fi": "resistenssin kehityssuunta (malliarvio)", "en": "trend indicator"},
    "posterior": {"fi": "posterioritodennäköisyys", "en": "posterior probability"},
    "illustrative": {"fi": "havainnollistava esimerkki", "en": "illustrative example"},
}
# Columns of the coverage table (ward x specimen x organism per window), recomputed into data.json meta.
COVERAGE_THRESHOLDS = (30, 10)

DEFAULT_UNIT = "URO"                # the launch context normally sets this
DEFAULT_SPECIMEN = "urine"

# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

DETECTOR_WINDOW_WEEKS = HEADLINE_WINDOW_WEEKS   # the headline window is the detector window
BASELINE_WEEKS = 52                 # trailing weeks before the detector window
BASELINE_MIN_WEEKS = 8              # no test until this much history precedes the window
SCAN_STEP_DAYS = 14                 # the scan grid over the whole dataset
# Baseline rates carry a Jeffreys pseudocount so a zero baseline cannot make p = 0.
BASELINE_PSEUDOCOUNT = 0.5

# Proportion drift: one-sided binomial test of the 4-week % R against the baseline % R.
# The baseline comes from the unit when its first-isolate count allows, else from the parent
# level (group, then all).
DRIFT_BASELINE_N_MIN = N_MIN
DRIFT_WATCH = {"n_min": 10, "p_max": 0.01}
DRIFT_ALERT = {"n_min": 30, "p_max": 0.001, "min_increase_pp": 10.0}

# Resistant phenotypes tracked as patient counts per unit and organism. Each is a 0/1 column
# of isolates.csv or a rule over AST results (organisms, agent found R); `unless` names a
# column that claims the isolate first, so one isolate carries one phenotype.
PHENOTYPES = {
    "ESBL": {"column": "esbl", "fi": "ESBL", "en": "ESBL"},
    "MRSA": {"column": "mrsa", "fi": "MRSA", "en": "MRSA"},
    "VRE": {"column": "vre", "fi": "VRE", "en": "VRE"},
    "CPE": {"column": "cpe", "fi": "CPE", "en": "CPE"},
    "CR-PA": {"rule": {"organisms": ("Pseudomonas aeruginosa",), "agent": "MEM"},
              "fi": "karbapeneemiresistentti P. aeruginosa",
              "en": "carbapenem-resistant P. aeruginosa"},
    "MEM-R-Enterobacterales": {
        "rule": {"organisms": ("Escherichia coli", "Klebsiella pneumoniae", "Klebsiella oxytoca",
                               "Proteus mirabilis", "Citrobacter freundii"),
                 "agent": "MEM"},
        "unless": "cpe",
        "fi": "meropeneemiresistentti enterobakteeri (ei CPE)",
        "en": "meropenem-resistant Enterobacterales (not CPE)"},
}
# Phenotype cluster: patients with the same organism and phenotype in one unit within
# CLUSTER_RULE["days"], tested as a Poisson excess over the unit's own baseline rate.
# alert: count >= CLUSTER_RULE["min_count"] and p < CLUSTER_ALERT_P_MAX; watch: count and p as in
# CLUSTER_WATCH. Setting CLUSTER_ALERT_P_MAX to 1.0 restores the bare count rule.
CLUSTER_RULE = {"min_count": 3, "days": 14}
CLUSTER_ALERT_P_MAX = 0.001
CLUSTER_WATCH = {"min_count": 2, "p_max": 0.01}
CLUSTER_PHENOTYPES = ("ESBL", "MRSA", "VRE", "CPE", "CR-PA")

# A single isolate of any of these is an alert.
ALWAYS_ALERT = ("CPE", "VRE", "MEM-R-Enterobacterales")

# Data quality (housekeeping, microbiologist view; not graded as clinical alerts).
DATA_QUALITY = {"volume_drop_fraction": 0.5,      # 4-week isolates below this share of baseline
                "volume_min_baseline_weeks": 26,  # a shorter baseline is too noisy to judge volume
                "ast_completeness_min": 0.95}     # first isolates with at least one AST result
# §4.4 names a check the dataset cannot carry; alerts.json meta says so instead of omitting it.
QUALITY_NOT_COMPUTED = {"late_results": {"fi": "myöhästyneet tulokset: aineistossa ei ole vastauspäivää",
                                         "en": "late results: the dataset has no result date"}}

GRADES = ("watch", "alert", "always-alert")
GRADE_LABELS = {"watch": {"fi": "seuranta", "en": "watch"},
                "alert": {"fi": "hälytys", "en": "alert"},
                "always-alert": {"fi": "aina hälytys", "en": "always alert"}}
FAMILY_LABELS = {"drift": {"fi": "resistenssin nousu", "en": "proportion drift"},
                 "cluster": {"fi": "epidemiaepäily (rypäs)", "en": "phenotype cluster"},
                 "always_alert": {"fi": "moniresistentti mikrobi (MDR-mikrobi)",
                                  "en": "always-alert phenotype"},
                 "quality": {"fi": "tiedon laatu", "en": "data quality"}}
QUALITY_LABELS = {"eucast_mix": {"fi": "tulkintarajat vaihtuivat jaksolla",
                                 "en": "EUCAST breakpoint versions mixed in the window"},
                  "volume_drop": {"fi": "kantojen määrä laski", "en": "isolate volume dropped"},
                  "ast_completeness": {"fi": "herkkyysmääritysten kattavuus",
                                       "en": "AST completeness"},
                  "unit_mapping": {"fi": "yksikkökoodit ilman hierarkiaa",
                                   "en": "unit codes missing from the hierarchy"}}

# ---------------------------------------------------------------------------
# Demo knowledge: the illustrative block and the two injected events.
# ---------------------------------------------------------------------------

# The clinician view wears a unit's latest finding if it ended within this many weeks.
RECENT_FINDING_WEEKS = 8

# AI layer. A request is treated as patient-specific when the rules say
# so, when the model's verdict says so, or when the model's calibrated probability of
# "patient-specific" reaches this gate; low on purpose, a missed redirect is the costly error.
AI_GATE_P_PATIENT_SPECIFIC = 0.2
# Where the page looks for the local Ask endpoint (ai/serve.py); the launch parameter `ai` overrides.
AI_ENDPOINT = "http://127.0.0.1:8765"

# The synthetic dataset carries two injected events. The page finds them in the
# episodes by these keys; it never types their numbers. Titles are copy for the demo page.
INJECTED_EVENTS = (
    {"family": "drift", "unit": "URO", "organism": "Escherichia coli", "specimen": "urine",
     "agent": "CRO", "peak": "peak_pct_r",
     "fi": "URO: ESBL-epidemia, resistenssin nousu", "en": "URO: ESBL outbreak, proportion drift"},
    {"family": "cluster", "unit": "URO", "organism": "Escherichia coli", "phenotype": "ESBL",
     "peak": "peak_count", "fi": "URO: ESBL-rypäs", "en": "URO: ESBL cluster"},
    {"family": "cluster", "unit": "ICU", "organism": "Staphylococcus aureus", "phenotype": "MRSA",
     "peak": "peak_count", "fi": "ICU: MRSA-rypäs (vain rypästunnistin näkee sen)",
     "en": "ICU: MRSA cluster (only the cluster detector sees it)"},
)

# The trend indicator is a roadmap feature. The demo shows what its slot would look like with
# values typed here, embedded by build/embed.py as the "illustrative" JSON block and labelled
# on every occurrence. Nothing in web/ types these numbers.
ILLUSTRATIVE = {
    "trend_indicator": {
        "label": UI_LABELS["illustrative"],
        "unit": "GER", "organism": "Escherichia coli", "specimen": "urine", "agent": "TMP",
        "window_weeks": HEADLINE_WINDOW_WEEKS,
        "unit_n": 8, "unit_n_r": 4, "unit_pct_r": 50.0,   # the ward's own small 4-week cell
        "parent_pct_r": 19.0,                      # the inpatient group rate it is shrunk toward
        "shrunk_pct_r": 27.0,                      # the hierarchical Beta-binomial estimate
        "posterior": 0.71,                         # P(unit % R exceeds baseline by the drift margin)
        "margin_pp": DRIFT_ALERT["min_increase_pp"],
    }
}

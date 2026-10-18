# build/: from the CSVs to the page's data

```
data/*.csv --> build/build_cube.py --> web/data/data.json
               build/detect.py     --> web/data/alerts.json
               build/export_web.py --> web/data/router.json, illustrative.json, ai_eval.json
```

```bash
uv sync
uv run python build/build_cube.py
uv run python build/detect.py
uv run python build/export_web.py
```

The built files are committed, so the page works without running this.

- **config.py** holds every rule the laboratory would own: the intrinsic-resistance pairs, the
  unit hierarchy, the minimum sample (30 isolates), the window ladder (4, 8, 13, 26, 52 weeks and
  calendar years), the suppression threshold (cells under 5 isolates are never written), the
  detector thresholds, the always-alert phenotypes and the illustrative trend-indicator block. The
  other scripts import it; no constant is typed twice.
- **build_cube.py**: one isolate per patient, organism and specimen group per window; intrinsic
  resistance excluded; the cube over windows at unit, unit-group and county level; Wilson
  intervals; the honest-scope decision per cell (own window, widened, lifted to a higher level,
  suppressed) with the reason recorded; cells under the suppression threshold removed. Prints a
  summary with counts only.
- **detect.py**: scans the whole history in 14-day steps. Rise in resistance (last 4 weeks
  against the preceding year on the same unit, one-sided exact binomial test, lifted to a higher level when
  isolates are few), phenotype clusters (same organism and phenotype within 14 days against the
  unit's own expected count, Poisson tail), always-alert isolates, data-quality checks. Graded
  watch and alert, with an evidence sentence in Finnish and English per episode.
- **export_web.py**: writes the keyword tables of the rule-based router from `ai/router_rules.py`
  (so the browser and Python share one vocabulary), the illustrative block, and the recorded model
  evaluation from `ai/results/eval.json`.

Every number on the page comes from these files. The only exception is the trend indicator's
illustrative value, which lives in its own named block and is always labelled as an example.

## Methods: what is computed and why

Every constant named here is in `config.py`. The short answers below are the ones to give when
someone asks why a number looks the way it does.

### Counting

- **First isolate per patient, organism and specimen group per period (CLSI M39).** A patient
  cultured ten times during one admission would otherwise count ten times and pull the
  percentage toward the resistant, repeatedly sampled few. The rule is applied again for every
  window and every baseline, so a 52-week period and a 4-week period each have their own
  deduplication. *Ref:* CLSI M39 [1]; adherence and effect of the rule in Moehring 2015 [2]; the
  same convention in Finres and the Tyks annual report [8, 9].
- **Intrinsic resistance excluded (EUCAST Expected Resistant Phenotypes).** Klebsiella and
  ampicillin is always resistant; counting it would report a 100 % that tells the prescriber
  nothing. The pairs are listed in `config.py` and never reach the cube. *Ref:* EUCAST Expected
  Resistant Phenotypes [3]; CLSI M39 on excluding intrinsically resistant combinations [1].
- **Minimum sample N_min = 30 (CLSI M39).** Below 30 tested isolates a percentage moves by
  several points with each new result. The convention is the one the annual antibiogram already
  follows, so the ward page claims nothing the annual report would not. *Ref:* CLSI M39 [1];
  Truong 2021 on the 30-isolate rule in practice [4]; Finres and HUSRES apply the same floor [8, 10].
- **Honest scope: widen the window, then raise the level.** When the unit's 4-week cell is under
  N_min, the period widens along the ladder (4, 8, 13, 26, 52 weeks); if 52 weeks is still too
  few, the unit group is used, then the whole county. Time is widened before place because a
  ward's own older data is usually closer to its patients than another ward's recent data. The
  cell shows what was done and why; the choice is a recorded field, not a footnote. *Ref:* CLSI
  M39 permits combining consecutive periods or units when a stratum is under 30 isolates [1]; the
  order of the ladder and its rungs are laboratory-owned settings in `config.py`.
- **Small-cell suppression k = 5.** A cell with fewer than 5 isolates is never written to the
  JSON. This is the disclosure-control floor: with n of 1 or 2, a percentage plus a ward name
  can point at a person. Suppression happens in the build, so the browser cannot un-suppress.
  *Ref:* the threshold-of-five rule used for aggregate output from Finnish health registers
  (Findata) [5]; statistical disclosure control in general, Hundepool et al. 2012 [6].

### The interval

- **95 % Wilson score interval on every percentage.** The usual normal (Wald) interval breaks
  at exactly the sizes this page lives at: for 0 of 30 it gives 0 ± 0, and it overshoots 0 and
  100. Wilson (1927) stays inside 0 to 100, has close to nominal coverage from n of about 10,
  and is asymmetric around small and large percentages, which is what the data looks like. It
  is the interval recommended for proportions in the standard references (Brown, Cai and
  DasGupta 2001) and is cheap to compute without a table. z is taken from `CI_LEVEL`. *Ref:*
  Wilson 1927 [11]; Newcombe 1998, seven methods compared [12]; Brown, Cai and DasGupta 2001 [13].

### Detectors

Both detectors run on a 14-day step grid over the whole dataset so every finding has a
timeline; in production the window would slide with each refresh. No test runs until at least
8 weeks of history precede the window (`BASELINE_MIN_WEEKS`), because a baseline measured on
less is noise.

- **Rise in resistance: one-sided exact binomial test.** The question is "is this 4-week
  resistant count surprising given the ward's own rate over the preceding 52 weeks?" The
  baseline rate is treated as known and the recent count as the random quantity, which is the
  binomial tail P(X >= n_R). It is one-sided because a fall in resistance is not an alarm. It is
  exact (`scipy.stats.binom.sf`) rather than a normal approximation because the recent n is
  often 10 to 40. (It is not Fisher's exact test: that would treat both periods as samples of
  equal standing, and the 52-week baseline is the reference, not a second sample.) *Ref:*
  comparing the current period with the unit's own earlier period is the M39 recommendation for
  trend review [1]; the reference-period design follows the Farrington surveillance algorithm,
  which also compares a current count with a baseline fitted on past windows [14].
- **Baseline fallback.** The baseline is the unit's own 52 weeks when it holds at least 30 first
  isolates (`DRIFT_BASELINE_N_MIN`); otherwise the unit group's, then the whole county's. The
  recent window is always the unit's own. The level used is stored on the finding.
- **Jeffreys pseudocount 0.5 on the baseline rate**, so the rate is (R + 0.5) / (n + 1). A ward
  that saw 0 resistant isolates last year would otherwise have p0 = 0 and the first resistant
  isolate would give p = 0, an infinite surprise. The pseudocount is the Jeffreys prior for a
  proportion and changes a 30-isolate baseline by under 2 percentage points. *Ref:* Jeffreys
  1946 [15]; Brown, Cai and DasGupta 2001 recommend the Jeffreys prior for proportions [13].
- **Grading, rise.** *Watch*: n >= 10 and p < 0.01. *Alert*: n >= 30, p < 0.001 and a rise of at
  least 10 percentage points. The percentage-point floor stops a statistically significant but
  clinically trivial move (say 2 % to 5 % on a large ward) from paging anyone; the n floor keeps
  alerts at antibiogram-grade sample sizes while letting watch flag earlier. *Ref:* the n floor
  of 30 is M39's [1]; the p-values and the 10-point floor are laboratory-owned settings in
  `config.py`, chosen so that both test events planted in the synthetic data are found and
  nothing else alerts (see the main `README.md`).
- **Cluster: Poisson excess.** Patients with the same organism and resistant phenotype (ESBL,
  MRSA, VRE, CPE, carbapenem-resistant *P. aeruginosa*) in one unit within 14 days are counted,
  and the count is compared with the unit's own expected count from its preceding 52 weeks:
  P(X >= count) under Poisson(expected). Poisson is the natural model for rare independent
  events in a fixed period; comparing with the ward's own rate means a ward that always has
  ESBL does not alarm on its usual two, while a ward that never does alarms on its second. *Ref:*
  Poisson-excess against a historical baseline is the Farrington design [14]; WHONET with SaTScan
  is the reference tool for laboratory cluster detection [7], Stelling 2010 [16]; the scan
  statistic itself, Kulldorff 1997 [17]. The simple fixed-window Poisson tail is used here instead
  of a space-time scan because the unit is already the spatial cell.
- **Grading, cluster.** *Alert*: 3 or more patients and p < 0.001. *Watch*: 2 or more and
  p < 0.01. The count floor of 3 is a common outbreak-suspicion threshold; the p-value makes it
  rate-aware. Setting `CLUSTER_ALERT_P_MAX` to 1.0 restores the bare count rule. *Ref:* THL's
  MDR control guidance defines epidemiaepäily by linked cases in one unit and period, which is the
  rule the count floor encodes [18]; the exact counts are laboratory-owned.
- **Always-alert phenotypes.** A single CPE, VRE or meropenem-resistant Enterobacterales is
  reported without any test. Infection control follows up each of these isolates individually
  whatever the ward's history, so a statistical gate would only add delay. *Ref:* THL Ohjaus
  22/2017 on CPE, VRE and other MDR findings [18]; the Communicable Diseases Act 1227/2016 on
  laboratory notification of findings [19].
- **Data quality checks are not alerts.** A volume drop (4-week isolates under 50 % of the
  baseline, baseline at least 26 weeks), AST completeness under 95 %, EUCAST breakpoint version
  mix and unit-mapping gaps are reported to the laboratory view. They explain findings; they are
  not findings. Late results cannot be checked because the dataset has no result date, and the
  metadata says so rather than omitting the check. *Ref:* the checks are those named in the
  challenge brief, section 4.4 [20]; the thresholds are laboratory-owned.

### Shown but not computed

- **Trend indicator (roadmap).** The slot shows a hierarchical Beta-binomial shrinkage estimate:
  a small cell's % R is pulled toward the parent level in proportion to how little data it has,
  and the posterior probability that the unit exceeds its baseline by the alert margin is
  reported. The values on the page are typed in `config.ILLUSTRATIVE` and labelled on every
  occurrence. It is not computed because a model-derived number needs a validation the hackathon
  could not do; the frequentist detectors above make no modelling assumption beyond
  independence. *Ref:* hierarchical Beta-binomial shrinkage, Gelman et al. 2013, chapter 5 [21].

### References

Numbered here for the section above; the full list with links is `docs/sources.md`, section 2.

1. CLSI M39, *Analysis and Presentation of Cumulative Antimicrobial Susceptibility Test Data*, 5th ed.
2. Moehring et al., *J Clin Microbiol* 2015;53:2977.
3. EUCAST, Expected Resistant Phenotypes v1.2.
4. Truong et al., *JAC-Antimicrob Resist* 2021;3:dlab060.
5. Findata, guidance on aggregate output from secondary-use data (small cells under five suppressed).
6. Hundepool et al., *Statistical Disclosure Control*, Wiley 2012.
7. WHONET, with SaTScan cluster detection.
8. THL, Finres 2023, Työpaperi 63/2024.
9. Tyks, Antibioottiresistenssitilanne Varsinais-Suomessa 2021 to 2024.
10. HUS Diagnostiikkakeskus, HUSRES herkkyystilastot.
11. Wilson, *J Am Stat Assoc* 1927;22:209.
12. Newcombe, *Stat Med* 1998;17:857.
13. Brown, Cai and DasGupta, *Stat Sci* 2001;16:101.
14. Farrington, Andrews, Beale and Catchpole, *J R Stat Soc A* 1996;159:547.
15. Jeffreys, *Proc R Soc A* 1946;186:453.
16. Stelling et al., *Epidemiol Infect* 2010;138:873.
17. Kulldorff, *Commun Stat Theory Methods* 1997;26:1481.
18. THL, Ohjaus 22/2017, Ohje moniresistenttien mikrobien tartunnantorjunnasta.
19. Communicable Diseases Act 1227/2016.
20. The challenge brief, Antibiotic efficacy indicator (AEI), TYKS Laboratories.
21. Gelman et al., *Bayesian Data Analysis*, 3rd ed., 2013, chapter 5.

/* ROUTER:BEGIN */
/* JS port of ai/router_rules.py: the rule-based intent router and guardrail. Logic only; every keyword table, regex and vocabulary comes from the embedded
   "router" JSON so nothing is typed twice. */
const ROUTER = (() => {
  const NORM = /[^a-z0-9äöå]+/g;
  const normalise = text => " " + String(text || "").toLowerCase().replace(NORM, " ").trim() + " ";
  const position = (norm, e) => e.match === "word" ? norm.indexOf(" " + e.token + " ") : e.match === "prefix" ? norm.indexOf(" " + e.token) : norm.indexOf(e.token);
  const byPos = (a, b) => a.pos - b.pos || a.order - b.order;
  function matches(norm, entries, lang) {
    const out = [];
    entries.forEach((e, i) => { if (e.lang && e.lang !== lang) return; const pos = position(norm, e); if (pos >= 0) out.push({ pos, order: i, entry: e }); });
    return out.sort(byPos);
  }
  function detectLang(norm, markers) {
    if (/[äöå]/.test(norm)) return "fi";
    const count = list => list.filter(t => norm.includes(" " + t + " ")).length;
    return count(markers.en) > count(markers.fi) ? "en" : "fi";
  }
  const rung = (weeks, ladder) => { for (const w of ladder) if (weeks <= w) return w + "w"; return ladder[ladder.length - 1] + "w"; };
  const first = ms => ms.length ? [ms[0].entry.value, ms[0].entry.token] : [null, null];
  function resolveOrganism(ms) {
    const strong = ms.filter(m => !m.entry.weak);
    const genera = new Set(strong.map(m => m.entry.value.split(" ")[0]));
    return first(strong.concat(ms.filter(m => m.entry.weak && !genera.has(m.entry.value.split(" ")[0]))).sort(byPos));
  }
  function resolveUnit(norm, ms, tab) {
    const units = new Set(tab.vocab.units), groups = new Set(tab.vocab.groups);
    const resolved = ms.map(m => {
      let v = m.entry.value;
      if (v === "PHC") { const d = matches(norm, tab.entries.phc_direction); v = d.length ? d[0].entry.value : "primary"; }
      return { pos: m.pos, order: m.order, value: v, token: m.entry.token };
    });
    for (const pick of [v => units.has(v), v => groups.has(v), v => v === tab.all_code, v => v === "context"]) {
      const hits = resolved.filter(r => pick(r.value)).sort(byPos);
      if (hits.length) return [hits[0].value, hits[0].token];
    }
    return [null, null];
  }
  function resolveWindow(norm, tab) {
    const p = tab.patterns, ladder = tab.ladder_weeks;
    let m = norm.match(new RegExp(p.weeks.re));
    if (m) return [rung(Number(m[1]), ladder), m[0].trim()];
    m = norm.match(new RegExp(p.months.re));
    if (m) return [rung(Number(m[1]) * 4, ladder), m[0].trim()];
    const years = [...norm.matchAll(new RegExp(p.year.re, "g"))].map(x => x[1]).sort();
    if (years.length) return ["Y" + years[years.length - 1], years[years.length - 1]];
    const phrases = matches(norm, tab.entries.window);
    if (phrases.length) { const best = phrases.reduce((a, b) => b.order < a.order ? b : a); return [best.entry.value, best.entry.token]; }
    return [null, null];
  }
  function route(text, lang, tab) {
    const ent = tab.entries, raw = String(text || "").toLowerCase(), norm = normalise(text);
    if (lang !== "fi" && lang !== "en") lang = detectLang(norm, tab.lang_markers);
    const matched = {}, cues = [];
    let organism, specimen, unit, sex, onset, agent, window_;
    [organism, matched.organism] = resolveOrganism(matches(norm, ent.organism));
    [specimen, matched.specimen] = first(matches(norm, ent.specimen));
    [unit, matched.unit] = resolveUnit(norm, matches(norm, ent.unit), tab);
    const sexes = matches(norm, ent.sex);
    [sex, matched.sex] = new Set(sexes.map(m => m.entry.value)).size === 1 ? first(sexes) : [null, null];
    [onset, matched.onset] = first(matches(norm, ent.onset));
    [agent, matched.agent] = first(matches(norm, ent.agent).filter(m => !m.entry.code || new RegExp("\\b" + m.entry.value + "\\b").test(String(text || ""))));
    [window_, matched.window] = resolveWindow(norm, tab);
    const override = matches(norm, ent.override);
    const patient = matches(norm, ent.patient, lang);
    const strong = patient.filter(m => !m.entry.weak), weak = patient.filter(m => m.entry.weak);
    let clinical = patient.some(m => m.entry.clinical);
    for (const name of ["age", "room", "identifier"]) {
      const p = tab.patterns[name];
      if (new RegExp(p.re).test(p.on === "raw" ? raw : norm)) { strong.push({ entry: { token: name } }); clinical = true; }
    }
    strong.forEach(m => cues.push("patient:" + m.entry.token));
    if (weak.length && !override.length) weak.forEach(m => cues.push("patient:" + m.entry.token));
    if (weak.length && override.length) cues.push("override:" + override[0].entry.token);
    const domainHits = matches(norm, ent.domain);
    domainHits.slice(0, 3).forEach(m => cues.push("domain:" + m.entry.token));
    const inDomain = domainHits.length > 0 || clinical || [organism, specimen, agent, unit].some(v => v !== null);
    let intent, view = null;
    if ((strong.length || (weak.length && !override.length)) && inDomain) intent = "patient_specific";
    else if (!inDomain) intent = "off_topic";
    else {
      intent = "aggregate";
      const micro = matches(norm, ent.view_micro);
      const stewardHits = matches(norm, ent.view_steward);
      const years = [...norm.matchAll(new RegExp(tab.patterns.year.re, "g"))].map(x => x[1]);
      view = micro.length ? "micro" : (stewardHits.length || years.length) ? "steward" : "clinician";
      if (micro.length) cues.push("view:" + micro[0].entry.token);
      else if (stewardHits.length) cues.push("view:" + stewardHits[0].entry.token);
      else if (years.length) cues.push("view:" + years[0]);
    }
    const matchedOut = {};
    for (const [k, v] of Object.entries(matched)) if (v) matchedOut[k] = v;
    return { intent, view, lang, slice: { organism, specimen, unit, sex, onset, window: window_, agent }, matched: matchedOut, cues };
  }
  return { route, normalise, detectLang };
})();
/* ROUTER:END */
window.ROUTER = ROUTER;

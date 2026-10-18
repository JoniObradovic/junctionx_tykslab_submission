/* ------------------------------------------------------------------------------------------
   AEI: the pure selection module. Plain objects in, plain objects out, no DOM. This is the
   seam a natural-language router would call: {unit, specimen, stratum} -> matrix rows.
   ------------------------------------------------------------------------------------------ */
const AEI = (() => {

  function rows(table) {
    const cols = table.columns;
    return table.rows.map(r => { const o = {}; cols.forEach((c, i) => { o[c] = r[i]; }); return o; });
  }
  const k = (...parts) => parts.join("|");

  function index(data) {
    const cells = new Map(), counts = new Map(), scopes = new Map();
    for (const c of rows(data.cells)) cells.set(k(c.window, c.level, c.unit, c.stratum, c.specimen, c.organism, c.agent), c);
    for (const c of rows(data.counts)) counts.set(k(c.window, c.level, c.unit, c.stratum, c.specimen, c.organism), c.n);
    const scopeRows = rows(data.scopes);
    for (const s of scopeRows) scopes.set(k(s.unit, s.specimen, s.organism, s.stratum), s);
    const intrinsic = new Set(data.meta.intrinsic_r.map(p => k(p[0], p[1])));
    return { meta: data.meta, cells, counts, scopes, scopeRows, intrinsic };
  }

  function indexAlerts(alerts) {
    const episodes = rows(alerts.episodes);
    const series = new Map(alerts.series.map(s => [s.id, rows(s)]));
    const quality = rows(alerts.quality);
    const findings = rows(alerts.alerts);
    /* Grade order is the order the lab lists the grades in; the last is the highest. */
    const rank = {};
    Object.keys(alerts.meta.grades).forEach((g, i) => { rank[g] = i; });
    return { meta: alerts.meta, episodes, series, quality, findings, rank };
  }

  /* Agents the lab reports for this organism and specimen group, in the lab's panel order. */
  function agentsFor(meta, organism, specimen) {
    const panel = new Set(meta.panels[organism] || []);
    return Object.keys(meta.agents).filter(a => panel.has(a) &&
      (meta.agents[a].reported_for === "all" || meta.agents[a].reported_for === specimen));
  }

  /* The clinician matrix: one row per organism present in the unit's specimen group, each cell
     looked up at the organism's honest scope, with the delta against the annual baseline at
     the same scope. */
  function matrix(idx, { unit, specimen, stratum }) {
    const meta = idx.meta;
    const ladderTop = meta.window_ladder[meta.window_ladder.length - 1];
    const scopeRows = idx.scopeRows.filter(s => s.unit === unit && s.specimen === specimen && s.stratum === stratum);
    const volume = s => idx.counts.get(k(ladderTop, "unit", unit, stratum, specimen, s.organism)) || 0;
    scopeRows.sort((a, b) => volume(b) - volume(a) || a.organism.localeCompare(b.organism));
    const agentSet = new Set();
    scopeRows.forEach(s => agentsFor(meta, s.organism, specimen).forEach(a => agentSet.add(a)));
    const agents = Object.keys(meta.agents).filter(a => agentSet.has(a));
    const out = scopeRows.map(scope => {
      const panel = new Set(agentsFor(meta, scope.organism, specimen));
      const cells = agents.map(agent => {
        if (!panel.has(agent)) {
          return { agent, kind: idx.intrinsic.has(k(scope.organism, agent)) ? "intrinsic" : "untested" };
        }
        if (scope.reason === "suppressed") return { agent, kind: "suppressed" };
        const cell = idx.cells.get(k(scope.window_used, scope.level_used, scope.unit_used, stratum, specimen, scope.organism, agent));
        if (!cell) return { agent, kind: "suppressed" };
        const annual = idx.cells.get(k(meta.annual_baseline_window, scope.level_used, scope.unit_used, stratum, specimen, scope.organism, agent));
        const delta = annual ? Math.round((cell.pct_r - annual.pct_r) * 10) / 10 : null;
        return { agent, kind: "cell", cell, annual, delta };
      });
      return { organism: scope.organism, scope, volume: volume(scope), cells };
    });
    return { unit, specimen, stratum, agents, rows: out };
  }

  /* The one finding a matrix row wears: the highest grade inside the recent span, otherwise
     the latest older one, flagged as such. */
  function rowFinding(aidx, { unit, specimen, organism }, weeks, earliest) {
    const asOf = new Date(aidx.meta.data_end);
    const since = new Date(asOf.getTime() - weeks * 7 * 86400000).toISOString().slice(0, 10);
    const eps = aidx.episodes.filter(e => e.unit === unit && e.organism === organism && e.end >= (earliest || "") && (e.family !== "drift" || e.specimen === specimen));
    if (!eps.length) return null;
    const recent = eps.filter(e => e.end >= since);
    if (recent.length) return { episode: sortEpisodes(recent, aidx.rank)[0], recent: true };
    const latest = [...eps].sort((a, b) => b.end.localeCompare(a.end))[0];
    return { episode: latest, recent: false };
  }

  function recentEpisodes(aidx, unit, weeks) {
    const asOf = new Date(aidx.meta.data_end);
    const since = new Date(asOf.getTime() - weeks * 7 * 86400000).toISOString().slice(0, 10);
    return sortEpisodes(aidx.episodes.filter(e => e.unit === unit && e.end >= since), aidx.rank);
  }

  function sortEpisodes(list, rank) {
    return [...list].sort((a, b) => (rank[b.grade] - rank[a.grade]) || (b.end < a.end ? -1 : b.end > a.end ? 1 : 0));
  }

  /* The injected events named in alerts meta, found as the peak alert-grade
     episode matching each key; nothing about them is typed here. */
  function injectedEvents(aidx) {
    const eps = aidx.episodes;
    const keys = ["family", "unit", "organism", "specimen", "agent", "phenotype"];
    const matches = spec => eps.filter(e => e.grade === "alert" && keys.every(k => spec[k] === undefined || e[k] === spec[k]));
    const found = aidx.meta.injected_events.map(spec => ({ spec, episode: matches(spec).reduce((best, e) => (best === null || e[spec.peak] > best[spec.peak]) ? e : best, null) }));
    const units = new Set(aidx.meta.injected_events.map(s => s.unit));
    const elsewhere = eps.filter(e => e.grade === "alert" && !units.has(e.unit));
    return { found, elsewhere };
  }

  function latestQuality(aidx) {
    const latest = new Map();
    for (const q of aidx.quality) {
      const key = q.check + "|" + q.unit;
      const prev = latest.get(key);
      if (!prev || (q.step_end || "") > (prev.step_end || "")) latest.set(key, q);
    }
    return [...latest.values()];
  }

  return { rows, key: k, index, indexAlerts, agentsFor, matrix, rowFinding, recentEpisodes, sortEpisodes, injectedEvents, latestQuality };
})();
window.AEI = AEI;



/* ------------------------------------------------------------------------------------------
   The shell: reads the JSON tags, keeps the selection state, renders. Nothing here computes a
   statistic; it formats what AEI selected.
   ------------------------------------------------------------------------------------------ */
(async () => {
  /* The data lives beside the page (build/build_cube.py, build/detect.py, build/export_web.py
     write web/data/*.json). Browsers refuse fetch on file:, so a page opened from disk shows the
     one-line hint to serve the folder instead. */
  const loadJson = async name => { const r = await fetch("data/" + name + ".json"); if (!r.ok) throw new Error(name + ": HTTP " + r.status); return r.json(); };
  let DATA, ALERTS, ILLUSTRATIVE, ROUTER_TABLES, AI_EVAL;
  try {
    [DATA, ALERTS, ILLUSTRATIVE, ROUTER_TABLES, AI_EVAL] = await Promise.all(["data", "alerts", "illustrative", "router", "ai_eval"].map(loadJson));
  } catch (err) {
    const el = document.getElementById("boot-error");
    const lang = new URLSearchParams(location.search).get("lang") === "fi" ? "fi" : "en";
    el.textContent = T[lang].boot + " (" + err.message + ")"; el.hidden = false;
    document.getElementById("app").hidden = true;
    return;
  }
  const idx = AEI.index(DATA);
  const aidx = AEI.indexAlerts(ALERTS);
  const meta = idx.meta, ameta = aidx.meta;
  const RECENT_WEEKS = ameta.recent_weeks;
  const k = AEI.key;
  const HEADLINE = "% " + meta.headline;
  const SEX = meta.sex_strata;

  const params = new URLSearchParams(location.search);
  /* The local AI endpoint (ai/serve.py). A page served over http (or opened as a file) asks it by
     default; the hosted https page asks it only when the launch parameter `ai` names it
     explicitly (browsers treat http://127.0.0.1 as a trustworthy origin, and the endpoint sends
     permissive CORS headers), so the GitHub Pages copy can use a decision model running on the
     reader's own machine: ...?ai=http://127.0.0.1:8765 */
  const AI_ENDPOINT = params.get("ai") || ROUTER_TABLES.endpoint;
  const ASK_TIMEOUT_MS = 8000;
  const localPage = () => { const proto = typeof location !== "undefined" ? location.protocol : ""; return Boolean(AI_ENDPOINT) && typeof fetch === "function" && (proto === "file:" || proto === "http:" || Boolean(params.get("ai"))); };
  async function probeEndpoint() {
    if (!localPage()) return;
    try {
      const ctrl = new AbortController(); const timer = setTimeout(() => ctrl.abort(), 3000);
      const r = await fetch(AI_ENDPOINT + "/health", { signal: ctrl.signal }); clearTimeout(timer);
      if (!r.ok) return;
      const hres = await r.json();
      state.aiProfiles = (hres.profiles || []).map(p => ({ name: p.name, label: p.label, ok: p.ok, thinking: p.thinking, timeoutMs: p.timeout_ms || ASK_TIMEOUT_MS, model: p.model }));
      if (!state.aiModel || !state.aiProfiles.some(p => p.name === state.aiModel && p.ok)) state.aiModel = (state.aiProfiles.find(p => p.name === hres.default && p.ok) || state.aiProfiles.find(p => p.ok) || {}).name || null;
      renderAsk();
    } catch (err) { state.aiProfiles = null; }
  }
  /* Theme: auto follows the operating system (prefers-color-scheme); light and dark pin it via
     data-theme on <html>, which the CSS tokens key on. Remembered per browser when storage allows. */
  function applyTheme() {
    if (state.theme === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = state.theme;
    try { if (typeof localStorage !== "undefined") localStorage.setItem("aei-theme", state.theme); } catch (err) { /* storage blocked: fine */ }
  }
  try { if (!params.get("theme") && typeof localStorage !== "undefined") { const saved = localStorage.getItem("aei-theme"); if (["light", "dark"].includes(saved)) state.theme = saved; } } catch (err) { /* storage blocked: fine */ }
  /* Everything a reader can change in a view. A role change restores these defaults, so the
     view a role opens is the one it would open from the launch context. */
  const defaultSelections = () => ({
    unit: meta.units[params.get("unit")] ? params.get("unit") : meta.default_unit,
    specimen: meta.specimens[params.get("specimen")] ? params.get("specimen") : meta.default_specimen,
    stratum: "all",
    alertUnit: "", alertFamily: "", alertGrade: "",
    ask: null, matrixAll: false, openFolds: new Set(),
    steward: { scope: meta.all.code, specimen: meta.default_specimen, organism: null },
    ex: { scope: "", specimen: "", organism: "", agent: "", stratum: "all", window: meta.window_ladder[meta.window_ladder.length - 1], minN: 0,
          unit: "", family: "", grade: "", year: "", scopeUnit: meta.default_unit },
  });
  const state = Object.assign({
    lang: ["fi", "en"].includes(params.get("lang")) ? params.get("lang") : "en",   /* submission is English; Finnish stays as a toggle */
    aiDebug: params.get("debug") === "1", aiModel: params.get("model") || null, aiProfiles: null,
    theme: ["light", "dark"].includes(params.get("theme")) ? params.get("theme") : "auto",
    role: null, tab: null,
  }, defaultSelections());
  const remembered = (key, fallback) => { try { const v = typeof localStorage !== "undefined" ? localStorage.getItem(key) : null; return v === null ? fallback : v === "1"; } catch (err) { return fallback; } };
  const remember = (key, on) => { try { if (typeof localStorage !== "undefined") localStorage.setItem(key, on ? "1" : "0"); } catch (err) { /* storage blocked: fine */ } };
  state.sideFolded = remembered("aei-side-folded", false);
  state.askFolded = remembered("aei-ask-folded", false);
  /* Roles come from the launch context and are not lab configuration: each role lists
     the tabs that are relevant to it, default first. Explore is in every list because it holds
     all the information; without a role every tab is shown. */
  const ROLES = {
    clinician: ["clinician", "explore"],
    infection: ["micro", "clinician", "explore"],
    steward: ["steward", "explore", "clinician"],
    labit: ["labit", "explore", "micro"],
  };
  const TABS = ["clinician", "micro", "steward", "explore", "labit"];
  if (ROLES[params.get("role")]) state.role = params.get("role");
  const hashTab = location.hash.replace("#", "");
  const allowed = () => state.role ? ROLES[state.role] : TABS;
  state.tab = allowed().includes(hashTab) ? hashTab : allowed()[0];

  /* DOM helpers; all text goes through textContent. */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    if (attrs) for (const [key, val] of Object.entries(attrs)) {
      if (val === null || val === undefined || val === false) continue;
      if (key === "class") el.className = val;
      else if (key.startsWith("on")) el.addEventListener(key.slice(2), val);
      else el.setAttribute(key, val === true ? "" : val);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const SVG_NS = "http://www.w3.org/2000/svg";
  function svg(tag, attrs, ...kids) {
    const el = document.createElementNS(SVG_NS, tag);
    if (attrs) for (const [key, val] of Object.entries(attrs)) {
      if (val === null || val === undefined) continue;
      if (typeof val === "string" && val.includes("var(")) el.style.setProperty(key, val); else el.setAttribute(key, val);
    }
    for (const kid of kids.flat()) if (kid) el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const t = () => T[state.lang];
  const L = obj => obj[state.lang];
  const fmt = (s, vars) => s.replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? "");
  const nbsp = " ";

  function num(x, digits = 1) {
    if (x === null || x === undefined || Number.isNaN(x)) return "";
    const s = Number(x).toFixed(digits);
    return state.lang === "fi" ? s.replace(".", ",") : s;
  }
  const pct = (x, digits = 1) => num(x, digits) + nbsp + "%";
  function date(iso) {
    if (!iso) return "";
    const [y, m, d] = iso.split("-").map(Number);
    return state.lang === "fi" ? d + "." + m + "." + y : iso;
  }
  const period = (a, b) => state.lang === "fi" ? date(a) + "–" + date(b) : date(a) + " to " + date(b);
  function pval(p, family) {
    if (p === null || p === undefined) return "";
    const pmax = family === "cluster" ? ameta.cluster.alert_p_max : ameta.drift.alert.p_max;
    const s = p < pmax ? Number(p).toExponential(1) : Number(p).toFixed(3);
    return "p = " + (state.lang === "fi" ? s.replace(".", ",") : s);
  }
  const unitName = code => code === meta.all.code ? meta.all.short : (meta.units[code] ? code + nbsp + "(" + L(meta.units[code]) + ")" : code);
  const unitShort = code => meta.units[code] ? L(meta.units[code]) : (meta.groups[code] ? L(meta.groups[code]) : meta.all.short);
  const windowLabel = w => w.startsWith("Y") ? w.slice(1) : w.replace("w", nbsp + t().clinician.weeks);
  const gradeList = () => Object.values(ameta.grades).map(g => L(g)).join(", ");
  const specimenList = () => Object.values(meta.specimens).map(s => L(s)).join(", ");
  const annualLabel = () => windowLabel(meta.annual_baseline_window);

  function chip(grade) {
    return h("span", { class: "chip " + grade }, h("span", { class: "dot", "aria-hidden": "true" }), L(ameta.grades[grade]));
  }
  function scopeBadge(scope) {
    return h("span", { class: "badge " + scope.reason, title: L({ fi: scope.reason_fi, en: scope.reason_en }) },
      scope.reason === "own" ? windowLabel(scope.window_used) : L({ fi: scope.reason_fi, en: scope.reason_en }));
  }

  /* Interval band: a 0 to 100 track, the Wilson interval as a band, the point as a mark. The
     cube stores the interval on %S; % R is its mirror. */
  function ciBand(cell) {
    const lo = 100 - cell.ci_s_hi, hi = 100 - cell.ci_s_lo, p = cell.pct_r;
    const el = svg("svg", { viewBox: "0 0 100 8", preserveAspectRatio: "none", role: "img",
      "aria-label": meta.labels.interval[state.lang] + " " + num(lo) + " – " + num(hi) });
    el.append(svg("rect", { x: 0, y: 3, width: 100, height: 2, fill: "var(--line)" }));
    el.append(svg("rect", { x: lo, y: 1, width: Math.max(hi - lo, 0.8), height: 6, rx: 1, fill: "var(--band)", opacity: 0.85 }));
    el.append(svg("rect", { x: Math.max(0, Math.min(99, p - 0.6)), y: 0, width: 1.2, height: 8, fill: "var(--point)" }));
    return el;
  }

  /* ---------------- Header, navigation ---------------- */
  /* Progressive disclosure: a short label that opens the explanation (details/summary). */
  function info(label, ...kids) {
    const d = h("details", { class: "info" }, h("summary", null, label));
    d.append(h("div", { class: "info-body" }, ...kids));
    return d;
  }
  function foldCard(title, ...kids) {
    const d = h("details", { class: "card fold", open: state.openFolds.has(title) || null,
      ontoggle: e => { if (e.target.open) state.openFolds.add(title); else state.openFolds.delete(title); } }, h("summary", null, h("h3", null, title)));
    d.append(h("div", { class: "fold-body" }, ...kids));
    return d;
  }
  /* Print opens every fold so the legend and notes are on paper. */
  const openFolds = open => { for (const d of document.querySelectorAll("details.info, details.fold")) d.open = open; };
  if (typeof window !== "undefined" && window.addEventListener) { window.addEventListener("beforeprint", () => openFolds(true)); window.addEventListener("afterprint", () => openFolds(false)); }

  /* The sidebar: brand block, the views as a vertical list, and the global selections (role,
     care unit, language, theme, print). A press logo in assets/brand/ replaces the drawn mark
     when the file exists; the chevron folds the sidebar to an icon rail. */
  const ICONS = {
    clinician: "M3 5h18v4H3zM3 11h8v8H3zM13 11h8v8h-8z",
    micro: "M12 3a6 6 0 0 0-6 6v4l-2 3h16l-2-3V9a6 6 0 0 0-6-6zM10 19a2 2 0 0 0 4 0",
    steward: "M3 18l5-6 4 3 5-8 4 4M3 21h18",
    explore: "M10 4a6 6 0 1 1 0 12 6 6 0 0 1 0-12zM14.5 14.5L21 21",
    labit: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM4 12h2M18 12h2M12 4v2M12 18v2M6.3 6.3l1.4 1.4M16.3 16.3l1.4 1.4M6.3 17.7l1.4-1.4M16.3 7.7l1.4-1.4",
  };
  const icon = name => { const el = svg("svg", { viewBox: "0 0 24 24", class: "ic", "aria-hidden": "true" }); el.append(svg("path", { d: ICONS[name], fill: "none", stroke: "currentColor", "stroke-width": "1.8", "stroke-linecap": "round", "stroke-linejoin": "round" })); return el; };
  function brandMark() {
    const el = svg("svg", { viewBox: "0 0 32 32", class: "mark", "aria-hidden": "true" });
    el.append(svg("circle", { cx: 16, cy: 16, r: 12, fill: "none", stroke: "var(--point)", "stroke-width": "3.5", "stroke-dasharray": "60 15.4", "stroke-dashoffset": "-8", "stroke-linecap": "round" }),
      svg("circle", { cx: 16, cy: 16, r: 4.5, fill: "var(--band)" }));
    return el;
  }
  function renderSide() {
    const side = document.getElementById("side"), c = t();
    document.documentElement.lang = state.lang;
    document.title = L(meta.labels.product).replace(/^./, ch => ch.toUpperCase()) + nbsp + c.brand.region;
    const brand = h("div", { class: "brand" }, brandMark(),
      h("img", { class: "logo", src: "assets/brand/logo.png", alt: c.brand.owner, onload: e => e.target.closest(".brand").classList.add("has-logo"),
        onerror: e => e.target.remove() }),
      h("div", { class: "bt" }, h("span", { class: "owner" }, c.brand.owner), h("span", { class: "prod" }, L(meta.labels.product).replace(/^./, ch => ch.toUpperCase()) + " · " + c.brand.region)));
    const nav = h("nav", { class: "nav", id: "nav", role: "tablist", "aria-label": c.side.views });
    for (const id of allowed()) nav.append(h("button", { type: "button", role: "tab", "aria-selected": String(id === state.tab), "aria-controls": id, title: c.tabs[id], onclick: () => showTab(id) }, icon(id), h("span", { class: "label" }, c.tabs[id])));
    const roleSel = h("select", { id: "role", onchange: ev => setRole(ev.target.value || null) }, h("option", { value: "", selected: !state.role }, "–"),
      ...Object.entries(c.roles.items).map(([code, item]) => h("option", { value: code, selected: code === state.role }, item[0])));
    const langSeg = h("div", { class: "seg", role: "group", "aria-label": c.langLabel },
      ...[["fi", "Suomi"], ["en", "English"]].map(([code, label]) => h("button", { type: "button", "data-lang": code, "aria-pressed": String(code === state.lang), onclick: () => { state.lang = code; renderAll(); } }, label)));
    const themeSeg = h("div", { class: "seg", role: "group", "aria-label": c.themeLabel },
      ...Object.entries(c.themes).map(([code, label]) => h("button", { type: "button", "aria-pressed": String(code === state.theme), title: c.themeLabel + ": " + label, onclick: () => { state.theme = code; applyTheme(); renderSide(); } }, label)));
    const printBtn = h("button", { type: "button", class: "toolbtn", id: "print-btn", onclick: () => { showTab("clinician"); if (typeof window.print === "function") window.print(); } }, c.print);
    const settings = h("div", { class: "sideset" },
      h("div", { class: "eyebrow" }, c.side.settings),
      h("div", { class: "control" }, h("label", { for: "role" }, c.roles.label), roleSel),
      h("div", { class: "control" }, h("label", { for: "unit" }, c.clinician.unit), unitSelect(state.unit, v => { state.unit = v; renderClinician(); renderSide(); }), h("span", { class: "small" }, c.side.unitHint)),
      h("div", { class: "control" }, h("span", { class: "lbl" }, c.langLabel), langSeg),
      h("div", { class: "control" }, h("span", { class: "lbl" }, c.themeLabel), themeSeg),
      printBtn,
      info(c.common.about, h("p", null, c.subtitle), h("p", { class: "small" }, c.roles.lead)));
    const fold = h("button", { type: "button", class: "sidefold", title: state.sideFolded ? c.side.unfold : c.side.fold, "aria-label": state.sideFolded ? c.side.unfold : c.side.fold,
      onclick: () => { state.sideFolded = !state.sideFolded; remember("aei-side-folded", state.sideFolded); applyFolds(); renderSide(); } }, state.sideFolded ? "›" : "‹");
    /* The region's logo (assets/brand/region.png) closes the sidebar when the file exists. */
    const region = h("img", { class: "regionlogo", src: "assets/brand/region.png", alt: c.brand.region, onerror: e => e.target.remove() });
    side.replaceChildren(brand, settings, nav, region, fold);
    const askFold = document.getElementById("ask-fold");
    askFold.textContent = state.askFolded ? c.side.askUnfold : "›"; askFold.title = state.askFolded ? c.side.askUnfold : c.side.askFold; askFold.setAttribute("aria-label", askFold.title);
    askFold.onclick = () => { state.askFolded = !state.askFolded; remember("aei-ask-folded", state.askFolded); applyFolds(); renderSide(); };
    document.getElementById("rolebrief").replaceChildren(info(c.common.about, h("span", null, h("b", null, state.role ? c.roles.items[state.role][0] + ". " : ""), c.roles.brief[state.role || "none"])));
    applyFolds();
  }
  function applyFolds() {
    const app = document.getElementById("app");
    app.classList.toggle("side-folded", Boolean(state.sideFolded));
    app.classList.toggle("ask-folded", Boolean(state.askFolded));
  }
  const renderHeader = renderSide, renderTabs = renderSide;

  function setRole(role) {
    /* A role is a launch context: every selection, question and open fold goes back to its
       default and the role's first view opens. */
    Object.assign(state, defaultSelections());
    state.role = role;
    state.tab = allowed()[0];
    renderAll();
  }

  function showTab(id) {
    if (!allowed().includes(id)) id = allowed()[0];
    state.tab = id;
    for (const tab of TABS) document.getElementById(tab).hidden = tab !== id;
    for (const b of document.querySelectorAll("#nav button")) b.setAttribute("aria-selected", String(b.getAttribute("aria-controls") === id));
    if (location.hash !== "#" + id) { try { history.replaceState(null, "", "#" + id); } catch (err) { location.hash = id; } }
  }

  /* ---------------- Section 1: clinician view ---------------- */
  function seg(options, current, onPick, disabled = () => false) {
    const el = h("div", { class: "seg", role: "group" });
    for (const [value, label] of options) {
      el.append(h("button", { type: "button", "aria-pressed": String(value === current), disabled: disabled(value), onclick: () => onPick(value) }, label));
    }
    return el;
  }

  function unitSelect(current, onPick, allowAll, id) {
    const sel = h("select", { id: id || (allowAll ? "alert-unit" : "unit"), onchange: e => onPick(e.target.value) });
    if (allowAll) sel.append(h("option", { value: "" }, t().micro.unitAll));
    for (const [gcode, g] of Object.entries(meta.groups)) {
      const og = h("optgroup", { label: L(g) });
      for (const [code, u] of Object.entries(meta.units)) if (u.group === gcode) og.append(h("option", { value: code, selected: code === current }, code + " · " + L(u)));
      sel.append(og);
    }
    return sel;
  }

  function renderClinician() {
    const c = t().clinician, root = document.getElementById("clinician");
    const m = AEI.matrix(idx, state);
    const w = meta.windows[meta.headline_window];
    root.replaceChildren(
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, c.eyebrow), h("h2", null, c.h)), info(t().common.about, c.lead)),
      askStrip("clinician"),
      h("div", { class: "controls" },
        h("div", { class: "control" }, h("span", { class: "lbl" }, c.specimen),
          seg(Object.entries(meta.specimens).map(([code, s]) => [code, L(s)]), state.specimen, v => { state.specimen = v; if (!meta.sex_strata_specimens.includes(v) && SEX.includes(state.stratum)) state.stratum = "all"; renderClinician(); })),
        h("div", { class: "control" }, h("span", { class: "lbl" }, c.stratum),
          seg(Object.entries(meta.strata).map(([code, s]) => [code, L(s)]), state.stratum, v => { state.stratum = v; renderClinician(); },
            v => SEX.includes(v) && !meta.sex_strata_specimens.includes(state.specimen)))),
      h("div", { class: "scopeline" },
        h("span", null, h("strong", null, unitName(state.unit)), " · ", L(meta.specimens[state.specimen]), state.stratum !== "all" ? " · " + L(meta.strata[state.stratum]) : ""),
        h("span", null, c.window + ": ", h("b", { class: "num" }, windowLabel(meta.headline_window)), " (" + period(w.start, w.end) + ")"),
        h("span", null, c.annual + ": ", h("b", null, annualLabel()))),
      renderRecentAlerts(),
      renderMatrix(m),
      info(t().common.howToRead,
      h("div", { class: "caption" }, h("span", null, h("b", null, L(meta.labels.rate))), h("span", null, "n = " + L(meta.labels.n) + " (" + L(meta.labels.first_isolate_rule) + ")"), h("span", null, L(meta.labels.interval)),
        h("span", null, c.nminLabel + " = ", h("b", { class: "num" }, meta.n_min)), h("span", null, c.dataEnd + ": " + period(meta.data_start, meta.data_end))),
      h("div", { class: "legend" },
        h("span", { class: "k" }, (() => { const s = svg("svg", { viewBox: "0 0 100 8", preserveAspectRatio: "none" }); s.append(svg("rect", { x: 0, y: 3, width: 100, height: 2, fill: "var(--line)" }), svg("rect", { x: 30, y: 1, width: 30, height: 6, rx: 1, fill: "var(--band)", opacity: 0.85 }), svg("rect", { x: 44, y: 0, width: 1.2, height: 8, fill: "var(--point)" })); return s; })(), L(meta.labels.interval)),
        h("span", { class: "k" }, "n = " + L(meta.labels.n) + ", " + L(meta.labels.first_isolate_rule)),
        h("span", { class: "k" }, "Δ " + fmt(c.delta, { year: annualLabel() }) + ", " + c.deltaNote),
        h("span", { class: "k" }, c.eucastNote)),
      h("p", { class: "note" }, fmt(c.nminNote, { nmin: meta.n_min, headline: windowLabel(meta.headline_window), ladder: meta.window_ladder.map(windowLabel).join(", "), groups: Object.values(meta.groups).map(g => L(g).toLowerCase()).join(", "), all: meta.all.short }) + " " + c.intrinsicNote + " " + L(meta.suppression_rule) + " " + fmt(c.onsetNote, { rule: L(meta.labels.onset_rule) }) + " " + fmt(c.sexNote, { specimens: meta.sex_strata_specimens.map(s => L(meta.specimens[s])).join(", ") }) + " " + c.printHint)));
  }

  function renderRecentAlerts() {
    const c = t().clinician;
    const recent = AEI.recentEpisodes(aidx, state.unit, RECENT_WEEKS);
    const box = h("div", { class: "alerts-inline" });
    box.append(h("div", { class: "eyebrow", style: "flex-basis:100%" }, c.recentAlerts));
    if (!recent.length) box.append(h("span", { class: "small" }, fmt(c.noRecent, { weeks: RECENT_WEEKS }) + " " + c.history));
    for (const e of recent) {
      box.append(h("div", { class: "item" },
        h("div", { style: "display:flex;gap:.5rem;align-items:center;flex-wrap:wrap" }, chip(e.grade), h("span", { class: "small" }, L(ameta.families[e.family]) + " · " + period(e.start, e.end))),
        info(t().common.evidence, L({ fi: e.evidence_fi, en: e.evidence_en }))));
    }
    if (recent.length) box.append(h("span", { class: "small", style: "flex-basis:100%" }, c.history));
    return box;
  }

  function renderMatrix(m) {
    const c = t().clinician;
    const thead = h("thead", null, h("tr", null,
      h("th", { class: "org", scope: "col" }, c.organism),
      ...m.agents.map(a => h("th", { scope: "col", class: askHit("agent", a) ? "hit" : null }, h("span", { class: "code" }, a), h("span", { class: "name" }, L({ fi: meta.agents[a].fi, en: meta.agents[a].en }))))));
    const tbody = h("tbody");
    const COLLAPSED_ROWS = 6;
    const hitRows = m.rows.filter(row => askHit("organism", row.organism));
    const shownRows = state.matrixAll ? m.rows : (hitRows.length ? hitRows : m.rows.slice(0, COLLAPSED_ROWS));
    for (const row of shownRows) {
      const s = row.scope;
      const org = h("td", { class: "org" },
        h("span", { class: "o" }, row.organism),
        h("span", { class: "n" }, s.reason === "suppressed" ? L(meta.labels.suppressed) : "n = " + s.n + " · " + (s.level_used === "unit" ? c.scopeOwn : unitShort(s.unit_used))),
        scopeBadge(s));
      /* The row wears one finding: the highest grade inside the recent span, else the latest one
         since the start of the annual comparison period, marked as earlier. */
      const finding = AEI.rowFinding(aidx, { unit: state.unit, specimen: state.specimen, organism: row.organism }, RECENT_WEEKS, meta.windows[meta.annual_baseline_window].start);
      if (finding) {
        const e = finding.episode;
        org.append(h("div", { class: "finding" + (finding.recent ? "" : " old") },
          chip(e.grade), h("span", { class: "small" }, (finding.recent ? c.latest : c.earlier) + ": " + L(ameta.families[e.family]) + (e.agent ? " " + e.agent : "") + ", " + date(e.end)),
          info(t().common.evidence, L({ fi: e.evidence_fi, en: e.evidence_en }))));
      }
      const tr = h("tr", { class: askHit("organism", row.organism) ? "hit" : null }, org);
      for (const x of row.cells) {
        if (x.kind === "cell") {
          const cell = x.cell;
          const d = x.delta;
          const deltaEl = d === null ? h("span", { class: "delta muted" }, "·") :
            h("span", { class: "delta", title: fmt(c.delta, { year: annualLabel() }) + " " + pct(x.annual.pct_r) + " (n = " + x.annual.n + ")" },
              h("span", { class: d > 0 ? "up" : d < 0 ? "down" : "" }, d > 0 ? "▲" : d < 0 ? "▼" : "="), " ", (d > 0 ? "+" : "") + num(d));
          tr.append(h("td", { class: "cell" + (cell.eucast_mixed ? " mixed" : "") + (askHit("agent", x.agent) ? " hit" : ""),
            title: L(meta.labels.rate) + " " + pct(cell.pct_r) + ", " + L(meta.labels.interval) + " " + num(100 - cell.ci_s_hi) + "–" + num(100 - cell.ci_s_lo) + ", n = " + cell.n + " (" + L({ fi: s.reason_fi, en: s.reason_en }) + "). %S = " + pct(cell.pct_s) + ". EUCAST " + cell.eucast_versions.join(", ") },
            h("div", { class: "pct" }, num(cell.pct_r), h("span", { class: "u" }, HEADLINE)),
            h("span", { class: "ci" }, ciBand(cell)),
            h("div", { class: "sub" }, h("span", null, "n = " + cell.n), deltaEl)));
        } else if (x.kind === "suppressed") {
          tr.append(h("td", { class: "cell sup", title: L(meta.suppression_rule) }, h("span", { class: "x" }, "×"), " ", c.suppressedCell, h("br"), h("span", null, "n < " + meta.k_suppress)));
        } else {
          tr.append(h("td", { class: "cell sup", title: x.kind === "intrinsic" ? L(meta.labels.intrinsic) : "" }, x.kind === "intrinsic" ? "R*" : "·"));
        }
      }
      tbody.append(tr);
    }
    const foot = shownRows.length < m.rows.length || state.matrixAll && m.rows.length > COLLAPSED_ROWS
      ? h("div", { class: "matrix-foot" }, h("span", null, fmt(t().ask.showingRows, { shown: shownRows.length, n: m.rows.length })),
          h("button", { type: "button", class: "toolbtn", onclick: () => { state.matrixAll = !state.matrixAll; renderClinician(); } }, state.matrixAll ? t().ask.showFewer : fmt(t().ask.showAll, { n: m.rows.length })))
      : null;
    return h("div", { class: "matrix-wrap" }, h("table", { class: "matrix" }, thead, tbody), foot);
  }

  /* ---------------- Section 2: microbiologist view ---------------- */
  function sparkline(e) {
    const s = aidx.series.get(e.id) || [];
    const W = 300, H = 72, padL = 4, padR = 4, padT = 6, padB = 4;
    const el = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "none", role: "img" });
    const n = s.length; if (!n) return el;
    const x = i => padL + (i / Math.max(n - 1, 1)) * (W - padL - padR);
    const isDrift = e.family === "drift";
    const vals = isDrift ? s.map(r => Math.max(r.pct_r ?? 0, r.baseline_pct_r ?? 0)) : s.map(r => Math.max(r.count ?? 0, r.expected ?? 0));
    const maxV = Math.max(isDrift ? ameta.drift.alert.min_increase_pp : ameta.cluster.rule.min_count, ...vals);
    const y = v => H - padB - (v / maxV) * (H - padT - padB);
    el.append(svg("line", { x1: padL, x2: W - padR, y1: y(0), y2: y(0), stroke: "var(--line-2)", "stroke-width": 1 }));
    if (isDrift) {
      const path = (field) => s.map((r, i) => r[field] === null || r[field] === undefined ? null : [x(i), y(r[field])]).reduce((acc, p, i, arr) => {
        if (!p) return acc; const prev = arr[i - 1]; return acc + (prev ? " L" : " M") + p[0].toFixed(1) + " " + p[1].toFixed(1); }, "");
      el.append(svg("path", { d: path("baseline_pct_r"), fill: "none", stroke: "var(--line-2)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
      el.append(svg("path", { d: path("pct_r"), fill: "none", stroke: "var(--point)", "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    } else {
      const bw = Math.min(24, Math.max(2, (W - padL - padR) / n - 2));
      s.forEach((r, i) => { if (r.count > 0) el.append(svg("rect", { x: x(i) - bw / 2, y: y(r.count), width: bw, height: Math.max(1, y(0) - y(r.count)), rx: 1, fill: "var(--point)" })); });
      const ex = s.map((r, i) => r.expected === null || r.expected === undefined ? null : [x(i), y(r.expected)]).filter(Boolean);
      if (ex.length) el.append(svg("path", { d: ex.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" "), fill: "none", stroke: "var(--line-2)", "stroke-width": 2 }));
    }
    s.forEach((r, i) => {
      if (r.grade) {
        const v = isDrift ? r.pct_r : r.count;
        el.append(svg("circle", { cx: x(i), cy: y(v), r: 4, fill: r.grade === "watch" ? "var(--watch)" : r.grade === "alert" ? "var(--alert)" : "var(--always)", stroke: "var(--surface)", "stroke-width": 2 }));
      }
      const tip = isDrift
        ? date(r.step_end) + ": " + (r.pct_r === null || r.pct_r === undefined ? "" : pct(r.pct_r) + " (n = " + r.n + ")") + (r.baseline_pct_r !== null && r.baseline_pct_r !== undefined ? ", " + fmt(t().micro.sparkBase, { b: ameta.baseline_weeks }) + " " + pct(r.baseline_pct_r) : "") + (r.grade ? ", " + L(ameta.grades[r.grade]) : "")
        : date(r.step_end) + ": " + r.count + (r.expected !== null && r.expected !== undefined ? ", " + t().micro.sparkExpected + " " + num(r.expected, 2) : "") + (r.grade ? ", " + L(ameta.grades[r.grade]) : "");
      const hit = svg("rect", { x: x(i) - (W / n) / 2, y: 0, width: W / n, height: H, fill: "transparent" });
      hit.append(svg("title", null, tip));
      el.append(hit);
    });
    el.append(svg("text", { x: padL, y: padT + 3, "font-size": 8, fill: "var(--muted)" }, isDrift ? pct(maxV, 0) : String(Math.ceil(maxV))));
    const wrap = h("div", { class: "spark" }, el,
      h("div", { class: "axis" }, h("span", null, date(s[0].step_end)), h("span", null, date(s[n - 1].step_end))),
      h("div", { class: "lg" }, h("span", null, isDrift ? fmt(t().micro.sparkRate, { rate: HEADLINE, w: ameta.detector_window_weeks }) : fmt(t().micro.sparkCount, { d: ameta.cluster.rule.days })), h("span", { class: "base" }, isDrift ? fmt(t().micro.sparkBase, { b: ameta.baseline_weeks }) : t().micro.sparkExpected), h("span", { class: "grade" }, t().micro.sparkGrade)));
    return wrap;
  }

  function episodeKey(e) {
    if (e.family === "drift") return h("span", { class: "key" }, h("i", null, e.organism), " · " + L(meta.specimens[e.specimen]) + " · " + e.agent + " (" + L({ fi: meta.agents[e.agent].fi, en: meta.agents[e.agent].en }) + ")");
    return h("span", { class: "key" }, L(ameta.phenotypes[e.phenotype]) + " · ", h("i", null, e.organism));
  }

  function renderMicro() {
    const c = t().micro, root = document.getElementById("micro");
    let list = aidx.episodes.filter(e => (!state.alertUnit || e.unit === state.alertUnit) && (!state.alertFamily || e.family === state.alertFamily) && (!state.alertGrade || e.grade === state.alertGrade));
    list = AEI.sortEpisodes(list, aidx.rank);
    const famSel = h("select", { id: "alert-family", onchange: ev => { state.alertFamily = ev.target.value; renderMicro(); } }, h("option", { value: "" }, c.familyAll),
      ...["drift", "cluster", "always_alert"].map(f => h("option", { value: f, selected: f === state.alertFamily }, L(ameta.families[f]))));
    const gradeSel = h("select", { id: "alert-grade", onchange: ev => { state.alertGrade = ev.target.value; renderMicro(); } }, h("option", { value: "" }, c.gradeAll),
      ...Object.keys(ameta.grades).map(g => h("option", { value: g, selected: g === state.alertGrade }, L(ameta.grades[g]))));
    const alertList = h("div", { class: "alert-list" });
    for (const e of list) {
      alertList.append(h("article", { class: "alert" },
        h("div", null,
          h("div", { class: "meta" }, chip(e.grade), h("span", null, L(ameta.families[e.family])), h("span", null, h("b", null, e.unit), " " + unitShort(e.unit)), h("span", null, c.period + ": " + period(e.start, e.end) + " (" + e.steps + " " + c.steps + ")")),
          episodeKey(e),
          h("p", { class: "evi" }, L({ fi: e.evidence_fi, en: e.evidence_en }))),
        sparkline(e)));
    }
    const clusters = AEI.sortEpisodes(aidx.episodes.filter(e => e.family === "cluster"), aidx.rank);
    const clusterCols = c.clusterCols.map(x => fmt(x, { d: ameta.cluster.rule.days }));
    const always = aidx.episodes.filter(e => e.family === "always_alert").sort((a, b) => b.start.localeCompare(a.start));
    root.replaceChildren(
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, c.eyebrow), h("h2", null, c.h)), info(t().common.about, fmt(c.lead, { step: ameta.step_days, grades: gradeList() }))),
      askStrip("micro"),
      info(c.tierShort, h("p", { class: "note tier" }, c.tier)),
      h("div", { class: "controls" },
        h("div", { class: "control" }, h("label", { for: "alert-unit" }, t().clinician.unit), unitSelect(state.alertUnit, v => { state.alertUnit = v; renderMicro(); }, true)),
        h("div", { class: "control" }, h("label", { for: "alert-family" }, c.family), famSel),
        h("div", { class: "control" }, h("label", { for: "alert-grade" }, c.grade), gradeSel),
        h("span", { class: "hint" }, fmt(c.count, { n: list.length }) + ". " + c.sorted)),
      alertList,
      h("div", { class: "cols", style: "margin-top:1rem" },
        h("div", { class: "card" }, h("h3", null, c.clusters),
          h("div", { class: "table-wrap" }, h("table", { class: "plain" }, h("thead", null, h("tr", null, ...clusterCols.map((x, i) => h("th", { class: i === 4 ? "num" : "" }, x)))),
            h("tbody", null, ...clusters.map(e => h("tr", null, h("td", null, e.unit), h("td", null, h("i", null, e.organism)), h("td", null, L(ameta.phenotypes[e.phenotype])), h("td", null, period(e.start, e.end)), h("td", { class: "num" }, e.peak_count), h("td", null, chip(e.grade))))))),
          info(t().common.method, fmt(c.gridNote, { step: ameta.step_days, b: ameta.baseline_weeks, min: ameta.cluster.rule.min_count }))),
        h("div", { class: "card" }, h("h3", null, c.always),
          h("div", { class: "table-wrap" }, h("table", { class: "plain" }, h("thead", null, h("tr", null, ...c.alwaysCols.map((x, i) => h("th", { class: i === 4 ? "num" : "" }, x)))),
            h("tbody", null, ...always.map(e => h("tr", null, h("td", null, e.unit), h("td", null, h("i", null, e.organism)), h("td", null, L(ameta.phenotypes[e.phenotype])), h("td", null, period(e.start, e.end)), h("td", { class: "num" }, e.peak_count)))))))),
      h("div", { class: "cols", style: "margin-top:1rem" },
        renderQuality(),
        trendCard()),
      info(c.moreH, h("ul", { class: "tight small", style: "margin:0;padding-left:1.1rem" }, ...c.more.map(x => h("li", null, fmt(x, { specimens: specimenList() }))))));
  }

  /* The illustrative trend-indicator slot: microbiologist and stewardship views only. */
  function trendCard() {
    const c = t().micro, ti = ILLUSTRATIVE.trend_indicator;
    return foldCard(L(meta.labels.trend_indicator),
      h("p", { class: "note illustrative" }, L(ti.label).replace(/^./, ch => ch.toUpperCase()) + " · " + L(meta.labels.trend_indicator) + ": " + ti.unit + ", " + ti.organism + ", " + L(meta.specimens[ti.specimen]) + ", " + ti.agent),
      h("div", { class: "trend" },
        h("div", null, h("div", { class: "v" }, pct(ti.unit_pct_r, 0)), h("div", { class: "l" }, fmt(c.trendOwn, { weeks: ti.window_weeks }) + ", " + ti.unit_n_r + "/" + ti.unit_n)),
        h("div", null, h("div", { class: "v" }, pct(ti.parent_pct_r, 0)), h("div", { class: "l" }, fmt(c.trendParent, { rate: HEADLINE }))),
        h("div", null, h("div", { class: "v" }, pct(ti.shrunk_pct_r, 0)), h("div", { class: "l" }, c.trendShrunk)),
        h("div", null, h("div", { class: "v" }, num(ti.posterior, 2)), h("div", { class: "l" }, L(meta.labels.posterior)), h("div", { class: "meter" }, h("div", { style: "width:" + (100 * ti.posterior) + "%" })))),
      info(t().common.method, fmt(c.trendLead, { margin: num(ti.margin_pp, 0) }) + " " + L(ti.label) + "."));
  }

  function renderQuality() {
    const c = t().micro;
    const latest = AEI.latestQuality(aidx).filter(q => q.unit === meta.all.code || q.flag);
    const flagged = aidx.quality.filter(q => q.flag).sort((a, b) => (b.step_end || "").localeCompare(a.step_end || ""));
    const valueText = q => q.check === "ast_completeness" ? pct(q.value) : q.check === "eucast_mix" ? q.value + (q.detail ? " (" + q.detail + ")" : "") : String(q.value);
    return foldCard(c.quality,
      h("div", { class: "table-wrap" }, h("table", { class: "plain" },
        h("thead", null, h("tr", null, ...c.qualityCols.map(x => h("th", null, x)))),
        h("tbody", null, ...latest.map(q => h("tr", null,
          h("td", null, L(ameta.quality_checks[q.check]) + (q.unit !== meta.all.code ? " · " + q.unit : "")),
          h("td", null, q.period_start ? period(q.period_start, q.period_end) : date(q.step_end)),
          h("td", null, valueText(q)),
          h("td", null, q.check === "ast_completeness" ? pct(q.expected, 0) : q.expected === null ? "" : String(q.expected)),
          h("td", null, q.flag ? h("span", { class: "chip quality" }, h("span", { class: "dot" }), c.qualityFlag) : c.qualityOk)))))),
      h("p", { class: "small", style: "margin-top:.5rem" }, c.notComputed + " " + Object.values(ameta.quality_not_computed).map(x => L(x)).join("; ") + "."),
      h("h4", { style: "margin-top:.8rem" }, c.qualityEvents + " (" + flagged.length + ")"),
      h("ul", { class: "tight small" }, ...flagged.map(q => h("li", null, L({ fi: q.evidence_fi, en: q.evidence_en })))));
  }

  /* ---------------- Section 3: challenge and insight ---------------- */

  /* ---------------- Architecture diagrams: inline SVG from a layered grid ----------------
     A spec is rows of nodes on a column grid plus labelled edges. Nodes: {id, title, lines,
     col, span, kind: plain | ext | accent | future}. Edges: {from, to, label, dashed, accent}.
     One element per figure carries the accent: the thing the figure's claim is about. */
  const DIAGRAMS = {
    fi: {
      asis: { claim: "Nykytila: herkkyystieto lähtee LIS:stä kerran vuodessa käsin tehtynä WHONET-vientinä, vaikka tietoaltaan syöte on jo olemassa. Mikään ei lue sitä resistenssiseurantaa varten.",
        cols: 4, rows: [
          [{ id: "an", title: "Analysaattorit", lines: ["VITEK 2, kiekkodiffuusio, MALDI-TOF"], col: 1, span: 2, kind: "ext" }],
          [{ id: "lis", title: "Weblab Clinical / My+ (LIS)", lines: ["kannat, herkkyydet, tulkintarajan vuosi"], col: 1, span: 2 }],
          [{ id: "ur", title: "Uranus", lines: ["TYKS erikoissairaanhoito", "12/2026 asti"], col: 0, span: 1, kind: "ext" },
           { id: "om", title: "Omni360 / Pegasos", lines: ["perusterveydenhuolto", "2024 alkaen"], col: 1, span: 1, kind: "ext" },
           { id: "lake", title: "Varhan tietoallas", lines: ["2M-IT, yöpoiminta", "ei AMR-lukijaa"], col: 2, span: 1 },
           { id: "pdf", title: "Vuosiraportti PDF + Finres", lines: ["WHONET-vienti kerran vuodessa", "käsityönä, 12 kk viive"], col: 3, span: 1, kind: "accent" }],
          [{ id: "au", title: "Auria Tietopalvelu", lines: ["toisiokäyttö, OMOP, Atolli"], col: 2, span: 1, kind: "ext" }]],
        edges: [{ from: "an", to: "lis", label: "tulokset" }, { from: "lis", to: "ur", label: "HL7" }, { from: "lis", to: "om", label: "HL7" },
                { from: "lis", to: "lake", label: "yöpoiminta" }, { from: "lake", to: "au", label: "luvanvarainen poiminta" },
                { from: "lis", to: "pdf", label: "WHONET, 1 × vuosi", dashed: true, accent: true }] },
      target: { claim: "Tavoitetila: sama olemassa oleva syöte, Tier A -aggregointipalvelu tietoaltaassa, ja selaimeen ylittää rajan vain aggregaatti-JSON. Potilaskonteksti on erillinen järjestelmä, ei tämän tuotteen osa.",
        cols: 4, rows: [
          [{ id: "lis", title: "Weblab Clinical (LIS)", lines: ["kannat, herkkyydet, tulkintarajan vuosi"], col: 0, span: 2 },
           { id: "lake", title: "Varhan tietoallas", lines: ["2M-IT, mikroerä 15 min, 60 min"], col: 2, span: 2 }],
          [{ id: "svc", title: "AMR-aggregointipalvelu, Tier A (ei lääkinnällinen laite)", lines: ["1 kanta/potilas, EUCAST-vuosi, luontainen R pois", "kuutio: mikrobi × lääke × näytetyyppi × yksikköhierarkia × jakso", "adaptiivinen jakso ja varataso, tunnistimet, k-suojaus"], col: 0, span: 4, kind: "accent" }],
          [{ id: "view", title: "Herkkyystilastonäkymä (selain)", lines: ["Omni360 SSO, käynnistyskonteksti: rooli, hoitopaikka, kysymys", "ei potilaskontekstia, ei tunnisteita"], col: 0, span: 4 }],
          [{ id: "cl", title: "Lääkäri", lines: ["nollakyselyn oletus"], col: 0, span: 1 }, { id: "ic", title: "Infektioiden torjunta", lines: ["porautuminen ja loki"], col: 1, span: 1 },
           { id: "st", title: "Hoidonohjaus", lines: ["trendit, viennit"], col: 2, span: 1 }, { id: "it", title: "Laboratorion IT", lines: ["säännöt, laatu"], col: 3, span: 1 }],
          [{ id: "b", title: "Tier B: potilaskonteksti = MDSW IIa", lines: ["MDR 5(5) oma valmistus tai Omni360-ominaisuus, oma laatujärjestelmä"], col: 1, span: 2, kind: "future" }]],
        edges: [{ from: "lis", to: "lake", label: "olemassa oleva syöte" }, { from: "lake", to: "svc", label: "lukee rivit, ei kirjoita" },
                { from: "svc", to: "view", label: "vain aggregaatti-JSON, k ≥ 5", accent: true },
                { from: "view", to: "cl" }, { from: "view", to: "ic" }, { from: "view", to: "st" }, { from: "view", to: "it" },
                { from: "view", to: "b", label: "erillinen järjestelmä, tulevaisuus", dashed: true }] },
    },
    en: {
      asis: { claim: "As-is: susceptibility data leaves the LIS once a year as a manual WHONET export, although the data-lake feed already exists. Nothing reads it for resistance surveillance.",
        cols: 4, rows: [
          [{ id: "an", title: "Analyzers", lines: ["VITEK 2, disk diffusion, MALDI-TOF"], col: 1, span: 2, kind: "ext" }],
          [{ id: "lis", title: "Weblab Clinical / My+ (LIS)", lines: ["isolates, AST, breakpoint year"], col: 1, span: 2 }],
          [{ id: "ur", title: "Uranus", lines: ["TYKS specialised care", "until Dec 2026"], col: 0, span: 1, kind: "ext" },
           { id: "om", title: "Omni360 / Pegasos", lines: ["primary care", "since 2024"], col: 1, span: 1, kind: "ext" },
           { id: "lake", title: "Varha data lake", lines: ["2M-IT, nightly extract", "no AMR reader"], col: 2, span: 1 },
           { id: "pdf", title: "Annual PDF + Finres", lines: ["WHONET export once a year", "by hand, 12-month lag"], col: 3, span: 1, kind: "accent" }],
          [{ id: "au", title: "Auria Tietopalvelu", lines: ["secondary use, OMOP, Atolli"], col: 2, span: 1, kind: "ext" }]],
        edges: [{ from: "an", to: "lis", label: "results" }, { from: "lis", to: "ur", label: "HL7" }, { from: "lis", to: "om", label: "HL7" },
                { from: "lis", to: "lake", label: "nightly extract" }, { from: "lake", to: "au", label: "permit-based extraction" },
                { from: "lis", to: "pdf", label: "WHONET, once a year", dashed: true, accent: true }] },
      target: { claim: "Target: the same existing feed, a Tier A aggregation service inside the lake, and only aggregate JSON crosses to the browser. The patient-context module is a separate system, not part of this product.",
        cols: 4, rows: [
          [{ id: "lis", title: "Weblab Clinical (LIS)", lines: ["isolates, AST, breakpoint year"], col: 0, span: 2 },
           { id: "lake", title: "Varha data lake", lines: ["2M-IT, micro-batch 15 to 60 min"], col: 2, span: 2 }],
          [{ id: "svc", title: "AMR aggregation service, Tier A (not a medical device)", lines: ["first isolate per patient, EUCAST year, intrinsic R excluded", "cube: organism × agent × specimen × unit hierarchy × window", "adaptive window and fallback, detectors, k-suppression"], col: 0, span: 4, kind: "accent" }],
          [{ id: "view", title: "Antibiogram view (browser)", lines: ["Omni360 SSO, launch context: role, care location, question", "no patient context, no identifiers"], col: 0, span: 4 }],
          [{ id: "cl", title: "Clinician", lines: ["zero-query default"], col: 0, span: 1 }, { id: "ic", title: "Infection control", lines: ["drill-down with audit log"], col: 1, span: 1 },
           { id: "st", title: "Stewardship", lines: ["trends, exports"], col: 2, span: 1 }, { id: "it", title: "Lab IT", lines: ["rules, quality"], col: 3, span: 1 }],
          [{ id: "b", title: "Tier B: patient context = MDSW IIa", lines: ["MDR Art. 5(5) in-house or Omni360 feature, own QMS"], col: 1, span: 2, kind: "future" }]],
        edges: [{ from: "lis", to: "lake", label: "existing feed" }, { from: "lake", to: "svc", label: "reads rows, writes nothing" },
                { from: "svc", to: "view", label: "aggregate JSON only, k ≥ 5", accent: true },
                { from: "view", to: "cl" }, { from: "view", to: "ic" }, { from: "view", to: "st" }, { from: "view", to: "it" },
                { from: "view", to: "b", label: "separate system, future", dashed: true }] },
    },
  };

  function drawDiagram(spec, title) {
    const W = 960, GAP = 18, ROW_GAP = 46, LINE = 15, PAD = 10, FS = 12;
    const colW = W / spec.cols;
    const boxes = new Map();
    let y = 8;
    for (const row of spec.rows) {
      const hs = row.map(n => PAD * 2 + LINE + n.lines.length * (LINE - 1));
      const rowH = Math.max(...hs);
      for (const n of row) {
        const x = n.col * colW + GAP / 2, w = n.span * colW - GAP;
        boxes.set(n.id, { ...n, x, y, w, h: rowH });
      }
      y += rowH + ROW_GAP;
    }
    const H = y - ROW_GAP + 8;
    const el = svg("svg", { viewBox: "0 0 " + W + " " + H, role: "img", "aria-label": title + ". " + spec.claim });
    const defs = svg("defs");
    for (const [id, color] of [["arrow", "var(--ink-2)"], ["arrow-accent", "var(--accent)"]]) {
      const m = svg("marker", { id: "d-" + id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 8, markerHeight: 8, orient: "auto-start-reverse" });
      m.append(svg("path", { d: "M0 0 L10 5 L0 10 z", fill: color })); defs.append(m);
    }
    el.append(defs);
    const stroke = n => n.kind === "accent" ? "var(--accent)" : n.kind === "future" ? "var(--muted)" : "var(--line-2)";
    for (const n of boxes.values()) {
      el.append(svg("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: 6, fill: n.kind === "accent" ? "var(--accent-soft)" : n.kind === "ext" ? "var(--surface-2)" : "var(--surface)",
        stroke: stroke(n), "stroke-width": n.kind === "accent" ? 2 : 1.2, "stroke-dasharray": n.kind === "future" ? "6 4" : null }));
      el.append(svg("text", { x: n.x + n.w / 2, y: n.y + PAD + FS, "text-anchor": "middle", "font-size": FS + 1, "font-weight": 600, fill: n.kind === "future" ? "var(--ink-2)" : "var(--ink)" }, n.title));
      n.lines.forEach((ln, i) => el.append(svg("text", { x: n.x + n.w / 2, y: n.y + PAD + FS + (i + 1) * (LINE - 1) + 2, "text-anchor": "middle", "font-size": FS - 1, fill: "var(--ink-2)" }, ln)));
    }
    const edgeLabel = (x, yy, text, accent) => {
      const w = text.length * (FS - 1) * 0.56 + 10;
      el.append(svg("rect", { x: x - w / 2, y: yy - FS + 2, width: w, height: FS + 4, rx: 3, fill: "var(--surface)" }));
      el.append(svg("text", { x, y: yy, "text-anchor": "middle", "font-size": FS - 1, fill: accent ? "var(--accent-ink)" : "var(--ink-2)", "font-weight": accent ? 600 : 400 }, text));
    };
    /* Several edges leaving one box fan out from distinct points so lines never overlap. */
    const outgoing = {};
    spec.edges.forEach(e => (outgoing[e.from] ||= []).push(e));
    for (const e of spec.edges) {
      const a = boxes.get(e.from), b = boxes.get(e.to);
      const color = e.accent ? "var(--accent)" : "var(--ink-2)";
      const common = { fill: "none", stroke: color, "stroke-width": e.accent ? 2 : 1.4, "stroke-dasharray": e.dashed ? "6 4" : null, "marker-end": "url(#d-" + (e.accent ? "arrow-accent" : "arrow") + ")" };
      if (a.y === b.y) {
        const x1 = a.x + a.w, x2 = b.x, yy = a.y + a.h / 2;
        el.append(svg("path", { d: "M" + x1 + " " + yy + " L" + (x2 - 1) + " " + yy, ...common }));
        if (e.label) edgeLabel((x1 + x2) / 2, yy - 8, e.label, e.accent);
      } else {
        const siblings = outgoing[e.from].filter(o => boxes.get(o.to).y !== a.y);
        const idx2 = siblings.indexOf(e), n = siblings.length;
        const x1 = a.x + a.w * (idx2 + 1) / (n + 1), x2 = b.x + b.w / 2;
        const y1 = a.y + a.h, y2 = b.y, ym = y1 + ROW_GAP / 2;
        el.append(svg("path", { d: "M" + x1 + " " + y1 + " L" + x1 + " " + ym + " L" + x2 + " " + ym + " L" + x2 + " " + (y2 - 1), ...common }));
        if (e.label) edgeLabel(x1 === x2 ? x2 : (x1 + x2) / 2, ym - 6, e.label, e.accent);
      }
    }
    return h("figure", { class: "diagram" }, el, h("figcaption", null, spec.claim));
  }

  function renderArchitecture(root) {
    const c = t().architecture;
    const d = DIAGRAMS[state.lang];
    root.replaceChildren(
      info(t().common.about, c.lead),
      h("div", { class: "stack" },
        h("div", null, h("h3", { style: "margin-bottom:.5rem" }, c.asis), drawDiagram(d.asis, c.asis)),
        h("div", null, h("h3", { style: "margin-bottom:.5rem" }, c.target), drawDiagram(d.target, c.target))),
      h("ul", { class: "tight", style: "margin-top:1rem" }, ...c.points.map(x => h("li", null, x))));
  }

  /* ---------------- Sections 5 and 6, footer ---------------- */
  function renderFoot() {
    const c = t().foot, root = document.getElementById("foot");
    root.replaceChildren(h("p", null, c.synthetic), info(t().common.about,
      h("p", null, fmt(c.generated, { data: meta.generated.slice(0, 16).replace("T", " "), alerts: ameta.generated.slice(0, 16).replace("T", " ") })),
      h("p", null, c.refs + " N_min = " + meta.n_min + ", k = " + meta.k_suppress + ", " + L(meta.labels.window) + " " + meta.window_ladder.map(windowLabel).join(", ") + ".")));
  }

  /* ---------------- Section 3: stewardship ---------------- */
  const scopeLevel = code => code === meta.all.code ? "all" : (meta.groups[code] ? "group" : "unit");
  function scopeSelect(id, current, onPick, anyLabel) {
    const sel = h("select", { id, onchange: e => onPick(e.target.value) });
    if (anyLabel !== undefined) sel.append(h("option", { value: "", selected: current === "" }, anyLabel));
    sel.append(h("option", { value: meta.all.code, selected: current === meta.all.code }, meta.all.short + " · " + L(meta.all)));
    for (const [gcode, g] of Object.entries(meta.groups)) sel.append(h("option", { value: gcode, selected: current === gcode }, L(g)));
    const og = h("optgroup", { label: t().clinician.unit });
    for (const [code, u] of Object.entries(meta.units)) og.append(h("option", { value: code, selected: current === code }, code + " · " + L(u)));
    sel.append(og);
    return sel;
  }
  function pick(options, current, id, onPick, anyLabel) {
    const sel = h("select", { id, onchange: e => onPick(e.target.value) });
    if (anyLabel !== undefined) sel.append(h("option", { value: "", selected: current === "" }, anyLabel));
    for (const [value, label] of options) sel.append(h("option", { value, selected: value === current }, label));
    return sel;
  }
  const yearWindows = () => Object.keys(meta.windows).filter(w => w.startsWith("Y")).sort();
  const completeYears = () => yearWindows().filter(w => meta.windows[w].complete);

  function renderSteward() {
    const c = t().steward, root = document.getElementById("steward"), st = state.steward;
    const level = scopeLevel(st.scope);
    const years = yearWindows(), top = meta.window_ladder[meta.window_ladder.length - 1];
    const vol = o => idx.counts.get(k(top, level, st.scope, "all", st.specimen, o)) || 0;
    const organisms = meta.organisms.filter(o => years.some(w => idx.counts.has(k(w, level, st.scope, "all", st.specimen, o)))).sort((x, y) => vol(y) - vol(x));
    if (!organisms.includes(st.organism)) st.organism = organisms[0];
    const agents = AEI.agentsFor(meta, st.organism, st.specimen);
    const cellAt = (w, agent) => idx.cells.get(k(w, level, st.scope, "all", st.specimen, st.organism, agent));
    const yearCell = (w, cell, partial) => cell ? h("td", { class: partial ? "part" : "", title: L(meta.labels.interval) + " " + num(100 - cell.ci_s_hi) + "–" + num(100 - cell.ci_s_lo) + ", n = " + cell.n },
      h("span", { class: "yv" }, num(cell.pct_r)), h("br"), h("span", { class: "yn" }, "n = " + cell.n)) : h("td", { class: "part" }, "·");
    const bars = agent => {
      const vals = years.map(w => cellAt(w, agent)).map(cl => cl ? cl.pct_r : null);
      const maxV = Math.max(ameta.drift.alert.min_increase_pp, ...vals.filter(v => v !== null));
      const el = svg("svg", { viewBox: "0 0 80 20", preserveAspectRatio: "none", role: "img" });
      const bw = Math.min(24, 80 / years.length - 2);
      vals.forEach((v, i) => { if (v !== null) el.append(svg("rect", { x: i * (80 / years.length) + 1, y: 20 - (v / maxV) * 20, width: bw, height: Math.max(0.6, (v / maxV) * 20), rx: 1, fill: meta.windows[years[i]].complete ? "var(--point)" : "var(--band)" })); });
      return el;
    };
    const table = h("table", { class: "plain years" },
      h("thead", null, h("tr", null, h("th", null, c.agent), ...years.map(w => h("th", { class: meta.windows[w].complete ? "" : "part" }, windowLabel(w) + (meta.windows[w].complete ? "" : "*"))), h("th", null, fmt(c.trailing, { w: top.replace("w", "") })), h("th", null, ""))),
      h("tbody", null, ...agents.map(agent => h("tr", null, h("td", null, h("span", { class: "mono" }, agent), " ", h("span", { class: "small" }, L({ fi: meta.agents[agent].fi, en: meta.agents[agent].en }))),
        ...years.map(w => yearCell(w, cellAt(w, agent), !meta.windows[w].complete)), yearCell(top, cellAt(top, agent), false), h("td", null, bars(agent))))));
    /* Largest changes between the two latest complete years at this scope and specimen. */
    const cy = completeYears(), a = cy[cy.length - 1], b = cy[cy.length - 2];
    const changes = [];
    if (a && b) for (const o of meta.organisms) for (const agent of AEI.agentsFor(meta, o, st.specimen)) {
      const ca = idx.cells.get(k(a, level, st.scope, "all", st.specimen, o, agent)), cb = idx.cells.get(k(b, level, st.scope, "all", st.specimen, o, agent));
      if (ca && cb && ca.n >= meta.n_min && cb.n >= meta.n_min) changes.push({ o, agent, ca, cb, d: Math.round((ca.pct_r - cb.pct_r) * 10) / 10 });
    }
    changes.sort((x, y) => y.d - x.d);
    const changeTable = list => h("table", { class: "plain" }, h("thead", null, h("tr", null, ...c.changesCols.map((x, i) => h("th", { class: i >= 2 ? "num" : "" }, fmt(x, { a: windowLabel(a || ""), b: windowLabel(b || "") }))))),
      h("tbody", null, ...list.map(r => h("tr", null, h("td", null, h("i", null, r.o)), h("td", null, h("span", { class: "mono" }, r.agent)), h("td", { class: "num" }, pct(r.cb.pct_r) + " (" + r.cb.n + ")"), h("td", { class: "num" }, pct(r.ca.pct_r) + " (" + r.ca.n + ")"),
        h("td", { class: "num delta" }, h("span", { class: r.d > 0 ? "up" : r.d < 0 ? "down" : "" }, r.d > 0 ? "▲" : r.d < 0 ? "▼" : "="), " " + (r.d > 0 ? "+" : "") + num(r.d))))));
    root.replaceChildren(
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, c.eyebrow), h("h2", null, c.h)), info(t().common.about, c.lead)),
      askStrip("steward"),
      h("div", { class: "controls" },
        h("div", { class: "control" }, h("label", { for: "st-scope" }, c.scope), scopeSelect("st-scope", st.scope, v => { st.scope = v; renderSteward(); })),
        h("div", { class: "control" }, h("span", { class: "lbl" }, t().clinician.specimen), seg(Object.entries(meta.specimens).map(([code, sp]) => [code, L(sp)]), st.specimen, v => { st.specimen = v; renderSteward(); })),
        h("div", { class: "control" }, h("label", { for: "st-org" }, c.organism), pick(organisms.map(o => [o, o]), st.organism, "st-org", v => { st.organism = v; renderSteward(); }))),
      h("div", { class: "card" }, h("h3", null, fmt(c.yearsH, { rate: L(meta.labels.rate) }) + ": ", h("i", null, st.organism), ", " + L(meta.specimens[st.specimen]) + ", " + unitShort(st.scope)),
        h("div", { class: "table-wrap" }, table),
        info(t().common.howToRead, c.partial + ". " + fmt(c.cellNote, { rate: L(meta.labels.rate), k: meta.k_suppress }))),
      h("div", { class: "cols", style: "margin-top:1rem" },
        h("div", { class: "card" }, h("h3", null, fmt(c.changesH, { a: windowLabel(a || ""), b: windowLabel(b || "") })), info(t().common.about, fmt(c.changesLead, { nmin: meta.n_min })),
          changes.length ? h("div", null, h("h4", { style: "margin-top:.6rem" }, c.up), h("div", { class: "table-wrap" }, changeTable(changes.filter(x => x.d > 0).slice(0, 8))), h("h4", { style: "margin-top:.8rem" }, c.down), h("div", { class: "table-wrap" }, changeTable(changes.filter(x => x.d < 0).slice(-8).reverse()))) : h("p", { class: "small" }, fmt(c.none, { nmin: meta.n_min }))),
        h("div", { class: "stack" }, trendCard(), info(c.exportH, c.exportText))));
  }

  /* ---------------- Section 4: explore ---------------- */
  function renderExplore() {
    const c = t().explore, root = document.getElementById("explore"), ex = state.ex;
    const allCells = idx.cellRows || (idx.cellRows = AEI.rows(DATA.cells));
    const level = ex.scope ? scopeLevel(ex.scope) : "";
    const match = r => (!ex.scope || (r.level === level && r.unit === ex.scope)) && (!ex.specimen || r.specimen === ex.specimen) && (!ex.organism || r.organism === ex.organism)
      && (!ex.agent || r.agent === ex.agent) && (!ex.stratum || r.stratum === ex.stratum) && (!ex.window || r.window === ex.window) && r.n >= ex.minN;
    const hits = allCells.filter(match).sort((x, y) => y.n - x.n);
    const LIMIT = 300;
    const windows = Object.keys(meta.windows).map(w => [w, windowLabel(w) + (w.startsWith("Y") && !meta.windows[w].complete ? "*" : "")]);
    const re = () => { renderExplore(); };
    const cellTable = h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.cellCols.map((x, i) => h("th", { class: i >= 7 && i <= 9 ? "num" : "" }, fmt(x, { rate: L(meta.labels.rate) }))))),
      h("tbody", null, ...hits.slice(0, LIMIT).map(r => h("tr", null, h("td", null, windowLabel(r.window)), h("td", null, r.level), h("td", null, r.unit), h("td", null, L(meta.strata[r.stratum])), h("td", null, L(meta.specimens[r.specimen])), h("td", null, h("i", null, r.organism)), h("td", null, h("span", { class: "mono" }, r.agent)),
        h("td", { class: "num" }, r.n), h("td", { class: "num" }, r.n_r), h("td", { class: "num" }, h("b", null, num(r.pct_r))), h("td", null, num(100 - r.ci_s_hi) + "–" + num(100 - r.ci_s_lo)), h("td", null, r.eucast_versions.join(", ") + (r.eucast_mixed ? " ¹" : ""))))));
    const years = [...new Set(aidx.episodes.map(e => e.start.slice(0, 4)))].sort();
    const eps = AEI.sortEpisodes(aidx.episodes.filter(e => (!ex.unit || e.unit === ex.unit) && (!ex.family || e.family === ex.family) && (!ex.grade || e.grade === ex.grade) && (!ex.year || e.start.slice(0, 4) === ex.year || e.end.slice(0, 4) === ex.year)), aidx.rank);
    const epTable = h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.episodeCols.map(x => h("th", null, x)))),
      h("tbody", null, ...eps.map(e => h("tr", null, h("td", null, chip(e.grade)), h("td", null, L(ameta.families[e.family])), h("td", null, e.unit), h("td", null, h("i", null, e.organism)), h("td", null, e.specimen ? L(meta.specimens[e.specimen]) : ""), h("td", null, e.agent || (e.phenotype ? L(ameta.phenotypes[e.phenotype]) : "")),
        h("td", null, date(e.start)), h("td", null, date(e.end)), h("td", { class: "num" }, e.steps), h("td", { class: "num" }, e.family === "drift" ? pct(e.peak_pct_r) + " (n = " + e.peak_n + ")" : e.peak_count), h("td", null, pval(e.peak_p, e.family)), h("td", { class: "wrap" }, L({ fi: e.evidence_fi, en: e.evidence_en }))))));
    const scopes = idx.scopeRows.filter(sr => sr.unit === ex.scopeUnit);
    const scopeTable = h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.scopeCols.map(x => h("th", null, x)))),
      h("tbody", null, ...scopes.map(sr => h("tr", null, h("td", null, sr.unit), h("td", null, L(meta.specimens[sr.specimen])), h("td", null, h("i", null, sr.organism)), h("td", null, L(meta.strata[sr.stratum])), h("td", null, windowLabel(sr.window_used)), h("td", null, sr.level_used === "unit" ? sr.unit_used : unitShort(sr.unit_used)), h("td", { class: "num" }, sr.n === null ? "" : sr.n), h("td", { class: "wrap" }, scopeBadge(sr), " ", L({ fi: sr.reason_fi, en: sr.reason_en }))))));
    const scopeUnitSel = unitSelect(ex.scopeUnit, v => { ex.scopeUnit = v; re(); }, false, "ex-scopeunit");
    root.replaceChildren(
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, c.eyebrow), h("h2", null, c.h)), info(t().common.about, c.lead)),
      askStrip("explore"),
      h("div", { class: "sub-block" }, h("h3", null, c.cellsH),
        h("div", { class: "filters" },
          h("div", { class: "control" }, h("label", { for: "ex-scope" }, c.level), scopeSelect("ex-scope", ex.scope, v => { ex.scope = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-specimen" }, c.specimen), pick(Object.entries(meta.specimens).map(([code, sp]) => [code, L(sp)]), ex.specimen, "ex-specimen", v => { ex.specimen = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-organism" }, c.organism), pick(meta.organisms.map(o => [o, o]), ex.organism, "ex-organism", v => { ex.organism = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-agent" }, c.agent), pick(Object.keys(meta.agents).map(a => [a, a + " · " + L({ fi: meta.agents[a].fi, en: meta.agents[a].en })]), ex.agent, "ex-agent", v => { ex.agent = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-stratum" }, c.stratum), pick(Object.entries(meta.strata).map(([code, sr]) => [code, L(sr)]), ex.stratum, "ex-stratum", v => { ex.stratum = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-window" }, c.window), pick(windows, ex.window, "ex-window", v => { ex.window = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-minn" }, c.minN), h("input", { id: "ex-minn", type: "number", min: 0, step: 5, value: ex.minN, onchange: e => { ex.minN = Number(e.target.value) || 0; re(); } }))),
        h("p", { class: "resultline" }, fmt(c.showing, { shown: Math.min(hits.length, LIMIT), total: hits.length })),
        h("div", { class: "table-wrap card" }, cellTable)),
      h("div", { class: "sub-block" }, h("h3", null, c.episodesH),
        h("div", { class: "filters" },
          h("div", { class: "control" }, h("label", { for: "ex-unit" }, t().clinician.unit), unitSelect(ex.unit, v => { ex.unit = v; re(); }, true, "ex-unit")),
          h("div", { class: "control" }, h("label", { for: "ex-family" }, c.family), pick(["drift", "cluster", "always_alert"].map(f => [f, L(ameta.families[f])]), ex.family, "ex-family", v => { ex.family = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-grade" }, c.grade), pick(Object.keys(ameta.grades).map(g => [g, L(ameta.grades[g])]), ex.grade, "ex-grade", v => { ex.grade = v; re(); }, c.any)),
          h("div", { class: "control" }, h("label", { for: "ex-year" }, c.year), pick(years.map(y => [y, y]), ex.year, "ex-year", v => { ex.year = v; re(); }, c.any))),
        h("p", { class: "resultline" }, fmt(t().micro.count, { n: eps.length })),
        h("div", { class: "table-wrap card" }, epTable)),
      h("div", { class: "sub-block" }, h("h3", null, c.scopesH), info(t().common.about, c.scopesLead),
        h("div", { class: "filters" }, h("div", { class: "control" }, h("label", { for: "ex-scopeunit" }, t().clinician.unit), scopeUnitSel)),
        h("div", { class: "table-wrap card" }, scopeTable)));
  }

  /* ---------------- Section 5: lab IT ---------------- */
  function renderLabIT() {
    const c = t().labit, root = document.getElementById("labit");
    const versions = [...new Set(aidx.quality.filter(q => q.check === "eucast_mix").flatMap(q => (q.detail || "").split(", ").map(x => x.split(" ")[1]).filter(Boolean)))].sort();
    const kv = (key, value) => [h("dt", null, c.kv[key]), h("dd", null, value)];
    const archBody = h("div");
    renderArchitecture(archBody);
    const arch = foldCard(t().architecture.h, archBody);
    root.replaceChildren(
      h("div", { class: "head" }, h("div", null, h("div", { class: "eyebrow" }, c.eyebrow), h("h2", null, c.h)), info(t().common.about, c.lead)),
      h("div", { class: "stack labit-list" },
        foldCard(c.pipelineH, h("ol", { class: "tight small", style: "padding-left:1.2rem" }, ...c.pipeline.map(x => h("li", null, fmt(x, { step: ameta.step_days }))))),
        foldCard(c.configH, h("dl", { class: "kv" },
          ...kv("nmin", String(meta.n_min)), ...kv("k", String(meta.k_suppress)), ...kv("ladder", meta.window_ladder.map(windowLabel).join(", ")),
          ...kv("headline", windowLabel(meta.headline_window)), ...kv("annual", windowLabel(meta.annual_baseline_window)),
          ...kv("onset", L(meta.labels.onset_rule)), ...kv("intrinsic", meta.intrinsic_r.map(pr => pr[0] + " / " + pr[1]).join("; ")),
          ...kv("driftWatch", "n ≥ " + ameta.drift.watch.n_min + ", p < " + ameta.drift.watch.p_max), ...kv("driftAlert", "n ≥ " + ameta.drift.alert.n_min + ", p < " + ameta.drift.alert.p_max + ", +" + num(ameta.drift.alert.min_increase_pp, 0) + " pp"),
          ...kv("baseline", ameta.baseline_weeks + " " + t().clinician.weeks + " (min " + ameta.baseline_min_weeks + "), pseudocount " + num(ameta.baseline_pseudocount)),
          ...kv("cluster", "≥ " + ameta.cluster.rule.min_count + " / " + ameta.cluster.rule.days + " d, p < " + ameta.cluster.alert_p_max + "; " + L(ameta.grades.watch) + ": ≥ " + ameta.cluster.watch.min_count + ", p < " + ameta.cluster.watch.p_max),
          ...kv("always", ameta.always_alert.map(ph => L(ameta.phenotypes[ph])).join(", ")), ...kv("phenotypes", Object.keys(ameta.phenotypes).join(", ")),
          ...kv("quality", Object.values(ameta.quality_checks).map(x => L(x)).join("; ")))),
        foldCard(c.provenanceH, h("dl", { class: "kv" },
          ...kv("span", period(meta.data_start, meta.data_end)), ...kv("generated", meta.generated.slice(0, 16).replace("T", " ") + " / " + ameta.generated.slice(0, 16).replace("T", " ")),
          ...kv("cells", meta.cells_total + " / " + (meta.cells_total - meta.cells_suppressed) + " / " + meta.cells_suppressed),
          ...kv("scopes", Object.entries(meta.scopes_by_reason).map(([r, n]) => r + " " + n).join(", ")), ...kv("eucast", versions.join(", "))),
          h("h4", { style: "margin-top:.8rem" }, c.kv.dedup), h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.dedupCols.map((x, i) => h("th", { class: i ? "num" : "" }, x)))),
            h("tbody", null, ...Object.entries(meta.dedup).map(([w, d]) => h("tr", null, h("td", null, windowLabel(w)), h("td", { class: "num" }, d.before), h("td", { class: "num" }, d.after))))),
          h("h4", { style: "margin-top:.8rem" }, c.kv.cells), h("div", { class: "table-wrap" }, h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.cellsCols.map((x, i) => h("th", { class: i >= 2 ? "num" : "" }, x)))),
            h("tbody", null, ...Object.entries(meta.cells_by_window).flatMap(([w, levels]) => Object.entries(levels).map(([lv, d]) => h("tr", null, h("td", null, windowLabel(w)), h("td", null, lv), h("td", { class: "num" }, d.computed), h("td", { class: "num" }, d.written), h("td", { class: "num" }, num(d.suppressed_pct, 0))))))))),
        renderQuality(), renderAICard(),
          foldCard(c.contractH, h("ul", { class: "tight small" }, ...c.contract.map(x => h("li", null, x))),
            h("h4", { style: "margin-top:.6rem" }, c.kv.invariant), h("p", { class: "small" }, c.invariantText)),
        arch));
  }


  /* ---------------- The Ask box: the AI layer ----------------
     The router (endpoint if reachable, else the inline port) returns an intent and a slice of the
     cube's closed vocabulary. An aggregate slice becomes a selection: the clinician matrix for a
     unit, the infection-control or stewardship view when asked, Explore for everything else.
     A patient-specific request is redirected (Tier B); off-topic gets examples. No number is
     produced here: the existing renderers show the cells. */
  const askHit = (field, value) => Boolean(state.ask && state.ask.applied && state.ask.res.slice[field] === value);
  function windowName(w) {
    const c = t().ask;
    if (!w) return "";
    if (c.windowNames[w]) return c.windowNames[w];
    return meta.windows[w] ? windowLabel(w) : w.startsWith("Y") ? w.slice(1) + " (" + c.noSuchWindow + ")" : w;
  }
  function resolveAskWindow(w) {
    if (w === "annual") return meta.annual_baseline_window;
    if (w === "current_year") { const ys = yearWindows(); return ys[ys.length - 1]; }
    return meta.windows[w] ? w : null;
  }
  function describeSlice(s) {
    const c = t().ask, parts = [];
    if (s.organism) parts.push([c.fields.organism, s.organism]);
    if (s.specimen) parts.push([c.fields.specimen, L(meta.specimens[s.specimen])]);
    if (s.unit) parts.push([c.fields.unit, s.unit === "context" ? fmt(c.contextUnit, { unit: unitName(state.unit) }) : (meta.units[s.unit] ? unitName(s.unit) : unitShort(s.unit))]);
    if (s.sex) parts.push([c.fields.sex, L(meta.strata[s.sex])]);
    if (s.onset) parts.push([c.fields.onset, L(meta.strata[s.onset])]);
    if (s.window) parts.push([c.fields.window, windowName(s.window)]);
    if (s.agent && meta.agents[s.agent]) parts.push([c.fields.agent, s.agent + " · " + L({ fi: meta.agents[s.agent].fi, en: meta.agents[s.agent].en })]);
    return parts;
  }
  /* When a question names an organism but no specimen, keep the current specimen if it holds
     that organism at the scope, else switch to the specimen with most isolates for it. */
  function specimenFor(scope, organism, current) {
    if (!organism) return current;
    const level = scopeLevel(scope), top = meta.window_ladder[meta.window_ladder.length - 1];
    const vol = sp => idx.counts.get(k(top, level, scope, "all", sp, organism)) || 0;
    if (vol(current) > 0) return current;
    const best = Object.keys(meta.specimens).sort((a, b) => vol(b) - vol(a))[0];
    return vol(best) > 0 ? best : current;
  }
  function applySlice(res) {
    const c = t().ask, s = res.slice, notes = [];
    const unit = s.unit === "context" || !s.unit ? state.unit : s.unit;
    const isUnit = Boolean(meta.units[unit]);
    const view = res.view || "clinician";
    const can = id => allowed().includes(id);
    let opened;
    if (view === "micro" && can("micro")) {
      state.alertUnit = isUnit && s.unit ? unit : "";
      renderMicro(); showTab("micro");
      opened = fmt(c.opened.micro, { scope: state.alertUnit ? unitName(unit) : meta.all.short });
    } else if (view === "steward" && can("steward")) {
      const st = state.steward;
      if (s.unit) st.scope = isUnit ? unit : s.unit;
      st.specimen = s.specimen || specimenFor(st.scope, s.organism, st.specimen);
      if (s.organism) st.organism = s.organism;
      renderSteward(); showTab("steward");
      opened = fmt(c.opened.steward, { scope: unitShort(st.scope) + ", " + L(meta.specimens[st.specimen]) + ", " + st.organism });
    } else if (view === "clinician" && can("steward") && (!isUnit || (s.window && s.window !== meta.headline_window && /^(annual|Y\d{4}|52w)$/.test(s.window)))) {
      /* A group or county scope, or a yearly window, is stewardship's question when that view is open to the role. */
      const st = state.steward;
      if (s.unit && s.unit !== "context") st.scope = isUnit ? unit : s.unit;
      st.specimen = s.specimen || specimenFor(st.scope, s.organism, st.specimen);
      if (s.organism) st.organism = s.organism;
      renderSteward(); showTab("steward");
      opened = fmt(c.opened.steward, { scope: unitShort(st.scope) + ", " + L(meta.specimens[st.specimen]) + ", " + st.organism });
    } else if (view === "clinician" && isUnit && can("clinician") && (!s.window || s.window === meta.headline_window)) {
      state.unit = unit;
      if (s.specimen) state.specimen = s.specimen;
      else if (s.sex && !meta.sex_strata_specimens.includes(state.specimen)) state.specimen = meta.sex_strata_specimens[0];
      else state.specimen = specimenFor(unit, s.organism, state.specimen);
      const stratum = s.sex || s.onset || "all";
      if (SEX.includes(stratum) && !meta.sex_strata_specimens.includes(state.specimen)) {
        state.stratum = "all";
        notes.push(fmt(c.sexNote, { specimens: meta.sex_strata_specimens.map(x => L(meta.specimens[x])).join(", ") }));
      } else state.stratum = stratum;
      renderClinician(); showTab("clinician");
      opened = fmt(c.opened.clinician, { scope: unitName(state.unit) + ", " + L(meta.specimens[state.specimen]) + (state.stratum !== "all" ? ", " + L(meta.strata[state.stratum]) : "") });
      const hits = [s.organism, s.agent].filter(Boolean);
      if (hits.length) notes.push(fmt(c.highlighted, { what: hits.join(", ") }));
    } else {
      const ex = state.ex;
      ex.scope = s.unit ? (isUnit ? unit : s.unit) : ""; ex.specimen = s.specimen || ""; ex.organism = s.organism || ""; ex.agent = s.agent || "";
      ex.stratum = s.sex || s.onset || "all"; ex.minN = 0;
      const resolved = resolveAskWindow(s.window);
      ex.window = resolved || meta.window_ladder[meta.window_ladder.length - 1];
      if (s.window && !resolved) notes.push(fmt(c.noWindowNote, { window: windowName(s.window), shown: windowLabel(ex.window) }));
      ex.unit = isUnit && s.unit ? unit : ""; ex.family = view === "micro" ? "" : ex.family;
      renderExplore(); showTab("explore");
      opened = fmt(c.opened.explore, { scope: [ex.scope ? unitShort(ex.scope) : t().explore.any, ex.specimen ? L(meta.specimens[ex.specimen]) : null, ex.organism || null, windowLabel(ex.window)].filter(Boolean).join(", ") });
      if (view !== "clinician" && !can(view)) notes.push(c.roleNote);
      if (view === "clinician" && !isUnit) notes.push(c.scopeNote);
      if (view === "clinician" && isUnit && s.window && s.window !== meta.headline_window) notes.push(fmt(c.windowNote, { headline: windowLabel(meta.headline_window), window: windowName(s.window) }));
    }
    return { opened, notes, tab: state.tab };
  }
  async function ask(text) {
    text = String(text || "").trim();
    if (!text) { state.ask = null; renderAsk(); renderClinician(); return; }
    const profile = (state.aiProfiles || []).find(p => p.name === state.aiModel) || null;
    state.ask = { text, res: null, source: "rules", busy: true, applied: false, opened: null, notes: [], started: Date.now(), profile };
    renderAsk();
    const tick = setInterval(() => { const el = document.getElementById("ask-busy"); if (el && state.ask && state.ask.busy) el.textContent = fmt(t().ask.busyFor, { s: Math.round((Date.now() - state.ask.started) / 1000), model: profile ? profile.label : "" }); }, 1000);
    let res = null, source = "rules", endpointFailed = false;
    if (localPage()) {
      endpointFailed = true;
      try {
        const ctrl = new AbortController(); const timer = setTimeout(() => ctrl.abort(), profile ? profile.timeoutMs + 2000 : ASK_TIMEOUT_MS);
        const r = await fetch(AI_ENDPOINT + "/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text, lang: state.lang, unit: state.unit, debug: state.aiDebug, model: state.aiModel }), signal: ctrl.signal });
        clearTimeout(timer);
        if (r.ok) { res = await r.json(); source = res.source || "endpoint"; endpointFailed = false; }
      } catch (err) { res = null; }
    }
    clearInterval(tick);
    if (!res) res = ROUTER.route(text, state.lang, ROUTER_TABLES);
    const out = { text, res, source, endpointFailed, profile, elapsedMs: Date.now() - state.ask.started, busy: false, applied: res.intent === "aggregate", opened: null, notes: [] };
    state.ask = out;
    if (out.applied) Object.assign(out, applySlice(res));
    state.matrixAll = false;
    /* The view was drawn inside applySlice before the result was complete; draw it once more so
       the result strip and the collapsed matrix reflect the finished answer. */
    if (out.applied && out.tab) ({ clinician: renderClinician, micro: renderMicro, steward: renderSteward, explore: renderExplore }[out.tab] || (() => {}))();
    renderAsk();
    if (state.aiDebug) renderLabIT();
    const target = out.applied && out.tab ? document.getElementById(out.tab) : null;
    if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ behavior: "smooth", block: "start" });
  }
  function showAsAggregate() {
    const a = state.ask; if (!a) return;
    const res = Object.assign({}, a.res, { intent: "aggregate", view: "clinician" });
    state.ask = Object.assign({}, a, { res, applied: true, overridden: true });
    Object.assign(state.ask, applySlice(res));
    renderAsk();
  }
  const chipIntent = intent => h("span", { class: "chip " + intent }, h("span", { class: "dot", "aria-hidden": "true" }), t().ask.intents[intent]);
  function renderAsk() {
    const c = t().ask, root = document.getElementById("ask"), a = state.ask;
    const input = h("input", { id: "ask-input", type: "text", placeholder: c.placeholder, value: a ? a.text : "", autocomplete: "off",
      onkeydown: e => { if (e.key === "Enter") ask(e.target.value); } });
    const row = h("div", { class: "askrow" }, h("label", { for: "ask-input", class: "lbl" }, c.h), input,
      h("button", { type: "button", class: "btn", onclick: () => ask(input.value) }, c.button),
      a ? h("button", { type: "button", class: "btn ghost", onclick: () => ask("") }, c.clear) : null,
      modelSelect());
    const head = h("div", { class: "askhead" }, h("div", null, h("div", { class: "eyebrow" }, c.eyebrow), h("h2", { id: "ask-title" }, c.title)),
      h("span", { class: "ailabel" }, c.aiLabel));
    const more = h("details", { class: "ai" }, h("summary", null, c.aiShort), h("p", { style: "margin:.3rem 0 0" }, c.aiNote + " " + fmt(c.endpointNote, { url: AI_ENDPOINT })));
    const left = h("div", { class: "askleft" }, row, examplePicker(), more);
    let right = null;
    if (a && a.busy) right = h("div", { class: "decision" }, h("h3", null, c.decisionH), h("p", { class: "small", id: "ask-busy" }, c.busy + (a.profile && a.profile.thinking ? " " + c.busyThinking : "")));
    else if (a) right = askPanel(a);
    root.replaceChildren(head, h("div", { class: "askgrid" + (right ? " has-result" : "") }, left, right));
  }

  /* Model selector: the profiles the endpoint reported as reachable (local pages only). The
     rules in the browser are always the first option. */
  function modelSelect() {
    const c = t().ask;
    if (!state.aiProfiles || !state.aiProfiles.length) return null;
    const sel = h("select", { id: "ask-model", "aria-label": c.modelLabel, title: c.modelLabel, onchange: e => { state.aiModel = e.target.value || null; } });
    sel.append(h("option", { value: "", selected: !state.aiModel }, c.modelRules));
    for (const p of state.aiProfiles) sel.append(h("option", { value: p.name, selected: p.name === state.aiModel, disabled: !p.ok || null }, p.label + (p.ok ? "" : " (" + c.modelOffline + ")")));
    return h("div", { class: "control", style: "flex-basis:100%" }, h("span", { class: "lbl" }, c.modelLabel), sel);
  }
  /* The decision block beside the prompt: verdict, the fields that were read (with their source),
     what the page did, and a fold with the full interpretation table, cues and raw data. The
     selected data itself is shown in the view below, marked by the result strip. */
  function askPanel(a) {
    const c = t().ask, r = a.res, s = r.slice, llm = r.llm && r.llm.ok ? r.llm : null;
    const box = h("div", { class: "decision" }, h("h3", null, c.decisionH));
    const latency = llm && llm.latency_ms ? " · " + (llm.latency_ms >= 10000 ? num(llm.latency_ms / 1000, 1) + " s" : num(llm.latency_ms, 0) + " ms") : "";
    box.append(h("div", { class: "verdict" }, chipIntent(r.intent), h("span", { class: "say" }, c.verdict[r.intent]), a.overridden ? h("span", { class: "small" }, c.overridden) : null));
    box.append(h("div", { class: "meta" },
      h("span", { class: "chip source" }, h("span", { class: "dot", "aria-hidden": "true" }), (c.source[a.source] || a.source) + latency),
      r.gate && r.gate.p_patient_specific !== null && r.gate.p_patient_specific !== undefined ? h("span", null, fmt(c.gateLine, { p: num(r.gate.p_patient_specific, 2), t: num(r.gate.threshold, 2) })) : null,
      a.endpointFailed ? h("span", null, c.endpointFail) : null));
    const rulesMatched = r.matched || {};
    const fieldValue = (field, value) => {
      if (field === "organism") return value;
      if (field === "specimen") return L(meta.specimens[value]);
      if (field === "unit") return value === "context" ? fmt(c.contextUnit, { unit: unitName(state.unit) }) : (meta.units[value] ? unitName(value) : unitShort(value));
      if (field === "sex" || field === "onset") return L(meta.strata[value]);
      if (field === "window") return windowName(value);
      if (field === "agent") return meta.agents[value] ? value + " · " + L({ fi: meta.agents[value].fi, en: meta.agents[value].en }) : value;
      return String(value);
    };
    const found = Object.keys(c.fields).filter(f => s[f] !== null && s[f] !== undefined);
    if (found.length) box.append(h("div", { class: "fieldchips" }, ...found.map(f => h("span", { class: "fc" }, h("span", { class: "k" }, c.fields[f] + ":"), h("b", null, fieldValue(f, s[f])), h("span", { class: "m" }, rulesMatched[f] ? c.fieldSource.rules : (llm ? c.fieldSource.llm : c.fieldSource.rules))))));
    /* What the page did about it. */
    if (r.intent === "patient_specific" && !a.overridden) {
      box.append(h("p", { class: "note tier", style: "margin:0" }, c.tierB));
      if (s.organism || s.specimen || s.agent) box.append(h("div", { class: "actions" }, h("button", { type: "button", class: "btn ghost", onclick: showAsAggregate }, c.tierBShow)));
    } else if (r.intent === "off_topic") {
      box.append(h("p", { class: "small", style: "margin:0" }, c.offTopic));
    } else {
      box.append(h("p", { class: "did", style: "margin:0" }, a.opened || c.nothingDone));
      if (a.notes && a.notes.length) box.append(h("ul", { class: "tight small", style: "margin:0;padding-left:1.1rem" }, ...a.notes.map(n => h("li", null, n))));
      if (a.tab) box.append(h("div", { class: "actions" }, h("button", { type: "button", class: "btn ghost", onclick: () => { showTab(a.tab); const el = document.getElementById(a.tab); if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ behavior: "smooth", block: "start" }); } }, c.goto + ": " + t().tabs[a.tab])));
    }
    /* The fold: every field with its source word, cues, the model's own reading, raw JSON. */
    const defaults = a.applied && a.tab === "clinician" ? { unit: unitName(state.unit), specimen: L(meta.specimens[state.specimen]) } : {};
    const rows = Object.keys(c.fields).map(field => {
      const value = s[field];
      let shown, from, source;
      if (value !== null && value !== undefined) { shown = fieldValue(field, value); from = rulesMatched[field] || ""; source = rulesMatched[field] ? c.fieldSource.rules : (llm ? c.fieldSource.llm : c.fieldSource.rules); }
      else if (defaults[field]) { shown = fmt(c.defaultUsed, { value: defaults[field] }); from = ""; source = c.fieldSource.default; }
      else { shown = c.notFound; from = ""; source = ""; }
      return h("tr", null, h("td", null, c.fields[field]), h("td", { class: value !== null && value !== undefined ? "val" : "muted" }, shown), h("td", { class: "src" }, from), h("td", { class: "src" }, source));
    });
    const details = h("details", null, h("summary", null, c.detailsH));
    details.append(h("div", { class: "echo", style: "margin-top:.4rem" }, c.echo + ": ", h("q", null, a.text), " (" + c.langName[r.lang] + ")"));
    details.append(h("div", { class: "table-wrap", style: "margin-top:.4rem" }, h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.interpCols.map(x => h("th", null, x)))), h("tbody", null, ...rows))));
    const dl = h("dl", { class: "kv", style: "margin-top:.5rem" });
    if (r.cues && r.cues.length) dl.append(h("dt", null, c.cues), h("dd", null, r.cues.join(", ")));
    if (llm) {
      dl.append(h("dt", null, c.agreeIntent), h("dd", null, (llm.agreed_intent ? c.yes : c.no) + ", " + c.agreeSlice + ": " + (llm.agreed_slice ? c.yes : c.no)));
      dl.append(h("dt", null, c.modelVerdict), h("dd", null, c.intents[llm.intent] + (llm.view ? " · " + t().tabs[llm.view] : "") + " · " + JSON.stringify(Object.fromEntries(Object.entries(llm.slice || {}).filter(([, v]) => v !== null)))));
      if (llm.model) dl.append(h("dt", null, "model"), h("dd", null, llm.model + (llm.profile ? " (" + llm.profile + ")" : "") + (llm.reasoning_chars ? " · " + fmt(c.reasoningChars, { n: llm.reasoning_chars }) : "")));
    }
    details.append(dl);
    if (llm && llm.reasoning) details.append(h("h4", { style: "margin:.6rem 0 0;font-size:.78rem" }, c.dbg.reasoning + " (" + fmt(c.reasoningChars, { n: llm.reasoning_chars }) + ")"), h("pre", null, llm.reasoning));
    if (r.debug && r.debug.steps) details.append(h("h4", { style: "margin:.6rem 0 0;font-size:.78rem" }, c.dbg.steps),
      h("ol", { class: "tight small", style: "margin:.3rem 0 0;padding-left:1.2rem" }, ...r.debug.steps.map(x => h("li", { class: "mono", style: "font-size:.74rem" }, x))));
    details.append(h("h4", { style: "margin:.6rem 0 0;font-size:.78rem" }, c.rawH),
      h("pre", null, JSON.stringify({ intent: r.intent, view: r.view, lang: r.lang, slice: s, matched: r.matched, gate: r.gate || null, source: a.source }, null, 1)));
    box.append(details);
    return box;
  }
  /* The result strip at the top of the view a question opened: the question, what was selected
     and highlighted, and a way to clear it. */
  function askStrip(tab) {
    const a = state.ask, c = t().ask;
    if (!a || !a.applied || a.tab !== tab) return null;
    return h("div", { class: "askstrip", role: "status" }, h("span", { class: "lbl" }, c.resultStrip), h("q", null, a.text),
      h("span", { class: "small" }, [a.opened, ...(a.notes || [])].filter(Boolean).join(" ")),
      h("button", { type: "button", class: "toolbtn", onclick: () => ask("") }, c.clearStrip));
  }
  /* Example questions follow the launch-context role: one dropdown in every case. With a role,
     the groups are kinds of request (typical, minimal, noisy, redirected, off-topic) for that
     role; without a role, one group per role with a sample of its working requests, then one
     group of requests that are redirected or off-topic. Every example was checked against the
     rules and the shipped decision model. */
  function examplePicker() {
    const c = t().ask, G = c.exampleGroups;
    const sel = h("select", { id: "ask-examples", "aria-label": c.pickExample, onchange: e => { if (e.target.value) { ask(e.target.value); e.target.value = ""; } } });
    sel.append(h("option", { value: "", selected: true }, c.pickExample));
    const group = (label, items) => { const og = h("optgroup", { label }); for (const x of items) og.append(h("option", { value: x }, x)); return og; };
    if (state.role && c.examples[state.role]) {
      for (const [g, items] of Object.entries(c.examples[state.role])) sel.append(group(G[g] || g, items));
    } else {
      const seen = new Set(), uniq = xs => xs.filter(x => !seen.has(x) && seen.add(x));
      for (const [code, groups] of Object.entries(c.examples)) {
        sel.append(group(t().roles.items[code][0], uniq([...groups.typical, ...groups.minimal.slice(0, 2), ...groups.noisy.slice(0, 2)])));
      }
      sel.append(group(G.mixed, uniq(Object.values(c.examples).flatMap(groups => [...groups.blocked.slice(0, 2), groups.offtopic[0]]))));
    }
    return h("div", { class: "examples" }, sel);
  }
  /* Lab IT card: what the AI layer is, where it runs, and the recorded evaluation (numbers from
     the embedded ai_eval JSON, never typed here). */
  function renderAICard() {
    const c = t().ask, ev = AI_EVAL || {};
    const kv = (key, value) => [h("dt", null, key), h("dd", null, value)];
    const card = h("div", null, info(t().common.about, c.labitLead),
      h("dl", { class: "kv" }, ...kv(c.kv.fallback, c.kv.fallbackText), ...kv(c.kv.endpoint, AI_ENDPOINT),
        ...kv(c.kv.tables, ROUTER_TABLES.vocab.units.length + " / " + ROUTER_TABLES.vocab.agents.length + " / " + ROUTER_TABLES.vocab.organisms.length)));
    if (ev.n_requests) {
      const pctOf = x => x === null || x === undefined ? "" : pct(100 * x);
      const lat = r => r && r.latency_ms_p50 !== undefined ? num(r.latency_ms_p50, r.latency_ms_p50 < 10 ? 1 : 0) + " / " + num(r.latency_ms_p95, r.latency_ms_p95 < 10 ? 1 : 0) : "";
      const rows = [[c.evalRows.rules + " (n = " + ev.rules.n + ")", pctOf(ev.rules.intent_accuracy), pctOf(ev.rules.slice_accuracy), pctOf(ev.rules.redirect_recall), lat(ev.rules)]];
      const models = ev.models || (ev.llm && ev.llm.n ? { default: Object.assign({}, ev.llm, { combined: ev.combined }) } : {});
      for (const [name, m] of Object.entries(models)) {
        const who = (m.label || fmt(c.evalRows.llm, { model: m.model || name })) + " (n = " + m.n + (m.subset === "holdout" ? ", " + c.evalHoldout : "") + ")";
        rows.push([who, pctOf(m.intent_accuracy), pctOf(m.slice_accuracy), pctOf(m.redirect_recall), lat(m)]);
        if (m.combined) rows.push([c.evalRows.combined + " · " + (m.label || name), pctOf(m.combined.intent_accuracy), pctOf(m.combined.slice_accuracy), pctOf(m.combined.redirect_recall), lat(m.combined)]);
      }
      card.append(h("h4", { style: "margin-top:.8rem" }, fmt(c.evalH, { n: ev.n_requests, date: String(ev.generated || "").slice(0, 10) })),
        h("div", { class: "table-wrap" }, h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.evalCols.map((x, i) => h("th", { class: i ? "num" : "" }, x)))),
          h("tbody", null, ...rows.map(r => h("tr", null, ...r.map((x, i) => h("td", { class: i ? "num" : "" }, x))))))),
        h("p", { class: "small" }, c.evalNote));
      for (const [name, m] of Object.entries(models)) {
        if (!m.calibration) continue;
        const cal = m.calibration;
        card.append(h("p", { class: "small" }, h("b", null, (m.label || name) + ": "), fmt(c.calibration, { ece: num(cal.ece, 3), brier: num(cal.brier, 3), n: cal.n, pos: Math.round(cal.positive_rate * cal.n) })),
          reliabilityDiagram(cal.reliability, c.calibrationAxes));
      }
    } else card.append(h("p", { class: "small" }, c.notRun));
    card.append(h("label", { class: "small", style: "display:flex;gap:.5rem;align-items:flex-start;margin-top:.8rem" },
      h("input", { type: "checkbox", checked: state.aiDebug || null, onchange: e => { state.aiDebug = Boolean(e.target.checked); renderLabIT(); } }), c.dbg.toggle));
    if (state.aiDebug) card.append(debugBlock());
    return foldCard(c.labitH, card);
  }
  /* Debug block: the exact request and raw response of the last question when the endpoint
     answered in debug mode, the decision trace, the rules' candidates; all text, no numbers of
     the cube. Long texts sit in collapsed <details>. */
  function debugBlock() {
    const c = t().ask.dbg, a = state.ask;
    const box = h("div", { class: "block2", style: "margin-top:.6rem" }, h("h3", null, c.h));
    if (!a || !a.res) { box.append(h("p", { class: "small", style: "margin:0" }, c.none)); return box; }
    const r = a.res, llm = r.llm || null, dbg = (llm && llm.debug) || null, rdbg = r.debug || null;
    const pre = (obj, cls) => h("pre", { class: cls || "" }, typeof obj === "string" ? obj : JSON.stringify(obj, null, 1));
    const fold = (title, body, open) => { const d = h("details", { open: open || null }, h("summary", null, title)); d.append(body); return d; };
    box.append(h("p", { class: "small", style: "margin:0 0 .4rem" }, t().ask.echo + ": ", h("q", null, a.text), " · " + (t().ask.source[a.source] || a.source)));
    if (a.source === "rules" || !r.debug && !dbg) {
      box.append(h("p", { class: "small", style: "margin:0" }, c.rulesOnly));
      box.append(fold(c.normalised, pre(ROUTER.normalise(a.text).trim())));
      box.append(fold(t().ask.rawH, pre(r)));
      return box;
    }
    if (rdbg && rdbg.steps) box.append(fold(c.steps, h("ol", { class: "tight", style: "margin:.3rem 0 0;padding-left:1.2rem" }, ...rdbg.steps.map(x => h("li", { class: "mono", style: "font-size:.76rem" }, x))), true));
    if (rdbg && rdbg.rules) {
      box.append(fold(c.normalised, pre(rdbg.rules.normalised + "  [" + rdbg.rules.lang_detected + "]")));
      const nonEmpty = Object.entries(rdbg.rules.candidates || {}).filter(([, v]) => v.length);
      box.append(fold(c.candidates, pre(Object.fromEntries(nonEmpty.map(([k, v]) => [k, v.map(x => x.value + " <- " + x.token + " @" + x.pos)])))));
    }
    if (dbg) {
      const rq = dbg.request || {}, rs = dbg.response || {};
      box.append(fold(c.request + " · " + c.userMessage, pre(rq.user_message || ""), true));
      box.append(fold(c.systemPrompt + " (" + String(rq.system_prompt || "").length + " chars)", pre(rq.system_prompt || "")));
      box.append(fold(c.schema, pre(rq.schema || {})));
      box.append(fold(c.response + " · " + c.content, pre(rs.content || ""), true));
      box.append(fold(c.reasoning, rs.reasoning_content ? pre(rs.reasoning_content) : h("p", { class: "small", style: "margin:.3rem 0 0" }, c.noReasoning), true));
      const kv = h("dl", { class: "kv", style: "margin-top:.4rem" });
      kv.append(h("dt", null, c.finish), h("dd", null, String(rs.finish_reason || c.none2)));
      if (rs.usage) kv.append(h("dt", null, c.usage), h("dd", null, "prompt " + rs.usage.prompt_tokens + ", completion " + rs.usage.completion_tokens + (rs.usage.prompt_tokens_details ? ", cached " + rs.usage.prompt_tokens_details.cached_tokens : "")));
      if (rs.timings) kv.append(h("dt", null, c.timings), h("dd", { class: "mono", style: "font-size:.76rem" }, "prompt " + num(rs.timings.prompt_ms, 0) + " ms / " + rs.timings.prompt_n + " tok (" + num(rs.timings.prompt_per_second, 0) + " tok/s); predicted " + num(rs.timings.predicted_ms, 0) + " ms / " + rs.timings.predicted_n + " tok (" + num(rs.timings.predicted_per_second, 1) + " tok/s)"));
      if (rs.model) kv.append(h("dt", null, "model"), h("dd", null, rs.model));
      box.append(kv);
      if (rs.logprobs && rs.logprobs.length) {
        const table = h("table", { class: "plain dense" }, h("thead", null, h("tr", null, ...c.logprobCols.map(x => h("th", null, x)))),
          h("tbody", null, ...rs.logprobs.map((tk, i) => h("tr", null, h("td", { class: "num" }, i + 1), h("td", { class: "mono" }, JSON.stringify(tk.token)), h("td", { class: "num" }, num(tk.logprob, 2)),
            h("td", { class: "mono", style: "font-size:.72rem" }, (tk.top || []).filter(x => x.token !== tk.token).map(x => JSON.stringify(x.token) + " " + num(x.logprob, 1)).join("  "))))));
        box.append(fold(c.logprobs + " (" + rs.logprobs.length + ")", h("div", { class: "table-wrap" }, table)));
      }
    }
    box.append(fold(t().ask.rawH, pre(Object.assign({}, r, { llm: llm ? Object.assign({}, llm, { debug: undefined }) : llm, debug: undefined }))));
    return box;
  }
  /* Reliability diagram: predicted P(patient-specific) against observed frequency per bin, bin
     counts under the axis; the diagonal is perfect calibration. One hue, counts as text. */
  function reliabilityDiagram(bins, axes) {
    const W = 320, H = 200, padL = 34, padR = 8, padT = 8, padB = 44;
    const pw = W - padL - padR, ph = H - padT - padB;
    const x = v => padL + v * pw, y = v => padT + (1 - v) * ph;
    const el = svg("svg", { viewBox: "0 0 " + W + " " + H, width: "100%", style: "max-width:22rem;display:block;margin-top:.4rem", role: "img", "aria-label": axes.label });
    el.append(svg("line", { x1: x(0), y1: y(0), x2: x(1), y2: y(1), stroke: "var(--line-2)", "stroke-dasharray": "4 3" }));
    for (const b of bins) {
      const bw = (b.hi - b.lo) * pw;
      if (b.n) {
        el.append(svg("rect", { x: x(b.lo) + 1, y: y(b.accuracy), width: Math.max(bw - 2, 1), height: Math.max(y(0) - y(b.accuracy), 0.8), fill: "var(--band)", opacity: 0.85 }));
        el.append(svg("circle", { cx: x(b.confidence), cy: y(b.accuracy), r: 3, fill: "var(--point)" }));
      }
      const t = svg("text", { x: x(b.lo) + bw / 2, y: y(0) + 13, "text-anchor": "middle", "font-size": "9", fill: "var(--muted)" }); t.textContent = String(b.n); el.append(t);
    }
    el.append(svg("line", { x1: x(0), y1: y(0), x2: x(1), y2: y(0), stroke: "var(--ink-2)" }), svg("line", { x1: x(0), y1: y(0), x2: x(0), y2: y(1), stroke: "var(--ink-2)" }));
    for (const v of [0, 0.5, 1]) {
      const tx = svg("text", { x: x(v), y: y(0) + 25, "text-anchor": "middle", "font-size": "9", fill: "var(--ink-2)" }); tx.textContent = num(v, 1); el.append(tx);
      const ty = svg("text", { x: x(0) - 5, y: y(v) + 3, "text-anchor": "end", "font-size": "9", fill: "var(--ink-2)" }); ty.textContent = num(v, 1); el.append(ty);
    }
    const xl = svg("text", { x: x(0.5), y: H - 4, "text-anchor": "middle", "font-size": "9.5", fill: "var(--ink-2)" }); xl.textContent = axes.x; el.append(xl);
    const yl = svg("text", { transform: "translate(9 " + y(0.5) + ") rotate(-90)", "text-anchor": "middle", "font-size": "9.5", fill: "var(--ink-2)" }); yl.textContent = axes.y; el.append(yl);
    return el;
  }

  function renderAll() {
    renderSide(); renderAsk(); renderClinician(); renderMicro(); renderSteward(); renderExplore(); renderLabIT(); renderFoot();
    showTab(state.tab);
  }
  applyTheme();
  probeEndpoint();
  window.addEventListener("hashchange", () => { const id = location.hash.replace("#", ""); if (TABS.includes(id) && id !== state.tab) showTab(id); });
  renderAll();
})();

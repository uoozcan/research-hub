/* Research Hub front end. Vanilla JS; vis-timeline and markdown-it are vendored. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const md = window.markdownit({ html: false, linkify: true, breaks: false });
const store = {
  get(k, d) { try { const v = localStorage.getItem("hub:" + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("hub:" + k, JSON.stringify(v)); } catch { /* private mode */ } },
};
const api = async (path, opts) => {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || j.detail || r.statusText);
  return j;
};
const fileUrl = (p) => "/api/file?path=" + encodeURIComponent(p);
const status = (msg) => { $("#status").textContent = msg || ""; };
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const DAY = 86400000;
const dayStr = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);

// Codex shares the aqua slot with the strip; the timeline dots use the same mapping.
// sources in the activity strip: fixed categorical order (validated adjacent pairs)
const SOURCES = [
  { key: "git", label: "Commits", slot: "--s1", match: ["git"] },
  { key: "claude", label: "Claude Code", slot: "--s2", match: ["claude"] },
  { key: "codex", label: "Codex", slot: "--s3", match: ["codex"] },
  { key: "file", label: "File changes", slot: "--s4", match: ["file"] },
  { key: "records", label: "Decisions, reports, handoffs", slot: "--s5", match: ["decision", "report", "vault"] },
  { key: "pomodoro", label: "Pomodoros", slot: "--s6", match: ["pomodoro"] },
];
const STATUS = {
  adopted: { label: "Adopted", icon: "✓", color: "--good" },
  pending: { label: "Pending", icon: "…", color: "--warning" },
  exploratory: { label: "Exploratory", icon: "?", color: "--s7" },
  superseded: { label: "Superseded", icon: "↻", color: "--muted" },
  cancelled: { label: "Cancelled", icon: "✕", color: "--critical" },
};

const URLP = new URLSearchParams(location.search).get("p");
const S = { meta: null, project: URLP || store.get("project", "all"), tab: location.hash.slice(1) || "overview", loaded: {} };
const projName = (k) => (S.meta.projects.find((p) => p.key === k) || {}).name || k;
const projSlot = (k) => `--s${(S.meta.projects.find((p) => p.key === k) || {}).slot || 1}`;
const q = () => "project=" + encodeURIComponent(S.project);
const single = () => S.project !== "all";

// ---------- tooltip ----------
const tip = document.createElement("div");
tip.className = "tip"; tip.hidden = true; document.body.append(tip);
function showTip(html, ev) { tip.innerHTML = html; tip.hidden = false; moveTip(ev); }
function moveTip(ev) {
  const w = tip.offsetWidth, h = tip.offsetHeight;
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + w > innerWidth - 8) x = ev.clientX - w - 14;
  if (y + h > innerHeight - 8) y = ev.clientY - h - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.hidden = true; };

// ---------- shell ----------
async function boot() {
  const theme = new URLSearchParams(location.search).get("theme") || store.get("theme", null);
  if (theme) document.documentElement.dataset.theme = theme;
  S.meta = await api("/api/projects");
  const sel = $("#project");
  sel.innerHTML = `<option value="all">All projects</option>` +
    S.meta.projects.map((p) => `<option value="${p.key}">${esc(p.name)}</option>`).join("");
  if (![...sel.options].some((o) => o.value === S.project)) S.project = "all";
  sel.value = S.project;
  sel.onchange = () => showProject(sel.value);
  $$("#tabs [data-tab]").forEach((b) => (b.onclick = () => {
    const menu = b.closest("details");
    if (menu) {
      menu.open = false;
      $("summary", menu).focus();
    }
    show(b.dataset.tab);
  }));
  $$(".shell-menu").forEach((menu) => {
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !menu.open) return;
      menu.open = false;
      $("summary", menu).focus();
    });
    menu.addEventListener("toggle", () => {
      if (menu.open) $$(".shell-menu[open]").filter((other) => other !== menu).forEach((other) => { other.open = false; });
    });
  });
  document.addEventListener("pointerdown", (event) => {
    $$(".shell-menu[open]").forEach((menu) => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  $("#theme").onclick = () => {
    const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.dataset.theme = dark ? "light" : "dark"; store.set("theme", dark ? "light" : "dark");
    S.loaded = {}; show(S.tab);
  };
  $("#refresh").onclick = async () => {
    $("#refresh").disabled = true; status("Refreshing all sources… this can take a few minutes.");
    try { const r = await api("/api/refresh", { method: "POST" }); status(r.notes.find((n) => n.startsWith("total")) || "Refreshed."); S.meta = await api("/api/projects"); S.loaded = {}; show(S.tab); }
    catch (e) { status("Refresh failed: " + e.message); }
    $("#refresh").disabled = false;
  };
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  pomodoro();
  if (!S.meta.refreshed) status("No index yet. Use Refresh to read project sources.");
  show(S.tab);
}

async function roadmap() {
  const r = await api("/api/roadmap");
  $("#roadmap").innerHTML = r.text ? `<p class="hint">${esc(r.path)}</p>` + md.render(r.text) : "No plan yet.";
}

const RENDER = { overview: () => overview(), literature: () => literature(), accountability, timeline, ideas, progress, figures, kanban, brain, handoff, cv, roadmap };
async function show(tab) {
  if (!RENDER[tab]) tab = "overview";
  S.tab = tab; store.set("tab", tab);
  if (location.hash.slice(1) !== tab) history.replaceState(null, "", "#" + tab);
  document.body.dataset.view = tab;
  const overflow = $$("#more-views [data-tab]").find((button) => button.dataset.tab === tab);
  $("#more-view-label").textContent = overflow ? overflow.textContent : "More";
  $("#more-view-label").dataset.active = Boolean(overflow);
  $$("#tabs [data-tab]").forEach((button) => {
    if (button.dataset.tab === tab) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  $$(".tab").forEach((s) => s.classList.toggle("active", s.id === "tab-" + tab));
  $$(".panel").forEach((p) => (p.hidden = true));
  const key = tab + "|" + S.project;
  if (S.loaded[key] && !["overview", "literature"].includes(tab)) return;
  S.loaded[key] = true;
  try { await RENDER[tab](); } catch (e) { console.error(e); status(`${tab}: ${e.message}`); S.loaded[key] = false; }
}

// ---------- overview ----------
async function projectSnapshot(el) {
  const keys = single() ? [S.project] : S.meta.projects.map((p) => p.key);
  const since = dayStr(new Date(Date.now() - 6 * DAY));
  const evs = await api(`/api/events?${q()}&start=${since}`);
  let kb = null; try { kb = await api("/api/kanban"); } catch { /* optional */ }
  const cards = [];
  for (const k of keys) {
    const p = S.meta.projects.find((x) => x.key === k);
    const mine = evs.filter((e) => e.project === k);
    if (p.tier > 1 && !single() && !mine.length) continue;
    const c = (src) => mine.filter((e) => e.source === src).length;
    let next = "";
    if (p.has_vault) {
      try {
        const v = await api(`/api/handoff?project=${k}`);
        next = v.filter((x) => x.exists).map((x) => {
          const m = (x["HANDOFF.md"] || "").match(/## NEXT\s*\n([\s\S]*?)(\n## |$)/);
          return m ? `<div class="next"><b>${esc(x.path.split(/[\\/]/).slice(-2, -1)[0])}</b> · last: ${esc(x.last_tool || "?")}\n${esc(m[1].trim())}</div>` : "";
        }).join("");
      } catch { /* vault missing */ }
    }
    const board = kb && kb.projects ? kb.projects.find((b) => b.id === Object.keys(kb.hub_keys).find((id) => kb.hub_keys[id] === k)) : null;
    const running = board ? board.columns.Running || [] : [];
    const recent = mine.filter((e) => ["decision", "release", "report", "handoff"].includes(e.kind)).slice(-5).reverse();
    let account = null;
    try { account = await api(`/api/accountability?project=${k}`); } catch { /* optional */ }
    const fs = account?.findings?.summary;
    cards.push(`<div class="card">
      <h3><span class="dot" style="background:var(${projSlot(k)})"></span> ${esc(p.name)} ${p.read_only ? '<span class="badge">read-only</span>' : ""}</h3>
      <div class="hint">Last activity: ${esc(p.last_active || "none recorded")}</div>
      <div class="kpi">
        <div><b>${c("git")}</b><span>commits, 7 days</span></div>
        <div><b>${c("claude") + c("codex")}</b><span>AI sessions</span></div>
        <div><b>${mine.filter((e) => e.kind === "decision").length}</b><span>decisions</span></div>
        <div><b>${c("pomodoro")}</b><span>pomodoros</span></div>
      </div>
      ${(fs?.total || account?.decisions?.summary?.total) ? `<div class="account-compact">
        ${fs?.total ? `<b>${fs.non_fixed}</b> non-fixed findings of ${fs.total}` : ""}
        ${account?.decisions?.summary?.awaiting_decision ? ` · <b>${account.decisions.summary.awaiting_decision}</b> decisions awaiting review` : ""}
        ${account?.commitments?.summary?.open ? ` · <b>${account.commitments.summary.open}</b> open commitments` : ""}
        ${account?.vaults?.summary?.stale ? ` · <b class="st-open">${account.vaults.summary.stale} stale vaults</b>` : ""}
        · <a href="#accountability" data-account-project="${esc(k)}">open tracker</a></div>` : ""}
      ${account?.rerun?.exists ? rerunBlock(account.rerun, true) : (account?.rerun?.error ? `<div class="account-compact"><b>Rerun snapshot unavailable</b> · ${esc(account.rerun.error)}</div>` : "")}
      ${next}
      ${running.length ? `<h4>Running on the board</h4><ul>${running.map((r) => `<li>${esc(r.title)}</li>`).join("")}</ul>` : ""}
      ${recent.length ? `<h4>Recent milestones</h4><ul>${recent.map((e) => `<li>${esc(e.day)} · ${esc(e.kind)}: ${esc(e.title)}</li>`).join("")}</ul>` : ""}
    </div>`);
  }
  el.innerHTML = `<div class="cards">${cards.join("") || '<div class="card">Nothing in the last 7 days.</div>'}</div>`;
  $$('[data-account-project]', el).forEach((a) => (a.onclick = () => {
    S.project = a.dataset.accountProject; store.set("project", S.project); $("#project").value = S.project;
    S.loaded = {}; show("accountability");
  }));
}

// ---------- accountability ----------
async function accountability() {
  const el = $("#accountability");
  if (!single()) { el.innerHTML = '<div class="card">Choose a project to inspect its findings and blockers.</div>'; return; }
  const a = await api(`/api/accountability?${q()}`);
  const f = a.findings, s = f.summary, d = a.decisions, commitments = a.commitments, vaults = a.vaults, rerun = a.rerun;
  if (!s.total && !d.summary.total && !commitments.summary.total && !vaults.summary.total && !rerun.exists) { el.innerHTML = '<div class="card">No accountability sources are configured for this project.</div>'; return; }
  const matrix = s.by_status_severity.map((x) => `<span class="chip"><b>${x.count}</b> ${esc(x.status)} · ${esc(x.severity)}</span>`).join("");
  const order = { FATAL: 0, MAJOR: 1, MINOR: 2, EDITORIAL: 3 };
  const rows = f.rows.slice().sort((x, y) => (x.status === "fixed") - (y.status === "fixed") ||
    (order[x.severity] ?? 9) - (order[y.severity] ?? 9) || x.issue_id.localeCompare(y.issue_id));
  el.innerHTML = `${rerun.exists || rerun.error ? `<div class="card account-wide"><h2>Development rerun</h2>${rerun.exists ? rerunBlock(rerun, false) : `<p class="st-open">No snapshot: ${esc(rerun.error || "not pulled yet")}</p>`}</div>` : ""}
  ${s.total ? `<div class="card account-wide" style="margin-top:12px">
    <h2>${esc(projName(S.project))} findings</h2>
    <div class="kpi"><div><b>${s.total}</b><span>tracked</span></div><div><b>${s.non_fixed}</b><span>non-fixed</span></div></div>
    <div class="account-matrix">${matrix}</div>
    <div class="table-scroll"><table><thead><tr><th>ID</th><th>Severity</th><th>Status</th><th>Owner</th><th>Remaining work</th><th>Verified</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="${r.status === "fixed" ? "row-fixed" : ""}"><td><a href="${fileUrl(r.path)}" target="_blank" rel="noopener">${esc(r.issue_id)}</a></td><td>${esc(r.severity)}</td><td><span class="badge st-${esc(r.status.replace(/\s+/g, "-"))}">${esc(r.status)}</span></td><td>${esc(r.owner)}</td><td>${esc(r.remaining_work)}</td><td>${esc(r.last_verified)}</td></tr>`).join("")}
    </tbody></table></div>
  </div>` : ""}
  ${d.summary.total ? `<div class="card account-wide" style="margin-top:12px">
    <h2>Decision ledger</h2>
    <p>${d.summary.awaiting_decision ? `<span class="badge st-open">${d.summary.awaiting_decision} awaiting review</span>` : '<span class="badge">No proposed or draft records</span>'}</p>
    <div class="table-scroll"><table><thead><tr><th>Date</th><th>Decision</th><th>Status</th></tr></thead><tbody>
      ${d.rows.slice().reverse().map((r) => `<tr class="${r.awaiting_decision ? "row-awaiting" : ""}"><td>${esc(r.date)}</td><td><a href="${fileUrl(r.path)}" target="_blank" rel="noopener">${esc(r.title)}</a></td><td>${r.awaiting_decision ? '<span class="badge st-open">awaiting review</span> ' : ""}${esc(r.status)}</td></tr>`).join("")}
    </tbody></table></div>
  </div>` : ""}
  ${commitments.summary.total ? `<div class="card account-wide" style="margin-top:12px">
    <h2>Waiting on people and services</h2>
    <p class="hint">Sorted by age. Maintained in <a href="${fileUrl(commitments.path)}" target="_blank" rel="noopener">${esc(commitments.path)}</a>.</p>
    <div class="table-scroll"><table><thead><tr><th>Age</th><th>Commitment</th><th>Owner</th><th>Status</th><th>Due</th><th>Evidence</th></tr></thead><tbody>
      ${commitments.rows.map((r) => `<tr><td>${r.age_days == null ? "—" : r.age_days + " d"}</td><td>${esc(r.item)}</td><td>${esc(r.owner)}</td><td><span class="badge st-${esc(r.status)}">${esc(r.status)}</span></td><td>${esc(r.due)}</td><td>${esc(r.evidence)}</td></tr>`).join("")}
    </tbody></table></div>
  </div>` : ""}
  ${vaults.summary.total ? `<div class="card account-wide" style="margin-top:12px">
    <h2>AI vault freshness</h2>
    <div class="table-scroll"><table><thead><tr><th>Repository</th><th>STATE updated</th><th>Newest commit</th><th>State</th></tr></thead><tbody>
      ${vaults.rows.map((r) => `<tr><td>${esc(r.repo)}</td><td>${esc(r.state_updated || "missing")}</td><td>${esc(r.newest_commit || "unknown")}</td><td><span class="badge ${r.stale ? "st-open" : ""}">${r.stale ? "stale" : "current"}</span></td></tr>`).join("")}
    </tbody></table></div>
  </div>` : ""}`;
}

function rerunBlock(r, compact) {
  const total = Number(r.total || 0), complete = Number(r.complete || 0);
  const pct = total ? Math.min(100, 100 * complete / total) : 0;
  const failed = Object.entries(r.failed_by_class || {}).map(([k, v]) => `${esc(k)} ${v}`).join(", ") || "none";
  const eta = r.eta_hours == null ? "estimating" : r.eta_hours < 48 ? `${r.eta_hours.toFixed(1)} h` : `${(r.eta_hours / 24).toFixed(1)} d`;
  return `<div class="rerun ${r.stale ? "stale" : ""}">
    <div class="rerun-head"><b>${complete}/${total || "?"} complete</b><span>${r.stale ? `STALE · ${r.age_hours} h old` : `${r.running || 0} running · ETA ${eta}`}</span></div>
    <div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="${total}" aria-valuenow="${complete}"><span style="width:${pct}%"></span></div>
    ${compact ? "" : `<p class="hint">Controller ${esc(r.controller_job || "unknown")} ${esc(r.controller_state || "")} · free ${r.free_gib == null ? "unknown" : r.free_gib + " GiB"} · never submitted ${r.never_submitted || 0} · failures: ${failed}<br>Snapshot ${esc(r.timestamp || "")} ${r.error ? `· last pull: ${esc(r.error)}` : ""}</p>`}
  </div>`;
}

// ---------- timeline + activity strip ----------
let TL = null, ACT = [], EVENTS = [];
async function timeline() {
  const [evs, ws, act] = await Promise.all([api(`/api/events?${q()}`), api(`/api/workstreams?${q()}`), api(`/api/activity?${q()}`)]);
  EVENTS = evs; ACT = act;
  const keys = [...new Set([...evs.map((e) => e.project), ...ws.map((w) => w.project)])]
    .sort((a, b) => S.meta.projects.findIndex((p) => p.key === a) - S.meta.projects.findIndex((p) => p.key === b));
  const groups = [], items = [];
  for (const k of keys) {
    groups.push({ id: k + ":ph", content: `<b>${esc(projName(k))}</b><br><span class="hint">phases</span>` });
    groups.push({ id: k + ":ws", content: `<span class="hint">git branches</span>` });
    groups.push({ id: k + ":ms", content: `<span class="hint">milestones</span>` });
    groups.push({ id: k + ":ai", content: `<span class="hint">AI & focus</span>` });
  }
  ws.forEach((w) => items.push({
    id: "ws" + w.id, group: w.project + (w.source === "git" ? ":ws" : ":ph"), start: w.start, end: dayStr(new Date(new Date(w.end).getTime() + DAY)),
    content: esc(w.source === "git" ? w.title.split(": ").slice(1).join(": ") : w.title), className: w.source === "git" ? "it-branch" : "it-" + (w.status || "done"), title: esc(`${w.title}\n${w.start} → ${w.end}\n${w.detail || ""}`), _ws: w,
  }));
  evs.forEach((e) => {
    if (["decision", "release", "report"].includes(e.kind)) {
      items.push({ id: e.id, group: e.project + ":ms", start: e.day, type: "point", className: "it-" + e.kind,
        content: esc(e.title.length > 30 ? e.title.slice(0, 28) + "…" : e.title), title: esc(`${e.kind} · ${e.day}\n${e.title}`), _ev: e });
    } else if (["session", "handoff", "pomodoro"].includes(e.kind)) {
      const cls = e.kind === "session" ? e.tool : e.kind;
      items.push({ id: e.id, group: e.project + ":ai", start: e.ts || e.day, type: "point", className: "it-" + cls,
        content: "", title: esc(`${e.tool || e.kind} · ${e.day}\n${e.title}`), _ev: e });
    }
  });
  const el = $("#timeline");
  el.innerHTML = "";
  const days = act.map((a) => a.day);
  const end = new Date(Date.now() + 3 * DAY);
  const start = new Date(Date.now() - 60 * DAY);
  TL = new vis.Timeline(el, new vis.DataSet(items), new vis.DataSet(groups), {
    stack: true, zoomMin: 3 * DAY, orientation: "top", start, end, maxHeight: Math.max(360, innerHeight - 330),
    min: days.length ? new Date(new Date(days[0]).getTime() - 14 * DAY) : undefined, max: new Date(Date.now() + 60 * DAY),
    tooltip: { followMouse: true }, margin: { item: { horizontal: 2, vertical: 4 } }, groupHeightMode: "auto",
  });
  // Redraw the strip only when the visible window or the label width changes. Redrawing on every
  // "changed" event loops: the strip redraw nudges layout, which makes vis-timeline redraw again.
  TL.on("rangechanged", () => drawStrip());
  TL.on("changed", () => drawStrip(true));
  window.addEventListener("resize", () => drawStrip(true));
  TL.on("select", ({ items: sel }) => {
    if (!sel.length) return;
    const it = TL.itemsData.get(sel[0]);
    if (it._ev) dayPanel(it._ev.day, it._ev.id);
    else if (it._ws) openPanel($("#day-panel"), `<h3>${esc(it._ws.title)}</h3><p>${esc(it._ws.start)} → ${esc(it._ws.end)} · ${esc(it._ws.status)}</p><p class="hint">${esc(it._ws.detail || "")}<br>${esc(it._ws.ref || "")}</p>`);
  });
  $$("#tab-timeline [data-range]").forEach((b) => (b.onclick = () => {
    const r = b.dataset.range;
    if (r === "all" && days.length) TL.setWindow(new Date(days[0]), end);
    else TL.setWindow(new Date(Date.now() - (+r) * DAY), end);
  }));
  $("#strip-legend").innerHTML = SOURCES.map((s) => `<span><span class="dot" style="background:var(${s.slot})"></span>${s.label}</span>`).join("");
  drawStrip();
}

let STRIP_KEY = "";
function drawStrip(onlyIfMoved) {
  if (!TL) return;
  const host = $("#strip");
  const left = ($("#timeline .vis-panel.vis-left") || {}).offsetWidth || 0;
  const W = host.clientWidth, H = 96, padB = 16, x0 = left, plotW = Math.max(50, W - left);
  const { start, end } = TL.getWindow();
  const key = `${start.getTime()}|${end.getTime()}|${W}|${left}`;
  if (onlyIfMoved && key === STRIP_KEY) return;
  STRIP_KEY = key;
  const t0 = start.getTime(), t1 = end.getTime();
  const x = (t) => x0 + ((t - t0) / (t1 - t0)) * plotW;
  const vis = ACT.filter((a) => { const t = new Date(a.day).getTime(); return t + DAY >= t0 && t <= t1; });
  const tot = (a) => SOURCES.reduce((s, src) => s + src.match.reduce((n, m) => n + (a[m] || 0), 0), 0);
  const max = Math.max(1, ...vis.map(tot));
  const bw = Math.max(1, (DAY / (t1 - t0)) * plotW - 2);
  const y = (v) => (H - padB) * (1 - v / max);
  let svg = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Activity per day">`;
  svg += `<line x1="${x0}" x2="${W}" y1="${H - padB}" y2="${H - padB}" stroke="${css("--axis")}" />`;
  svg += `<text x="${x0 - 6}" y="10" text-anchor="end" font-size="10" fill="${css("--muted")}">${max}</text>`;
  for (const a of vis) {
    const t = new Date(a.day + "T00:00:00").getTime();
    let acc = 0; const bx = x(t) + 1;
    const segs = SOURCES.map((s) => ({ s, v: s.match.reduce((n, m) => n + (a[m] || 0), 0) })).filter((g) => g.v);
    segs.forEach((g, i) => {
      const top = y(acc + g.v), h = y(acc) - top;
      const last = i === segs.length - 1;
      svg += `<rect x="${bx}" y="${top}" width="${bw}" height="${Math.max(0, h - (last ? 0 : 1))}" rx="${last && bw > 6 ? 2 : 0}" fill="${css(g.s.slot)}" />`;
      acc += g.v;
    });
    svg += `<rect class="hit" data-day="${a.day}" x="${bx - 1}" y="0" width="${bw + 2}" height="${H}" fill="transparent" style="cursor:pointer" />`;
  }
  svg += "</svg>";
  host.innerHTML = svg;
  $$("rect.hit", host).forEach((r) => {
    const a = ACT.find((z) => z.day === r.dataset.day);
    r.onmouseenter = (ev) => showTip(`<b>${a.day}</b><br>` + SOURCES.map((s) => {
      const v = s.match.reduce((n, m) => n + (a[m] || 0), 0);
      return v ? `<span class="dot" style="background:var(${s.slot})"></span> ${s.label}: ${v}` : "";
    }).filter(Boolean).join("<br>") + `<br><span class="hint">click for details</span>`, ev);
    r.onmousemove = moveTip; r.onmouseleave = hideTip;
    r.onclick = () => dayPanel(a.day);
  });
}

function openPanel(panel, html) {
  panel.innerHTML = `<button class="close ghost" aria-label="Close">✕</button>` + html;
  panel.hidden = false;
  $(".close", panel).onclick = () => (panel.hidden = true);
}

function dayPanel(day, focusId) {
  const evs = EVENTS.filter((e) => e.day === day);
  const order = ["decision", "release", "report", "handoff", "session", "commit", "files", "pomodoro"];
  evs.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || (a.ts || "").localeCompare(b.ts || ""));
  const html = `<h3>${esc(day)}</h3><p class="hint">${evs.length} events</p>` + evs.map((e) => `
    <div class="ev" ${e.id === focusId ? 'style="background:var(--accent-wash)"' : ""}>
      <span class="badge">${esc(e.tool || e.kind)}</span> ${single() ? "" : `<span class="hint">${esc(projName(e.project))}</span>`}
      <div>${esc(e.title)}</div>
      ${e.detail ? `<small>${esc(e.detail)}</small>` : ""}
      ${e.ref && e.source === "decision" ? `<small><a href="${fileUrl(e.ref)}" target="_blank" rel="noopener">open record</a></small>` : e.ref ? `<small>${esc(e.ref)}</small>` : ""}
    </div>`).join("");
  openPanel($("#day-panel"), html);
}

// ---------- idea map ----------
async function ideas() {
  const items = await api(`/api/ideas?${q()}`);
  const svg = $("#idea-map");
  const legend = $("#idea-legend");
  const counts = {};
  items.forEach((i) => (counts[i.status] = (counts[i.status] || 0) + 1));
  const hidden = new Set(store.get("idea-hidden", []));
  legend.innerHTML = Object.entries(STATUS).map(([k, s]) =>
    `<label class="check"><input type="checkbox" data-st="${k}" ${hidden.has(k) ? "" : "checked"}>
     <span class="dot" style="background:var(${s.color})"></span>${s.icon} ${s.label} (${counts[k] || 0})</label>`).join("");
  $$("input[data-st]", legend).forEach((c) => (c.onchange = () => {
    c.checked ? hidden.delete(c.dataset.st) : hidden.add(c.dataset.st); store.set("idea-hidden", [...hidden]); draw();
  }));
  $("#idea-labels").onchange = draw;
  if (!items.length) { $("#idea-scroll").innerHTML = '<div class="card">No ideas recorded yet. Add nodes to <code>.ai-vault/ideas.yaml</code>.</div>'; return; }

  function draw() {
    const showLabels = $("#idea-labels").checked;
    const list = items.slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const laneKey = (i) => (single() ? "" : projName(i.project) + " · ") + (i.lane || "general");
    const lanes = [...new Set(list.map(laneKey))];
    const COL = 118, ROW = 96, LEFT = 150, TOP = 46;
    const W = LEFT + list.length * COL + 60, H = TOP + lanes.length * ROW + 40;
    const pos = {};
    list.forEach((i, n) => (pos[i.id + "|" + i.project] = { x: LEFT + n * COL + COL / 2, y: TOP + lanes.indexOf(laneKey(i)) * ROW + 30, i }));
    const P = (id, proj) => pos[id + "|" + proj];
    let s = "";
    // lanes
    lanes.forEach((l, k) => {
      const yy = TOP + k * ROW + 30;
      s += `<line x1="${LEFT - 10}" x2="${W - 20}" y1="${yy}" y2="${yy}" stroke="${css("--grid")}" stroke-width="6" stroke-linecap="round" />`;
      s += `<text class="lane-label" x="12" y="${yy + 4}">${esc(l)}</text>`;
    });
    // months
    let lastM = "";
    list.forEach((i, n) => {
      const m = String(i.date).slice(0, 7);
      if (m !== lastM) {
        const xx = LEFT + n * COL;
        s += `<line x1="${xx}" x2="${xx}" y1="22" y2="${H - 20}" stroke="${css("--grid")}" stroke-dasharray="2 4" />`;
        s += `<text class="month" x="${xx + 4}" y="18">${new Date(m + "-01").toLocaleString("en", { month: "short", year: "numeric" })}</text>`;
        lastM = m;
      }
    });
    // edges
    const edge = (a, b, dashed) => {
      if (!a || !b) return "";
      const mx = (a.x + b.x) / 2;
      return `<path d="M${a.x},${a.y} C${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}" fill="none" stroke="${css(dashed ? "--ink-2" : "--muted")}" stroke-width="${dashed ? 1.5 : 2}" opacity="${dashed ? 0.8 : 0.9}" ${dashed ? 'stroke-dasharray="5 4"' : ""} />`;
    };
    list.forEach((i) => {
      const me = P(i.id, i.project);
      if (i.parent) s += edge(P(i.parent, i.project), me, false);
      if (i.replaced_by) s += edge(me, P(i.replaced_by, i.project), true);
    });
    // nodes
    list.forEach((i) => {
      const p = P(i.id, i.project), st = STATUS[i.status] || STATUS.pending;
      const faded = hidden.has(i.status) ? "faded" : "";
      const hollow = i.status === "superseded" || i.status === "cancelled";
      s += `<g class="node ${faded}" data-id="${esc(i.id)}" data-proj="${esc(i.project)}" tabindex="0" role="button" aria-label="${esc(st.label + ": " + i.title)}">
        <circle cx="${p.x}" cy="${p.y}" r="17" fill="transparent" />
        <circle class="mark" cx="${p.x}" cy="${p.y}" r="11" fill="${hollow ? css("--surface") : css(st.color)}" stroke="${css(st.color)}" stroke-width="${hollow ? 3 : 2}" />
        <text x="${p.x}" y="${p.y + 4}" text-anchor="middle" font-size="12" font-weight="700" fill="${hollow ? css(st.color) : "#fff"}" style="fill:${hollow ? css(st.color) : (i.status === "pending" ? "#0b0b0b" : "#fff")}">${st.icon}</text>
        ${showLabels ? wrapLabel(i.title, p.x, p.y + 30) : ""}
      </g>`;
    });
    svg.setAttribute("width", W); svg.setAttribute("height", H); svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = s;
    $$(".node", svg).forEach((g) => {
      const it = P(g.dataset.id, g.dataset.proj).i;
      g.onmouseenter = (ev) => showTip(`<b>${esc(it.title)}</b><br>${esc(STATUS[it.status]?.label || it.status)} · ${esc(it.date)}`, ev);
      g.onmousemove = moveTip; g.onmouseleave = hideTip;
      g.onclick = g.onkeydown = (ev) => { if (ev.type === "keydown" && ev.key !== "Enter") return; ideaPanel(it, items); $$(".node", svg).forEach((n) => n.classList.toggle("sel", n === g)); };
    });
    $("#idea-table").innerHTML = `<table><thead><tr><th>Date</th><th>Status</th><th>Idea</th><th>Lane</th><th>Reason</th></tr></thead><tbody>${
      list.map((i) => `<tr><td class="num">${esc(i.date)}</td><td>${STATUS[i.status]?.icon || ""} ${esc(i.status)}</td><td>${esc(i.title)}</td><td>${esc(laneKey(i))}</td><td>${esc(i.reason || "")}</td></tr>`).join("")}</tbody></table>`;
  }
  draw();
}

function wrapLabel(t, x, y) {
  const words = String(t).split(/\s+/), lines = [""];
  for (const w of words) {
    if ((lines[lines.length - 1] + " " + w).trim().length > 18) { if (lines.length === 3) { lines[2] += "…"; break; } lines.push(w); }
    else lines[lines.length - 1] = (lines[lines.length - 1] + " " + w).trim();
  }
  return lines.map((l, k) => `<text x="${x}" y="${y + k * 13}" text-anchor="middle">${esc(l)}</text>`).join("");
}

function ideaPanel(i, all) {
  const st = STATUS[i.status] || {};
  const by = (id) => all.find((x) => x.id === id && x.project === i.project);
  const children = all.filter((x) => x.parent === i.id && x.project === i.project);
  openPanel($("#idea-panel"), `
    <h3>${esc(i.title)}</h3>
    <p><span class="dot" style="background:var(${st.color})"></span> <b>${st.icon || ""} ${esc(st.label || i.status)}</b> · ${esc(i.date)} · ${esc(i.lane || "")}${i.inferred ? ' · <span class="badge">inferred link: confirm</span>' : ""}</p>
    ${i.reason ? `<p>${esc(i.reason)}</p>` : ""}
    ${i.parent && by(i.parent) ? `<p class="hint">Branched from: ${esc(by(i.parent).title)}</p>` : ""}
    ${i.replaced_by && by(i.replaced_by) ? `<p class="hint">Replaced by: ${esc(by(i.replaced_by).title)}</p>` : ""}
    ${children.length ? `<p class="hint">Led to: ${children.map((c) => esc(c.title)).join("; ")}</p>` : ""}
    ${i.evidence ? `<p>Evidence: ${i.evidence_path ? `<a href="${fileUrl(i.evidence_path)}" target="_blank" rel="noopener">${esc(i.evidence)}</a>` : esc(i.evidence)}</p>` : ""}
    <p class="hint">Edit in ${esc(i.source_file)}</p>`);
}

// ---------- progress ----------
async function progress() {
  const seg = $$("#tab-progress [data-period]");
  const period = store.get("period", "week");
  seg.forEach((b) => { b.classList.toggle("on", b.dataset.period === period); b.onclick = () => { store.set("period", b.dataset.period); progress(); }; });
  const rows = await api(`/api/progress?${q()}&period=${period}&limit=${period === "day" ? 45 : 40}`);
  const label = (k) => period === "week" ? `Week ${k.slice(6)} · ${k.slice(0, 4)}` : period === "month" ? new Date(k + "-01").toLocaleString("en", { month: "long", year: "numeric" }) : new Date(k).toLocaleDateString("en", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  $("#progress-list").innerHTML = rows.map((r) => {
    const c = r.counts;
    const chips = SOURCES.map((s) => { const v = s.match.reduce((n, m) => n + (c[m] || 0), 0); return v ? `<span class="chip"><span class="dot" style="background:var(${s.slot})"></span>${s.label} ${v}</span>` : ""; }).join("");
    return `<div class="card">
      <h3>${esc(label(r.key))}</h3>
      ${single() ? "" : `<div class="hint">${r.projects.map(projName).map(esc).join(", ")}</div>`}
      <div>${chips}</div>
      ${r.summary ? `<div class="note" style="padding:8px 12px;margin:8px 0">${md.render(r.summary)}</div>` : ""}
      ${r.handoffs.map((h) => `<p><span class="badge">${esc(h.tool)}</span> ${esc(h.title)}<br><small class="hint">${esc(h.detail.split("\n")[0] || "")}</small></p>`).join("")}
      ${r.highlights.length ? `<ul>${r.highlights.map((h) => `<li>${esc(h)}</li>`).join("")}</ul>` : ""}
      ${r.n_commits ? `<details><summary>${r.n_commits} commits</summary><ul>${r.commits.map((t) => `<li>${esc(t)}</li>`).join("")}${r.n_commits > r.commits.length ? "<li>…</li>" : ""}</ul></details>` : ""}
      ${r.sessions.length ? `<details><summary>${r.sessions.length} AI sessions</summary><ul>${r.sessions.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details>` : ""}
    </div>`;
  }).join("") || '<div class="card">No activity recorded.</div>';
}

// ---------- figures ----------
async function figures() {
  const grid = $("#fig-grid");
  if (!single()) { grid.innerHTML = '<div class="card">Choose a project to see its figures.</div>'; $("#fig-count").textContent = ""; return; }
  const figs = await api(`/api/figures?${q()}`);
  const folders = [...new Set(figs.map((f) => f.folder))].sort();
  $("#fig-folder").innerHTML = `<option value="">All folders (${folders.length})</option>` + folders.map((f) => `<option>${esc(f)}</option>`).join("");
  const render = () => {
    const s = $("#fig-search").value.toLowerCase(), fo = $("#fig-folder").value, qu = $("#fig-quar").checked;
    const list = figs.filter((f) => (!s || (f.stem + f.folder).toLowerCase().includes(s)) && (!fo || f.folder === fo) && (qu || !f.quarantined));
    $("#fig-count").textContent = `${list.length} of ${figs.length} figures, newest first`;
    grid.innerHTML = list.map((f, n) => `<div class="fig" data-n="${figs.indexOf(f)}" tabindex="0" role="button">
      <div class="thumb">${[".png", ".jpg", ".jpeg", ".svg", ".gif", ".webp"].includes(f.kind) ? `<img loading="lazy" alt="${esc(f.stem)}" src="${fileUrl(f.display)}">` : `<span class="ph">${f.kind === ".pdf" ? "PDF" : "HTML"}</span>`}</div>
      <div class="meta"><b>${esc(f.stem)}</b><span>${esc(f.date)} · ${Object.keys(f.files).join(" ")}</span>${f.quarantined ? ' <span class="badge" style="color:var(--critical)">quarantined</span>' : ""}<br><span>${esc(f.folder)}</span></div></div>`).join("");
    $$(".fig", grid).forEach((el) => (el.onclick = el.onkeydown = (ev) => { if (ev.type === "keydown" && ev.key !== "Enter") return; viewFigure(figs[+el.dataset.n]); }));
  };
  $("#fig-search").oninput = $("#fig-folder").onchange = $("#fig-quar").onchange = render;
  render();
}

function viewFigure(f) {
  const d = $("#viewer"), body = $("#viewer-body");
  $("#viewer-title").textContent = `${f.stem} · ${f.folder}`;
  const src = fileUrl(f.display);
  $("#viewer-open").href = f.files[".pdf"] ? fileUrl(f.files[".pdf"]) : src;
  body.innerHTML = f.kind === ".pdf" ? `<embed src="${src}" type="application/pdf">` : f.kind === ".html" ? `<iframe sandbox="allow-scripts" src="${src}"></iframe>` : `<img alt="${esc(f.stem)}" src="${src}">`;
  $("#viewer-close").onclick = () => d.close();
  d.showModal();
}

// ---------- kanban ----------
async function kanban() {
  const el = $("#kanban");
  const kb = await api("/api/kanban");
  if (kb.error) { el.innerHTML = `<div class="card">${esc(kb.error)}</div>`; return; }
  const boards = kb.projects.filter((b) => !single() || kb.hub_keys[b.id] === S.project);
  el.innerHTML = `<p class="hint">Read-only copy of <code>data.json</code>. Edit with <code>kanban_cli.py</code> in WSL. Public view: <a href="${esc(kb.url)}" target="_blank" rel="noopener">${esc(kb.url)}</a></p>` +
    (boards.map((b) => `<div class="kanban-wrap"><h3>${esc(b.name)}</h3><div class="kanban-cols">${Object.entries(b.columns).map(([col, cards]) => `
      <div class="kcol"><h4><span>${esc(col)}</span><span>${cards.length}</span></h4>
      ${cards.slice().sort((a, c) => (a.order ?? 0) - (c.order ?? 0)).map((c) => `<div class="kcard">${esc(c.title)}${c.description ? `<small>${esc(c.description)}</small>` : ""}<small>${esc((c.created_at || "").slice(0, 10))}</small></div>`).join("")}
      </div>`).join("")}</div></div>`).join("") || '<div class="card">No board for this project.</div>');
}

// ---------- second brain ----------
async function brain() {
  const side = $("#brain-list"), view = $("#brain-note");
  if (!single()) { side.innerHTML = ""; view.innerHTML = "Choose a project to browse its second brain."; return; }
  const b = await api(`/api/brain?${q()}`);
  if (!b.notes.length) { side.innerHTML = ""; view.innerHTML = `No notes found at <code>${esc(b.root)}</code>.`; return; }
  const byTitle = Object.fromEntries(b.notes.map((n) => [n.title.toLowerCase(), n]));
  const tags = [...new Set(b.notes.flatMap((n) => n.tags))].sort();
  $("#brain-tag").innerHTML = `<option value="">All tags</option>` + tags.map((t) => `<option>${esc(t)}</option>`).join("");
  let current = store.get("brain:" + S.project, "00_Dashboard.md");
  const list = () => {
    const s = $("#brain-search").value.toLowerCase(), t = $("#brain-tag").value;
    const notes = b.notes.filter((n) => (!s || n.path.toLowerCase().includes(s)) && (!t || n.tags.includes(t)));
    let last = null, h = "";
    for (const n of notes) {
      if (n.folder !== last) { h += `<div class="folder">${esc(n.folder === "." ? "top level" : n.folder)}</div>`; last = n.folder; }
      h += `<button data-path="${esc(n.path)}" class="${n.path === current ? "on" : ""}">${esc(n.title)}</button>`;
    }
    side.innerHTML = h;
    $$("button", side).forEach((x) => (x.onclick = () => open(x.dataset.path)));
  };
  const open = async (path) => {
    if (!b.notes.some((n) => n.path === path)) path = b.notes[0].path;
    current = path; store.set("brain:" + S.project, path); list();
    const n = b.notes.find((x) => x.path === path);
    const note = await api(`/api/brain/note?${q()}&path=${encodeURIComponent(path)}`);
    const text = note.text.replace(/\[\[([^\]|#]+)(#[^\]|]*)?(\|([^\]]*))?\]\]/g, (_, target, __, ___, alias) => `[${(alias || target).replace(/[\[\]]/g, "")}](#wl:${encodeURIComponent(target.trim())})`);
    view.innerHTML = `<p class="hint">${esc(path)} · ${esc(n.mtime)} · <a href="${esc(note.obsidian)}">Open in Obsidian</a></p>` + md.render(text) +
      (n.backlinks.length ? `<hr><h4>Backlinks</h4><ul>${n.backlinks.map((p) => `<li><a href="#" data-bl="${esc(p)}">${esc(p)}</a></li>`).join("")}</ul>` : "");
    $$("a[href^='#wl:']", view).forEach((a) => {
      const target = decodeURIComponent(a.getAttribute("href").slice(4)).split("/").pop().toLowerCase();
      const hit = byTitle[target];
      const span = document.createElement("span");
      span.className = "wl" + (hit ? "" : " missing"); span.textContent = a.textContent;
      if (hit) span.onclick = () => open(hit.path); else span.title = "No note with this name yet";
      a.replaceWith(span);
    });
    $$("a[data-bl]", view).forEach((a) => (a.onclick = (e) => { e.preventDefault(); open(a.dataset.bl); }));
  };
  $("#brain-search").oninput = $("#brain-tag").onchange = list;
  open(current);
}

// ---------- AI handoff ----------
async function handoff() {
  const el = $("#handoff");
  const keys = single() ? [S.project] : S.meta.projects.filter((p) => p.has_vault).map((p) => p.key);
  const out = [];
  for (const k of keys) {
    let vs = [];
    try { vs = await api(`/api/handoff?project=${k}`); } catch { /* none */ }
    if (!vs.length) out.push(`<div class="card"><h3>${esc(projName(k))}</h3><p>No handoff vault configured.</p></div>`);
    for (const v of vs) {
      if (!v.exists) { out.push(`<div class="card"><h3>${esc(projName(k))}</h3><p>Vault not created yet: <code>${esc(v.path)}</code></p></div>`); continue; }
      const age = v.handoff_mtime ? Math.round((Date.now() - new Date(v.handoff_mtime).getTime()) / 3600000) : null;
      const repo = v.path.split(/[\\/]/).slice(-2, -1)[0];
      out.push(`<div class="card">
        <h3><span class="dot" style="background:var(${projSlot(k)})"></span> ${esc(projName(k))} · ${esc(repo)}</h3>
        <p><span class="badge">last: ${esc(v.last_tool || "?")}</span> <span class="hint">${age === null ? "" : age < 48 ? `${age} h ago` : `${Math.round(age / 24)} days ago`}</span></p>
        <div class="note" style="padding:8px 14px">${md.render(v["HANDOFF.md"] || "")}</div>
        <details><summary>STATE.md</summary><div class="note" style="padding:8px 14px">${md.render(v["STATE.md"] || "")}</div></details>
        <details><summary>traps.md</summary><div class="note" style="padding:8px 14px">${md.render(v["traps.md"] || "")}</div></details>
        ${v.logs.map((l) => `<details><summary>log ${esc(l.day)}</summary><pre class="block">${esc(l.text)}</pre></details>`).join("")}
        <p class="hint">${esc(v.path)}</p></div>`);
    }
  }
  el.innerHTML = out.join("") || '<div class="card">No vaults yet.</div>';
}

// ---------- CV ----------
async function cv() {
  const files = await api("/api/cv");
  const list = $("#cv-list"), view = $("#cv-view");
  if (!files.length) { list.innerHTML = ""; view.innerHTML = "No CV files found (see cv_globs in hub.toml)."; return; }
  const open = async (f) => {
    $$(".item", list).forEach((b) => b.classList.toggle("on", b.dataset.path === f.path));
    if (f.ext === ".pdf") { view.innerHTML = `<embed src="${fileUrl(f.path)}" type="application/pdf">`; return; }
    view.innerHTML = "Rendering…";
    try { const r = await api("/api/cv/html?path=" + encodeURIComponent(f.path)); view.innerHTML = `<p class="hint">${esc(f.path)} · <a href="${fileUrl(f.path)}">download .docx</a></p>` + r.html; }
    catch (e) { view.textContent = "Could not render: " + e.message; }
  };
  list.innerHTML = `<p class="hint">Newest first. The newest .docx opens by default.</p>` + files.map((f) => `<button class="item" data-path="${esc(f.path)}"><b>${esc(f.name)}</b><br><span class="hint">${esc(f.mtime)} · ${esc(f.folder)}</span></button>`).join("");
  $$(".item", list).forEach((b) => (b.onclick = () => open(files.find((f) => f.path === b.dataset.path))));
  open(files.find((f) => f.ext === ".docx") || files[0]);
}

// ---------- pomodoro ----------
function pomodoro() {
  const box = $("#pomo"), out = $("#pomo-time"), go = $("#pomo-go");
  const sel = document.createElement("select");
  if (!S.meta.projects.some((p) => !p.read_only)) {
    box.textContent = "Add a writable project to log focus sessions.";
    return;
  }
  sel.id = "pomo-project"; sel.title = "Project this pomodoro counts toward"; sel.setAttribute("aria-label", "Pomodoro project");
  sel.innerHTML = S.meta.projects.filter((p) => !p.read_only).map((p) => `<option value="${p.key}">${esc(p.name)}</option>`).join("");
  box.insertBefore(sel, $(".pomo-modes"));
  const st = Object.assign({ min: 25, endAt: null, left: 25 * 60, startedAt: null, project: sel.options[0].value }, store.get("pomo", {}));
  if (![...sel.options].some((option) => option.value === st.project)) st.project = sel.options[0].value;
  sel.value = st.project; sel.onchange = () => { st.project = sel.value; save(); };
  const save = () => store.set("pomo", st);
  const fmt = (s) => `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  const setMode = (m) => { st.min = m; st.endAt = null; st.left = m * 60; st.startedAt = null; save(); paint(); };
  $$(".pomo-modes button").forEach((b) => (b.onclick = () => setMode(+b.dataset.min)));
  go.onclick = () => {
    if (st.endAt) { st.left = Math.max(0, (st.endAt - Date.now()) / 1000); st.endAt = null; }
    else { st.endAt = Date.now() + st.left * 1000; st.startedAt = st.startedAt || new Date().toISOString(); if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); }
    box.classList.remove("done"); save(); paint();
  };
  $("#pomo-reset").onclick = () => setMode(st.min);
  const beep = () => { try { const a = new AudioContext(), o = a.createOscillator(), g = a.createGain(); o.connect(g); g.connect(a.destination); o.frequency.value = 880; g.gain.setValueAtTime(0.15, a.currentTime); g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + 1.2); o.start(); o.stop(a.currentTime + 1.2); } catch { /* no audio */ } };
  const finish = async () => {
    const focus = st.min >= 20;
    beep(); box.classList.add("done");
    if ("Notification" in window && Notification.permission === "granted") new Notification(focus ? "Focus session done. Take a break." : "Break over.");
    if (focus) {
      try { await api("/api/pomodoro", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: st.project, started: st.startedAt, ended: new Date().toISOString(), minutes: st.min }) }); }
      catch (e) { status("Pomodoro not saved: " + e.message); }
      today(); S.loaded = {};
    }
    st.endAt = null; st.left = st.min * 60; st.startedAt = null; save();
  };
  const paint = () => {
    const left = st.endAt ? Math.max(0, (st.endAt - Date.now()) / 1000) : st.left;
    out.textContent = fmt(Math.ceil(left));
    const summary = $("#focus-summary-time");
    summary.textContent = out.textContent;
    summary.hidden = !st.endAt && !st.startedAt;
    box.classList.toggle("running", !!st.endAt);
    go.textContent = st.endAt ? "❚❚" : "▶"; go.setAttribute("aria-label", st.endAt ? "Pause" : "Start");
    $$(".pomo-modes button").forEach((b) => b.classList.toggle("on", +b.dataset.min === st.min));
    document.title = st.endAt ? `${fmt(Math.ceil(left))} · Research Hub` : "Research Hub";
    if (st.endAt && left <= 0) finish();
  };
  const today = async () => { try { const t = await api("/api/pomodoro/today"); $("#pomo-today").textContent = t.items.length ? `${t.items.length}× · ${Math.round(t.minutes)} min today` : ""; } catch { /* offline */ } };
  setInterval(paint, 250); paint(); today();
}

window.addEventListener("DOMContentLoaded", () => {
  boot().catch((e) => status("Failed to start: " + e.message));
});

/* Research Hub Literature view and shared project selection. */
"use strict";

let morningLiteratureRequest = 0;
let morningDate = new URLSearchParams(location.search).get("brief") || "";
let morningPoll = 0;

const eqProject = (item) => S.project === "all" || item.project === S.project;
const litQuery = () => `project=${encodeURIComponent(S.project)}${morningDate ? "&day=" + encodeURIComponent(morningDate) : ""}`;
const muted = (message) => `<p class="rh-empty">${esc(message)}</p>`;
const statusLabel = (state) => ({
  complete: "Complete", partial: "Partial", failed: "Failed", running: "Running",
  unavailable: "Unavailable", interrupted: "Interrupted", empty: "No items", pending: "Pending"
}[state] || state || "Waiting");
const externalLink = (url, text) => {
  try {
    if (new URL(url).protocol === "https:") {
      return `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
    }
  } catch { /* incomplete record */ }
  return esc(text);
};
const briefDate = (day) => {
  const date = new Date(day + "T12:00:00Z");
  return Number.isNaN(date.getTime()) ? day : new Intl.DateTimeFormat(undefined, {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC"
  }).format(date);
};
const reportItems = (report, key) => {
  if (!report) return [];
  const groups = key === "all"
    ? [...Object.values(report.projects), report.bioinformatics, report.biology, report.newsletters]
    : [report.projects[key] || []];
  return [...new Map(groups.flat().map((item) => [item.identity, item])).values()];
};

function showProject(key, tab = S.tab) {
  S.project = key;
  $("#project").value = key;
  store.set("project", key);
  S.loaded = {};
  const url = new URL(location.href);
  if (key === "all") url.searchParams.delete("p");
  else url.searchParams.set("p", key);
  history.replaceState(null, "", url);
  show(tab);
}

function paperEntry(item, ai) {
  const summary = ai?.summaries?.[item.identity] || {};
  return `<li class="rh-paper">
    <small>${esc([item.kind, item.source, item.published?.slice(0, 10)].filter(Boolean).join(" · "))}</small>
    <h3>${externalLink(item.url, item.title)}</h3>
    ${summary.finding || item.summary ? `<p>${esc(summary.finding || item.summary)}</p>` : ""}
    ${summary.relevance || item.reason ? `<p class="rh-relevance">${esc(summary.relevance || item.reason)}</p>` : ""}
    <p class="rh-caveat">${esc(summary.limitation || item.caveat)}</p>
    ${item.related?.length ? `<p class="rh-caveat">Related work · ${item.related.map((p) => externalLink(p.url, p.title)).join(" · ")}</p>` : ""}
  </li>`;
}

function paperGroup(name, items, ai) {
  return `<section class="rh-panel"><div class="rh-panel-head"><h2>${esc(name)}</h2></div>
    ${items.length ? `<ul class="rh-paper-list">${items.map((item) => paperEntry(item, ai)).join("")}</ul>` : muted("No selections in this briefing.")}
  </section>`;
}

function ideaEntry(idea, sources, day = "") {
  const category = idea.kind === "current_project" ? projName(idea.project) : "Potential new project";
  return `<li class="rh-paper rh-idea">
    <small>${esc(category)}${idea.day ? ` · ${esc(idea.day)}` : ""}</small>
    <h3>${esc(idea.title)}</h3>
    <p>${esc(idea.hypothesis)}</p>
    <dl>
      <div><dt>First test</dt><dd>${esc(idea.first_test)}</dd></div>
      <div><dt>Success criterion</dt><dd>${esc(idea.success_criterion)}</dd></div>
      <div><dt>Uncertainty</dt><dd>${esc(idea.uncertainty)}</dd></div>
    </dl>
    <p class="rh-caveat">Evidence · ${idea.record_ids.map((id) => {
      const record = sources.get(id) || idea.evidence?.[id];
      return record ? externalLink(record.url, record.title) : esc(id);
    }).join(" · ")}</p>
    ${day ? `<button data-save-idea="${esc(idea.id)}" data-day="${esc(day)}">Save idea</button>` : ""}
  </li>`;
}

function renderQueue(data) {
  const rows = data.queue.filter(eqProject);
  const projects = [...new Set(rows.filter((row) => row.decision === "accepted" && !row.zotero).map((row) => row.project))];
  return `<section class="rh-panel rh-queue">
    <div class="rh-panel-head"><h2>Paper filing</h2><button data-brief-action="retry" ${data.counts.pending && data.filing_enabled ? "" : "disabled"}>Retry pending</button></div>
    ${data.filing_enabled ? "" : muted("Enable filing in hub.toml to save or retry library entries.")}<p class="rh-small">${data.counts.review} to review · ${data.counts.pending} pending · ${data.counts.filed} filed in both</p>
    ${projects.length ? `<p class="rh-ris">${projects.map((key) => `<a href="/api/literature/ris?project=${encodeURIComponent(key)}">${esc(projName(key))} RIS</a>`).join(" · ")}</p>` : ""}
    ${rows.length ? `<ul class="rh-paper-list">${rows.map((row) => {
      const {paper} = row;
      const note = S.meta.projects.find((p) => p.key === row.project);
      const state = row.decision === "review" ? esc(paper.reason) : `Obsidian ${row.note ? "saved" : "pending"} · Zotero ${row.zotero ? "saved" : "pending"}`;
      return `<li class="rh-paper">
        <small>${esc(note?.name || row.project)} · ${esc(row.day)} · ${esc(paper.kind)}</small>
        <h3>${externalLink(paper.url, paper.title)}</h3>
        <p class="rh-caveat">${state}</p>
        ${row.note_error || row.zotero_error ? `<p class="rh-caveat">${[row.note_error, row.zotero_error].filter(Boolean).map(esc).join(" · ")}</p>` : ""}
        ${row.note ? `<p class="rh-note-path" title="${esc(row.note)}">${esc(row.note)}</p>` : ""}
        ${row.decision === "review" ? `<div class="rh-actions" data-project="${esc(row.project)}" data-record="${esc(paper.identity)}">
          <button data-paper-action="save" ${data.filing_enabled ? "" : "disabled"}>Save</button><button data-paper-action="dismiss">Dismiss</button>
        </div>` : ""}
      </li>`;
    }).join("")}</ul>` : muted("No papers awaiting filing or review.")}
  </section>`;
}

async function literature() {
  const request = ++morningLiteratureRequest;
  const target = S.project;
  const root = $("#tab-literature");
  const data = await api(`/api/literature?${litQuery()}`);
  if (request !== morningLiteratureRequest || target !== S.project || S.tab !== "literature") return;
  const report = data.report;
  const groups = report ? Object.entries(report.projects).filter(([key]) => target === "all" || key === target) : [];
  const showHighlights = report && target === "all";
  const records = new Map(reportItems(report, "all").map((item) => [item.identity, item]));
  const saved = new Set(data.saved_ideas.map((idea) => idea.id));
  const forProject = (idea) => target === "all" || idea.project === target;
  const runMessage = data.active ? `Today · ${statusLabel(data.active.state)}` : "Run when a source is configured";
  root.innerHTML = `
    <div class="rh-literature">
      <header class="rh-page-title">
        <div><h1>Literature</h1><p class="rh-small">${esc(runMessage)}${data.latest_complete ? ` · Last complete ${esc(briefDate(data.latest_complete))}` : ""}</p></div>
        <div class="rh-actions">
          ${data.dates.length ? `<label>Briefing <select id="rh-briefing-date">${data.dates.map((day) =>
            `<option value="${esc(day)}" ${day === report?.day ? "selected" : ""}>${esc(briefDate(day))}</option>`).join("")}</select></label>` : ""}
          <button class="rh-primary" data-brief-action="run" ${!data.collection_configured || data.active?.state === "running" ? "disabled" : ""}>Run now</button>
        </div>
      </header>
      <div aria-live="polite" id="rh-operation" class="rh-small"></div>
      <div class="rh-literature-grid">
        <div class="rh-stack">
          ${!report ? `<section class="rh-panel">${muted("No briefing available. Run a collection to get started.")}</section>` : ""}
          ${groups.map(([key, items]) => paperGroup(projName(key), items, report.ai)).join("")}
          ${target !== "all" && report && !groups.length ? `<section class="rh-panel">${muted("This project has no configured literature profile.")}</section>` : ""}
          ${showHighlights ? [
            paperGroup("Bioinformatics advances", report.bioinformatics, report.ai),
            paperGroup("Biology highlights", report.biology, report.ai),
            paperGroup("Newsletters", report.newsletters, report.ai),
          ].join("") : ""}
        </div>
        <div class="rh-stack">
          <section class="rh-panel">
            <div class="rh-panel-head"><h2>Morning ideas</h2></div>
            ${report?.ai.status === "unavailable" ? muted(report.ai.detail) : ""}
            ${report?.ideas.some(forProject) ? `<ul class="rh-paper-list">${report.ideas.filter(forProject).map((idea) =>
              ideaEntry(idea, records, saved.has(idea.id) ? "" : report.day)).join("")}</ul>` : muted("No ideas in this view.")}
            ${data.saved_ideas.some(forProject) ? `<details class="rh-saved"><summary>Saved ideas</summary><ul class="rh-paper-list">${
              data.saved_ideas.filter(forProject).map((idea) => ideaEntry(idea, records)).join("")}</ul></details>` : ""}
          </section>
          <section class="rh-panel">
            <div class="rh-panel-head"><h2>Source coverage</h2></div>
            ${report?.sources.length ? `<ul class="rh-source-list">${report.sources.map((source) => `<li>
              <div><strong>${esc(source.name)}</strong><small>${source.count} fetched${source.detail ? ` · ${esc(source.detail)}` : ""}</small></div>
              <span class="rh-mark ${source.status === "failed" ? "rh-urgent" : ""}">${esc(statusLabel(source.status))}</span>
            </li>`).join("")}</ul>` : muted("Coverage appears after collection finishes.")}
          </section>
          ${renderQueue(data)}
        </div>
      </div>
    </div>`;

  const date = $("#rh-briefing-date", root);
  if (date) date.onchange = () => {
    morningDate = date.value;
    const url = new URL(location.href);
    url.searchParams.set("brief", morningDate);
    history.replaceState(null, "", url);
    literature().catch((error) => status(error.message));
  };
  $$("[data-brief-action]", root).forEach((button) => button.onclick = () => briefAction(button, button.dataset.briefAction));
  $$("[data-paper-action]", root).forEach((button) => {
    button.onclick = async () => {
      const group = button.closest(".rh-actions");
      await briefAction(button, "decision", {project: group.dataset.project, record: group.dataset.record, action: button.dataset.paperAction});
    };
  });
  $$("[data-save-idea]", root).forEach((button) => {
    button.onclick = () => briefAction(button, "ideas", {day: button.dataset.day, id: button.dataset.saveIdea});
  });
}

async function briefAction(button, action, body) {
  button.disabled = true;
  try {
    await api(`/api/literature/${action}${action === "retry" ? "?" + q() : ""}`, {
      method: "POST",
      ...(body ? {headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {})
    });
    await literature();
    if (action !== "ideas" && !(action === "decision" && body.action === "dismiss")) {
      const running = action === "run";
      const message = running ? "Checking for new literature…" : "Checking pending destinations…";
      $("#rh-operation").textContent = message;
      window.clearTimeout(morningPoll);
      const started = Date.now();
      const poll = async () => {
        if (S.tab !== "literature") return;
        try {
          await literature();
          const keep = Date.now() - started < (running ? 720000 : 12000)
            && (!running || !!$('.rh-literature [data-brief-action="run"]:disabled') || Date.now() - started < 5000);
          if (keep && S.tab === "literature") {
            $("#rh-operation").textContent = message;
            morningPoll = window.setTimeout(poll, running ? 3500 : 2500);
          }
        } catch (error) { status(error.message); }
      };
      morningPoll = window.setTimeout(poll, 1600);
    }
  } catch (error) {
    const output = $("#rh-operation");
    if (output) output.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

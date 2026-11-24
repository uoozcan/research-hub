/* Interactive Overview. Indexed dates are civil dates in the Hub's configured time zone. */
"use strict";

const activityCategories = {
  commit: "Commits", release: "Releases", decision: "Decisions", report: "Reports",
  handoff: "Handoffs", files: "File groups", ai: "AI sessions", focus: "Focus sessions", other: "Other activity",
};
const activityBadges = {
  commit: "Commit", release: "Release", decision: "Decision", report: "Report",
  handoff: "Handoff", files: "File group", ai: "AI session", focus: "Focus session", other: "Activity",
};
const activityMonth = new Intl.DateTimeFormat(undefined, {month: "short", timeZone: "UTC"});
const activityNumber = new Intl.NumberFormat();
const isoDay = (ms) => new Date(ms).toISOString().slice(0, 10);
const asDay = (s) => Date.parse(s + "T00:00:00Z");
const activityCount = (count) => `${activityNumber.format(count)} ${count === 1 ? "entry" : "entries"}`;
const activityLevel = (count) => (count >= 8 ? 4 : count >= 4 ? 3 : count >= 2 ? 2 : count);

let overviewRevision = 0;
let activityDays = 365;
let activitySelected = "";

function overviewPriority(target, data) {
  const row = (item) => {
    const title = item.path
      ? `<a href="${esc(fileUrl(item.path))}" target="_blank" rel="noopener">${esc(item.title)}</a>`
      : esc(item.title);
    const context = [projName(item.project), item.owner, item.due ? "Due " + item.due : ""].filter(Boolean);
    return `<li class="rh-priority-row">
      <div class="ov-priority-copy"><strong>${title}</strong><small>${context.map(esc).join(" · ")}</small>
        ${item.detail ? `<p>${esc(item.detail)}</p>` : ""}</div>
      <span class="rh-mark${item.rank < 3 ? " rh-urgent" : ""}">${esc(item.status)}</span>
    </li>`;
  };
  const items = data.items;
  $("#rh-priority-count").textContent = data.total ? activityNumber.format(data.total) + " open" : "";
  target.innerHTML = items.length ? `<ul class="rh-priority-list">${items.slice(0, 6).map(row).join("")}</ul>
    ${items.length > 6 ? `<details class="ov-more-priorities"><summary><span class="ov-priorities-expand">Show ${items.length - 6} more priorities</span><span class="ov-priorities-collapse">Show fewer priorities</span></summary>
      <ul class="rh-priority-list">${items.slice(6).map(row).join("")}</ul></details>` : ""}`
    : muted("No priority items recorded.");
  if (data.total > items.length || data.unavailable.length) {
    const message = [
      data.total > items.length ? `${items.length} of ${data.total} priorities available here.` : "",
      data.unavailable.length ? "Unavailable sources: " + data.unavailable.join(", ") + "." : "",
    ].filter(Boolean).join(" ");
    target.insertAdjacentHTML("beforeend", muted(message));
  }
}

function overviewBrief(target, morning, project) {
  const {report, today, counts} = morning;
  const picks = reportItems(report, project).slice(0, 3);
  const badge = $("#rh-overview-brief-state");
  badge.textContent = report ? statusLabel(report.state) : "";
  badge.hidden = !report;
  target.innerHTML = `
    ${report ? `<p class="rh-brief-date">${esc(briefDate(report.day))}${report.day !== today ? " · Latest available" : ""}</p>` : ""}
    ${picks.length ? `<ul class="rh-brief-list">${picks.map((item) => `<li>
      <small>${esc(item.kind)} · ${esc(item.source)}</small>
      <strong>${externalLink(item.url, item.title)}</strong></li>`).join("")}</ul>`
      : muted(report ? "No selected papers for this view." : "No briefing available yet.")}
    ${counts.review || counts.pending ? `<p class="rh-brief-count">${counts.review} to review · ${counts.pending} filing pending</p>` : ""}`;
}

function activityMetrics(data) {
  const thirtyDaysAgo = asDay(data.end) - 29 * DAY;
  const active = data.days.filter((day) => asDay(day.day) >= thirtyDaysAgo && day.total).length;
  $("#ov-active-count").textContent = activityNumber.format(active);
  const stamp = $("#ov-refresh");
  if (data.refreshed) {
    const when = new Date(data.refreshed);
    const text = new Intl.DateTimeFormat(undefined, {
      timeZone: data.timezone, month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(when);
    stamp.innerHTML = `Last refresh <time datetime="${esc(data.refreshed)}">${esc(text)}</time>`;
  }
}

function activityCalendar(root, data, changeDay) {
  const start = asDay(data.start);
  const end = asDay(data.end);
  const offset = (new Date(start).getUTCDay() + 6) % 7;
  const numberOfDays = Math.round((end - start) / DAY) + 1;
  const weeks = Math.ceil((numberOfDays + offset) / 7);
  const days = new Map(data.days.map(({day, total}) => [day, total]));
  const focusDay = activitySelected || data.end;
  const months = [];
  const cells = [];
  for (let i = 0; i < numberOfDays; i += 1) {
    const date = new Date(start + i * DAY);
    const day = isoDay(date.getTime());
    const count = days.get(day) || 0;
    const col = Math.floor((i + offset) / 7) + 1;
    const row = (i + offset) % 7 + 2;
    if (date.getUTCDate() === 1 || (i === 0 && date.getUTCDate() <= 17)) {
      months.push(`<span class="ov-month" aria-hidden="true" style="grid-column:${col}">${esc(activityMonth.format(date))}</span>`);
    }
    const label = briefDate(day) + " · " + activityCount(count);
    cells.push(`<button type="button" class="ov-day" data-day="${day}" data-count="${count}" data-level="${activityLevel(count)}"
      style="grid-column:${col};grid-row:${row}" tabindex="${day === focusDay ? "0" : "-1"}" aria-label="${esc(label)}" title="${esc(label)}"
      aria-pressed="${day === activitySelected}" ${day === data.end ? 'aria-current="date"' : ""}></button>`);
  }
  const total = data.days.reduce((sum, day) => sum + day.total, 0);
  const active = data.days.filter((day) => day.total).length;
  $(".ov-calendar-summary", root).textContent = `${activityCount(total)} across ${activityNumber.format(active)} active ${active === 1 ? "day" : "days"}`;
  const target = $(".ov-calendar-content", root);
  target.innerHTML = `<div class="ov-calendar-frame">
    <div class="ov-weekdays" aria-hidden="true"><span>Mon</span><span>Wed</span><span>Fri</span></div>
    <div class="ov-calendar-scroll">
      <div class="ov-calendar" role="group" aria-label="Activity by day, from ${esc(briefDate(data.start))} to ${esc(briefDate(data.end))}" style="--weeks:${weeks}">
        ${months.join("")}${cells.join("")}
      </div>
    </div>
  </div>
  <div class="ov-calendar-footer">
    <span class="ov-calendar-readout">${total ? "Select a day to see its entries" : "No indexed activity in this range"}</span>
    <div class="ov-scale" aria-label="Entries per day">
      <span>Entries per day</span>${["0", "1", "2–3", "4–7", "8+"].map((label, level) =>
        `<span><i data-level="${level}" aria-hidden="true"></i>${label}</span>`).join("")}
    </div>
  </div>`;
  const grid = $(".ov-calendar", target);
  const scroller = $(".ov-calendar-scroll", target);
  const readout = $(".ov-calendar-readout", target);
  const current = (day) => $(`[data-day="${day}"]`, grid);
  const resetReadout = () => {
    const chosen = current(activitySelected);
    readout.textContent = chosen?.getAttribute("aria-label")
      || (total ? "Select a day to see its entries" : "No indexed activity in this range");
  };
  const pointAt = (button) => {
    const previous = $("[tabindex='0']", grid);
    if (previous && previous !== button) previous.tabIndex = -1;
    button.tabIndex = 0;
    readout.textContent = button.getAttribute("aria-label");
  };
  const showDay = (button) => {
    const pressed = $('[aria-pressed="true"]', grid);
    if (pressed && pressed !== button) pressed.setAttribute("aria-pressed", "false");
    button.setAttribute("aria-pressed", "true");
    pointAt(button);
    changeDay(button.dataset.day);
  };
  grid.addEventListener("click", (event) => {
    const button = event.target.closest(".ov-day");
    if (button) showDay(button);
  });
  grid.addEventListener("focusin", (event) => {
    if (event.target.matches(".ov-day")) pointAt(event.target);
  });
  grid.addEventListener("pointerover", (event) => {
    const button = event.target.closest(".ov-day");
    if (button) readout.textContent = button.getAttribute("aria-label");
  });
  grid.addEventListener("pointerleave", resetReadout);
  grid.addEventListener("focusout", (event) => {
    if (!grid.contains(event.relatedTarget)) resetReadout();
  });
  grid.addEventListener("keydown", (event) => {
    if (!event.target.matches(".ov-day")) return;
    if (event.key === "Enter") {
      event.preventDefault();
      showDay(event.target);
      return;
    }
    const movement = {ArrowUp: -1, ArrowDown: 1, ArrowLeft: -7, ArrowRight: 7};
    const date = asDay(event.target.dataset.day);
    if (!(event.key in movement) && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const next = event.key === "Home" ? start : event.key === "End" ? end : date + movement[event.key] * DAY;
    if (next >= start && next <= end) current(isoDay(next))?.focus();
  });
  if (activitySelected) {
    const chosen = current(activitySelected);
    if (chosen) scroller.scrollLeft = chosen.offsetLeft + chosen.offsetWidth - scroller.clientWidth;
    resetReadout();
  } else {
    scroller.scrollLeft = scroller.scrollWidth;
  }
  return (day) => {
    const button = current(day);
    button?.setAttribute("aria-pressed", "false");
    resetReadout();
    button?.focus();
  };
}

function overviewActivityDetail(container, detail, timezone, project) {
  const categories = Object.entries(activityCategories)
    .filter(([key]) => detail.counts[key])
    .map(([key, label]) => `<li><strong>${detail.counts[key]}</strong> ${esc(label)}</li>`)
    .join("");
  const time = new Intl.DateTimeFormat(undefined, {timeZone: timezone, hour: "2-digit", minute: "2-digit"});
  const list = detail.entries.map((item) => {
    const moment = item.timestamp ? new Date(item.timestamp) : null;
    const title = item.path
      ? `<a href="${esc(fileUrl(item.path))}" target="_blank" rel="noopener">${esc(item.title)}</a>`
      : esc(item.title);
    const meta = [
      project === "all" ? projName(item.project) : "",
      item.files != null ? `${item.files} ${item.files === 1 ? "file" : "files"}` : "",
      item.reference,
    ].filter(Boolean).join(" · ");
    return `<li class="ov-entry">
      <span class="ov-entry-kind">${esc(activityBadges[item.category])}</span>
      <div><strong>${title}</strong>${meta ? `<small>${esc(meta)}</small>` : ""}</div>
      ${moment && Number.isFinite(moment.getTime()) ? `<time datetime="${esc(item.timestamp)}">${esc(time.format(moment))}</time>` : ""}
    </li>`;
  }).join("");
  container.innerHTML = `<div class="ov-detail-summary"><strong>${activityCount(detail.total)}</strong>
    ${categories ? `<ul class="ov-breakdown">${categories}</ul>` : ""}</div>
    ${list ? `<ul class="ov-entry-list">${list}</ul>` : '<p class="ov-detail-empty">No indexed activity on this day.</p>'}`;
}

function overview() {
  const request = ++overviewRevision;
  const project = S.project;
  const query = "project=" + encodeURIComponent(project);
  const root = $("#tab-overview");
  const keepProjectFocus = root.contains(document.activeElement) && document.activeElement.matches("[data-overview-project]");
  const live = () => request === overviewRevision && S.tab === "overview" && S.project === project;
  const nav = [{key: "all", name: "All projects"}, ...S.meta.projects].map((p) =>
    `<button type="button" class="rh-project" data-overview-project="${esc(p.key)}" aria-pressed="${p.key === project}">
      <span class="rh-project-dot" style="background:var(${p.slot ? projSlot(p.key) : "--rh-accent"})"></span>
      <span>${esc(p.name)}</span>
    </button>`).join("");
  root.innerHTML = `
    <div class="rh-overview">
      <nav class="rh-projects" aria-label="Research projects">
        <h2>Projects</h2><div>${nav}</div>
      </nav>
      <div class="rh-work">
        <header class="ov-heading">
          <h1>${project === "all" ? "Research overview" : esc(projName(project))}</h1>
          <p id="ov-refresh">Last refresh unavailable</p>
        </header>
        <dl class="ov-metrics" aria-label="Workspace at a glance">
          <div><dt>Active days <small>Past 30 days</small></dt><dd id="ov-active-count">—</dd></div>
          <div><dt>Open priorities</dt><dd id="ov-priority-count">—</dd></div>
          <div><dt>Papers to review</dt><dd id="ov-review-count">—</dd></div>
        </dl>
        <section class="rh-panel ov-activity" aria-labelledby="ov-activity-title">
          <header class="rh-panel-head">
            <div><h2 id="ov-activity-title">Recorded work</h2><p class="ov-calendar-summary"></p></div>
            <div class="ov-range" role="group" aria-label="Calendar range">
              <button type="button" data-range-days="90" aria-pressed="${activityDays === 90}">90 days</button>
              <button type="button" data-range-days="365" aria-pressed="${activityDays === 365}">1 year</button>
            </div>
          </header>
          <div class="ov-calendar-content" aria-busy="true">${muted("Loading calendar…")}</div>
          <section class="ov-detail" aria-labelledby="ov-day-title" hidden>
            <div class="ov-detail-head"><h3 id="ov-day-title"></h3><button type="button" class="ghost ov-close-day">Close</button></div>
            <div class="ov-detail-body" aria-live="polite"></div>
          </section>
        </section>
        <div class="rh-dashboard-grid">
          <section class="rh-panel" aria-labelledby="rh-attention">
            <div class="rh-panel-head"><h2 id="rh-attention">Needs attention</h2><span id="rh-priority-count" class="rh-count"></span></div>
            <div id="ov-priorities" aria-busy="true">${muted("Loading priorities…")}</div>
          </section>
          <section class="rh-panel rh-brief-panel" aria-labelledby="rh-brief">
            <div class="rh-panel-head"><h2 id="rh-brief">Morning brief</h2><span id="rh-overview-brief-state" class="rh-mark" hidden></span></div>
            <div id="ov-brief" aria-busy="true">${muted("Loading briefing…")}</div>
            <a class="rh-cta" href="#literature">Open literature <span aria-hidden="true">↗</span></a>
          </section>
        </div>
        <details class="rh-recent rh-panel">
          <summary>Recent project activity</summary><div id="rh-project-snapshot"></div>
        </details>
      </div>
    </div>`;
  $$("[data-overview-project]", root).forEach((button) => {
    button.onclick = () => showProject(button.dataset.overviewProject, "overview");
  });
  if (keepProjectFocus) $('[data-overview-project][aria-pressed="true"]', root)?.focus();
  const recent = $(".rh-recent", root);
  recent.addEventListener("toggle", () => {
    if (!recent.open || recent.dataset.loaded) return;
    recent.dataset.loaded = "1";
    const snapshot = $("#rh-project-snapshot", recent);
    projectSnapshot(snapshot).catch(() => { snapshot.textContent = "Recent activity is unavailable."; });
  });

  const loadPanel = (selector, path, unavailable, render) => {
    const panel = $(selector, root);
    api(path).then((data) => { if (live()) render(panel, data); }).catch(() => {
      if (live()) panel.innerHTML = muted(unavailable);
    }).finally(() => { if (live()) panel.removeAttribute("aria-busy"); });
  };
  loadPanel("#ov-priorities", "/api/priorities?" + query, "Priority sources are unavailable.", (panel, data) => {
    $("#ov-priority-count").textContent = activityNumber.format(data.total);
    overviewPriority(panel, data);
  });
  loadPanel("#ov-brief", "/api/literature?" + query, "The briefing could not be loaded.", (panel, data) => {
    $("#ov-review-count").textContent = activityNumber.format(data.counts.review);
    overviewBrief(panel, data, project);
  });

  const activity = $(".ov-activity", root);
  const target = $(".ov-calendar-content", activity);
  const dayPanel = $(".ov-detail", activity);
  const dayBody = $(".ov-detail-body", dayPanel);
  let calendarRequest = 0;
  let detailRequest = 0;
  let focusCell = null;
  function closeDay() {
    const previous = activitySelected;
    activitySelected = "";
    detailRequest += 1;
    dayPanel.hidden = true;
    focusCell?.(previous);
  }
  async function pickDay(day, timezone) {
    activitySelected = day;
    const revision = ++detailRequest;
    dayPanel.hidden = false;
    $("#ov-day-title", dayPanel).textContent = briefDate(day);
    dayBody.innerHTML = '<p class="ov-detail-empty">Loading day…</p>';
    const current = () => live() && revision === detailRequest;
    try {
      const detail = await api(`/api/overview/activity/${day}?${query}`);
      if (current()) overviewActivityDetail(dayBody, detail, timezone, project);
    } catch {
      if (current()) dayBody.innerHTML = '<p class="ov-detail-empty">The day’s entries could not be loaded. Select the day to retry.</p>';
    }
  }
  $(".ov-close-day", dayPanel).onclick = closeDay;
  async function loadCalendar() {
    const revision = ++calendarRequest;
    detailRequest += 1;
    dayPanel.hidden = true;
    target.setAttribute("aria-busy", "true");
    target.innerHTML = muted("Loading calendar…");
    $(".ov-calendar-summary", activity).textContent = "";
    $$(".ov-range button", activity).forEach((button) => {
      button.setAttribute("aria-pressed", +button.dataset.rangeDays === activityDays);
    });
    const current = () => live() && revision === calendarRequest;
    try {
      const data = await api(`/api/overview/activity?${query}&days=${activityDays}`);
      if (!current()) return;
      activityMetrics(data);
      if (activitySelected < data.start || activitySelected > data.end) activitySelected = "";
      focusCell = activityCalendar(activity, data, (day) => pickDay(day, data.timezone));
      if (activitySelected) pickDay(activitySelected, data.timezone);
    } catch {
      if (current()) target.innerHTML = muted("Unable to load recorded work. Select a range to retry.");
    } finally {
      if (current()) target.removeAttribute("aria-busy");
    }
  }
  $$(".ov-range button", activity).forEach((button) => {
    button.onclick = () => { activityDays = +button.dataset.rangeDays; loadCalendar(); };
  });
  loadCalendar();
}

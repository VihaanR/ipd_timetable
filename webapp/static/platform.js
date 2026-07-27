// Platform page (P2 generate-from-DB): seed -> readiness -> generate (DB-backed run) -> poll ->
// render grids -> export -> history. Grid rendering (renderGrid/renderTabs/renderStages) is
// adapted from the legacy showcase's app.js; the `grids` JSON shape is identical, and the same
// CSS classes from style.css are reused so this page looks consistent with the showcase.
const $ = (id) => document.getElementById(id);

const TYPE_CLASS = { Theory: "theory", Practical: "lab", Tutorial: "tutorial", Break: "break" };

let currentGrids = null;
let activeDivision = 0;
let currentRunId = null;
let pollTimer = null;
let originalGrids = null;          // the un-adjusted run grids, so "Restore original" can revert
let movedIds = new Set();          // session ids relocated by the last adjustment (for highlighting)

// Compare-solvers panel state. Kept entirely separate from currentGrids/activeDivision/movedIds
// above: /api/compare is a one-shot synchronous call that never creates a run, so it must never
// disturb the generated run that the adjust panel operates on.
let compareResults = null;         // results[] from the last successful /api/compare response
let compareActiveSolver = 0;       // index into compareResults for the active solver tab
let compareActiveDivision = 0;     // division tab index within the active solver's grids

$("seedBtn").addEventListener("click", loadSeed);
$("generateBtn").addEventListener("click", generate);
$("compareBtn").addEventListener("click", runCompare);
$("adjustBtn").addEventListener("click", adjust);
$("restoreBtn").addEventListener("click", restoreOriginal);
$("adjScope").addEventListener("change", () => {
  $("adjFromWrap").style.display = $("adjScope").value === "from" ? "" : "none";
});

// ---------------------------------------------------------------- 1. starter data
async function loadSeed() {
  const btn = $("seedBtn");
  btn.disabled = true;
  $("seedStatus").textContent = "loading…";
  try {
    const res = await fetch("/api/seed/reference", { method: "POST" });
    if (res.status === 409) {
      $("seedStatus").textContent = "already loaded — continuing.";
    } else if (!res.ok) {
      const text = await res.text();
      $("seedStatus").textContent = `error (HTTP ${res.status}) — ${text.slice(0, 200)}`;
    } else {
      const data = await res.json();
      $("seedStatus").textContent =
        `loaded: ${data.divisions} divisions, ${data.faculty} faculty, ${data.courses} courses, ` +
        `${data.rooms} rooms, ${data.slots} slots (branch ${data.branch_code}).`;
    }
  } catch (e) {
    $("seedStatus").textContent = "backend not reachable — start the server on port 8750.";
  } finally {
    btn.disabled = false;
    checkReadiness();
  }
}

// ---------------------------------------------------------------- 2. readiness
async function checkReadiness() {
  const banner = $("readyBanner");
  banner.textContent = "checking…";
  banner.className = "readiness-banner";
  try {
    const res = await fetch("/api/readiness");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    renderReadiness(data);
  } catch (e) {
    banner.textContent = "Could not reach the backend API. Is the server running on port 8750?";
    banner.className = "readiness-banner not-ready";
  }
}

function renderReadiness(data) {
  const banner = $("readyBanner");
  if (data.ready) {
    banner.className = "readiness-banner ready";
    banner.innerHTML = "&#10003; Ready to generate";
  } else {
    banner.className = "readiness-banner not-ready";
    const items = (data.issues || []).map((i) => `<li>${i}</li>`).join("");
    banner.innerHTML = `<b>Not ready yet:</b><ul>${items}</ul>`;
  }
}

// ---------------------------------------------------------------- 3. generate
async function generate() {
  const btn = $("generateBtn");
  btn.disabled = true;
  $("genStatus").textContent = "submitting…";
  $("summary").style.display = "none";
  $("stagesWrap").style.display = "none";
  $("exportRow").style.display = "none";
  $("tabs").style.display = "none";
  $("legend").style.display = "none";
  $("gridArea").innerHTML = "";
  currentGrids = null;
  currentRunId = null;
  clearTimeout(pollTimer);

  const payload = {
    solver: $("solver").value,
    time_limit: parseFloat($("timeLimit").value) || 30,
    label: "",
  };

  try {
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 400) {
      const body = await res.json();
      const issues = Array.isArray(body.detail) ? body.detail : [String(body.detail)];
      renderReadiness({ ready: false, issues });
      $("genStatus").textContent = "not ready — see the readiness banner above.";
      btn.disabled = false;
      return;
    }
    if (res.status === 409) {
      $("genStatus").textContent = "a run is already in progress — try again shortly.";
      btn.disabled = false;
      return;
    }
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + " — " + text.slice(0, 300));
    }
    const data = await res.json();
    currentRunId = data.run_id;
    $("genStatus").textContent = `run #${currentRunId} queued…`;
    pollRun(currentRunId);
  } catch (e) {
    $("genStatus").textContent = "error: " + (e.message || e);
    btn.disabled = false;
  }
}

function pollRun(runId) {
  fetch(`/api/runs/${runId}`)
    .then((res) => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    })
    .then((run) => {
      if (run.status === "queued" || run.status === "running") {
        $("genStatus").textContent = `run #${runId} ${run.status}…`;
        pollTimer = setTimeout(() => pollRun(runId), 1500);
        return;
      }
      $("generateBtn").disabled = false;
      if (run.status === "done") {
        $("genStatus").textContent = `run #${runId} done.`;
        renderSummary(run);
        renderStages(run.stage_reports);
        currentGrids = run.grids;
        originalGrids = run.grids;
        movedIds = new Set();
        activeDivision = 0;
        renderTabs();
        renderGrid();
        $("legend").style.display = "flex";
        enableExport(runId);
        // reveal the disruption panel now that there's a baseline timetable to adjust
        $("adjustSection").style.display = "";
        $("adjustRunId").textContent = "#" + runId;
        $("restoreBtn").style.display = "none";
        $("adjStatus").textContent = "";
      } else {
        $("genStatus").textContent = `run #${runId} failed: ${run.error || "unknown error"}`;
      }
      loadHistory();
    })
    .catch((e) => {
      $("genStatus").textContent = "error polling run: " + (e.message || e);
      $("generateBtn").disabled = false;
    });
}

function enableExport(runId) {
  const row = $("exportRow");
  row.style.display = "flex";
  $("exportXlsx").href = `/api/runs/${runId}/export.xlsx`;
  $("exportPdf").href = `/api/runs/${runId}/export.pdf`;
}

// ---------------------------------------------------------------- 4. result rendering
function renderSummary(run) {
  $("summary").style.display = "flex";
  $("statSolver").textContent = run.solver;
  $("statStatus").textContent = run.status;
  const hard = $("statHard");
  hard.textContent = run.hard;
  hard.className = "stat-val " + (run.hard === 0 ? "good" : "bad");
  $("statSoft").textContent = typeof run.soft === "number" ? run.soft.toFixed(1) : run.soft;
  // wall_clock is the solver's total solve time (pipeline total, or the single solver's own).
  $("statWall").textContent = typeof run.wall_clock === "number" ? run.wall_clock.toFixed(1) + "s" : "—";
}

function renderStages(stages) {
  if (!stages || stages.length === 0) { $("stagesWrap").style.display = "none"; return; }
  $("stagesWrap").style.display = "block";
  const track = $("stageTrack");
  track.innerHTML = "";
  stages.forEach((s, i) => {
    const el = document.createElement("div");
    el.className = "stage" + (i === stages.length - 1 ? " best" : "");
    el.innerHTML = `
      <div class="stage-name">${s.name}</div>
      <div class="stage-row"><span>status</span><b>${s.status}</b></div>
      <div class="stage-row"><span>hard</span><b>${s.hard}</b></div>
      <div class="stage-row"><span>soft</span><b>${s.soft}</b></div>
      <div class="stage-row"><span>time</span><b>${s.wall_clock_s}s</b></div>
      <div class="stage-row"><span>running best</span><b>${s.best_hard}h / ${s.best_soft}</b></div>
      ${s.improved ? '<span class="improved-badge">&#9650; improved best</span>' : ''}
    `;
    track.appendChild(el);
    if (i < stages.length - 1) {
      const arrow = document.createElement("div");
      arrow.className = "stage-arrow";
      arrow.textContent = "→";
      track.appendChild(arrow);
    }
  });
}

// Generic division-tab renderer: shared by the generate/adjust flow (#tabs, currentGrids) and the
// compare panel (#compareTabs / #compareDivTabs, compareResults[i].grids) so both reuse the exact
// same tab markup/behaviour without either touching the other's state.
function renderDivisionTabs(container, grids, activeIdx, onSelect) {
  if (!grids || !grids.divisions || grids.divisions.length === 0) {
    container.style.display = "none";
    container.innerHTML = "";
    return;
  }
  container.style.display = "flex";
  container.innerHTML = "";
  grids.divisions.forEach((div, i) => {
    const t = document.createElement("div");
    t.className = "tab" + (i === activeIdx ? " active" : "");
    t.textContent = "Division " + div.id;
    t.onclick = () => onSelect(i);
    container.appendChild(t);
  });
}

function renderTabs() {
  renderDivisionTabs($("tabs"), currentGrids, activeDivision, (i) => {
    activeDivision = i;
    renderTabs();
    renderGrid();
  });
}

// Generic grid-table builder: pure function of (grids, activeIdx, movedSet) -> <table> or null.
// The generate/adjust flow calls it through renderGrid() below (movedSet = the module-level
// movedIds, so adjustment highlighting keeps working exactly as before). The compare panel calls
// it directly with an empty moved-set (compare results are never "adjusted").
function buildGridTable(grids, activeIdx, movedSet) {
  if (!grids || !grids.divisions || grids.divisions.length === 0) return null;
  const g = grids;
  const div = g.divisions[activeIdx];
  const table = document.createElement("table");
  table.className = "tt";

  const thead = document.createElement("thead");
  let hrow = "<tr><th class='time-col'>Time</th>";
  g.days.forEach((d) => (hrow += `<th>${d}</th>`));
  hrow += "</tr>";
  thead.innerHTML = hrow;
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  g.periods.forEach((p) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td class="time-col">${p.start}<br>${p.end}</td>`;
    g.days.forEach((_, dayIdx) => {
      const key = `${dayIdx}_${p.period}`;
      const entries = div.cells[key] || [];
      const td = document.createElement("td");
      if (entries.length === 0) {
        td.innerHTML = `<div class="cell empty"></div>`;
      } else {
        const cell = document.createElement("div");
        cell.className = "cell";
        entries.forEach((e) => {
          const s = document.createElement("div");
          s.className = "session " + (TYPE_CLASS[e.type] || "theory") +
            (movedSet.has(e.session_id) ? " moved" : "");
          if (e.is_break) {
            s.innerHTML = `<div class="s-course">BREAK</div>`;
          } else {
            const batch = e.batch ? ` · ${e.batch}` : "";
            s.innerHTML =
              `<div class="s-course">${e.course}${e.type === "Practical" ? " (Lab)" : ""}</div>` +
              `<div class="s-meta">${e.faculty ? e.faculty : ""}${e.room ? " · @" + e.room : ""}${batch}</div>`;
            s.title = `${e.course} — ${e.type}\n${e.faculty_name || ""}\n${e.room_name || ""}`;
          }
          cell.appendChild(s);
        });
        td.appendChild(cell);
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  return table;
}

function renderGrid() {
  const table = buildGridTable(currentGrids, activeDivision, movedIds);
  $("gridArea").innerHTML = "";
  if (table) $("gridArea").appendChild(table);
}

// ---------------------------------------------------------------- 5. compare solvers
// POST /api/compare is a single synchronous request that runs N solvers back-to-back (no
// background job, no run row, no history entry, no export links) — very different from the
// generate flow's queue-and-poll pattern. It never touches currentGrids/currentRunId/movedIds;
// its own state (compareResults/compareActiveSolver/compareActiveDivision) is entirely separate
// so the adjust panel keeps operating on the real generated run regardless of what's compared.
function selectedCompareSolvers() {
  const solvers = [];
  if ($("cmpCpsat").checked) solvers.push("cpsat");
  if ($("cmpPipeline").checked) solvers.push("pipeline");
  if ($("cmpGreedy").checked) solvers.push("greedy");
  return solvers;
}

async function runCompare() {
  const solvers = selectedCompareSolvers();
  if (solvers.length < 2) {
    $("compareStatus").textContent =
      "pick at least 2 solvers to compare — a single solver is just Generate above.";
    return;
  }

  const btn = $("compareBtn");
  btn.disabled = true;
  $("compareResultWrap").style.display = "none";
  $("compareStatus").textContent =
    `running ${solvers.length} solver(s) (${solvers.join(", ")}) back-to-back — this can take a ` +
    `while (cpsat/pipeline may each take 20–60s)…`;

  const payload = {
    time_limit: parseFloat($("compareTimeLimit").value) || 20,
    label: "",
    branch_ids: null,
    solvers,
  };

  try {
    const res = await fetch("/api/compare", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.status === 400) {
      const body = await res.json();
      const detail = body.detail;
      if (Array.isArray(detail)) {
        const items = detail.map((d) => `<li>${d}</li>`).join("");
        $("compareStatus").innerHTML = `<b>Not ready to generate:</b><ul>${items}</ul>`;
      } else {
        $("compareStatus").textContent = "error: " + String(detail);
      }
      return;
    }
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + " — " + text.slice(0, 300));
    }
    const data = await res.json();
    compareResults = data.results;
    compareActiveSolver = typeof data.best_index === "number" ? data.best_index : 0;
    compareActiveDivision = 0;

    $("compareStatus").textContent =
      `done — compared ${data.solvers.length} solver(s); winner: ${data.best_solver}.`;
    renderCompareTable(data);
    $("compareResultWrap").style.display = "block";
    renderCompareSolverTabs();
    renderCompareDivisionTabs();
    renderCompareGrid();
  } catch (e) {
    $("compareStatus").textContent = "error: " + (e.message || e);
  } finally {
    btn.disabled = false;
  }
}

function renderCompareTable(data) {
  const body = $("compareTableBody");
  body.innerHTML = "";
  data.results.forEach((r, i) => {
    const tr = document.createElement("tr");
    const isBest = i === data.best_index;
    if (isBest) tr.className = "compare-winner";
    const hardClass = r.hard_violations === 0 ? "good" : "bad";
    const soft = typeof r.soft_cost === "number" ? r.soft_cost.toFixed(1) : r.soft_cost;
    const wall = typeof r.wall_clock_s === "number" ? r.wall_clock_s.toFixed(1) : r.wall_clock_s;
    tr.innerHTML =
      `<td>${r.solver}${isBest ? ' <span class="compare-badge">best</span>' : ""}</td>` +
      `<td>${r.status}</td>` +
      `<td><span class="stat-val ${hardClass}" style="font-size:14px">${r.hard_violations}</span></td>` +
      `<td>${soft}</td>` +
      `<td>${wall}</td>`;
    body.appendChild(tr);
  });
}

function renderCompareSolverTabs() {
  const tabs = $("compareTabs");
  if (!compareResults || compareResults.length === 0) {
    tabs.style.display = "none";
    tabs.innerHTML = "";
    return;
  }
  tabs.style.display = "flex";
  tabs.innerHTML = "";
  compareResults.forEach((r, i) => {
    const t = document.createElement("div");
    t.className = "tab" + (i === compareActiveSolver ? " active" : "");
    t.textContent = r.solver;
    t.onclick = () => {
      compareActiveSolver = i;
      compareActiveDivision = 0;
      renderCompareSolverTabs();
      renderCompareDivisionTabs();
      renderCompareGrid();
    };
    tabs.appendChild(t);
  });
}

function renderCompareDivisionTabs() {
  const active = compareResults ? compareResults[compareActiveSolver] : null;
  renderDivisionTabs($("compareDivTabs"), active ? active.grids : null, compareActiveDivision, (i) => {
    compareActiveDivision = i;
    renderCompareDivisionTabs();
    renderCompareGrid();
  });
}

function renderCompareGrid() {
  const active = compareResults ? compareResults[compareActiveSolver] : null;
  const table = buildGridTable(active ? active.grids : null, compareActiveDivision, new Set());
  $("compareGridArea").innerHTML = "";
  if (table) $("compareGridArea").appendChild(table);
}

// ---------------------------------------------------------------- 6. holiday / rain adjustment
async function adjust() {
  if (!currentRunId) return;
  const btn = $("adjustBtn");
  btn.disabled = true;
  $("adjStatus").textContent = "adjusting…";
  const scope = $("adjScope").value;
  const payload = {
    day: parseInt($("adjDay").value, 10),
    from_period: scope === "from" ? (parseInt($("adjFrom").value, 10) || 0) : null,
    reason: scope === "from" ? "rain" : "holiday",
  };
  try {
    const res = await fetch(`/api/runs/${currentRunId}/adjust`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + " — " + text.slice(0, 200));
    }
    const d = await res.json();
    movedIds = new Set(d.moved.filter((m) => !m.dropped).map((m) => m.session_id));
    currentGrids = d.grids;
    renderTabs();
    renderGrid();
    const dropped = d.dropped_count
      ? `, ${d.dropped_count} dropped (rained out)`
      : "";
    $("adjStatus").innerHTML =
      `Adjusted: <b>${d.disrupted_day}</b>, ${d.scope} — ` +
      `${d.moved_count} session(s) moved${dropped}. ` +
      `Moved sessions are highlighted below.`;
    $("restoreBtn").style.display = "";
  } catch (e) {
    $("adjStatus").textContent = "error: " + (e.message || e);
  } finally {
    btn.disabled = false;
  }
}

function restoreOriginal() {
  if (!originalGrids) return;
  currentGrids = originalGrids;
  movedIds = new Set();
  renderTabs();
  renderGrid();
  $("adjStatus").textContent = "restored the original (un-adjusted) timetable.";
  $("restoreBtn").style.display = "none";
}

// ---------------------------------------------------------------- 7. history
async function loadHistory() {
  try {
    const res = await fetch("/api/runs");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const runs = await res.json();
    const body = $("historyBody");
    body.innerHTML = "";
    runs.forEach((r) => {
      const tr = document.createElement("tr");
      const created = (() => {
        const d = new Date(r.created_at);
        return isNaN(d.getTime()) ? r.created_at : d.toLocaleString();
      })();
      tr.innerHTML = `<td>${r.id}</td><td>${r.label || ""}</td><td>${r.solver}</td>` +
        `<td>${r.status}</td><td>${r.hard ?? ""}</td><td>${r.soft ?? ""}</td><td>${created}</td>`;
      body.appendChild(tr);
    });
  } catch (e) {
    // history is a nice-to-have; stay quiet on failure (readiness/generate already surface
    // backend-unreachable errors prominently).
  }
}

// ---------------------------------------------------------------- init
checkReadiness();
loadHistory();

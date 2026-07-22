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
let compareResults = [];
let compareActiveIndex = 0;

$("seedBtn").addEventListener("click", loadSeed);
$("generateBtn").addEventListener("click", generate);
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
    $("seedStatus").textContent = "backend not reachable — start the server on port 8750 (or set TIMETABLE_PORT).";
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
    banner.textContent = "Could not reach the backend API. Is the server running on port 8750 (or set TIMETABLE_PORT)?";
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
  $("compareSection").style.display = "none";
  $("compareTableWrap").innerHTML = "";
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
    const endpoint = $("solver").value === "compare" ? "/api/compare" : "/api/runs";
    const comparePayload = { ...payload, solvers: ["pipeline", "cpsat", "greedy"] };
    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify($("solver").value === "compare" ? comparePayload : payload),
    });
    if ($("solver").value === "compare") {
      if (!res.ok) {
        const text = await res.text();
        throw new Error("HTTP " + res.status + " — " + text.slice(0, 300));
      }
      const data = await res.json();
      $("adjustSection").style.display = "none";
      $("restoreBtn").style.display = "none";
      $("adjStatus").textContent = "";
      renderCompare(data);
      $("genStatus").textContent = `compare mode done — best: ${data.best_solver}.`;
      btn.disabled = false;
      return;
    }
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

function renderCompare(data) {
  compareResults = data.results || [];
  compareActiveIndex = data.best_index ?? 0;
  const best = compareResults[compareActiveIndex];
  if (!best) {
    $("compareSection").style.display = "none";
    return;
  }

  $("compareSection").style.display = "block";
  $("compareNote").textContent =
    `Compared ${compareResults.length} solvers on the same snapshot. Best result: ${data.best_solver}.`;

  const rows = compareResults.map((result, index) => {
    const active = index === compareActiveIndex ? " best" : "";
    const status = result.status === "done" ? "good" : "bad";
    return `<tr class="${active}">
      <td>${result.solver}${index === data.best_index ? ' <span class="compare-pill">best</span>' : ''}</td>
      <td><span class="stat-val ${status}">${result.status}</span></td>
      <td>${result.hard_violations}</td>
      <td>${typeof result.soft_cost === "number" ? result.soft_cost.toFixed(1) : result.soft_cost}</td>
      <td>${typeof result.wall_clock_s === "number" ? result.wall_clock_s.toFixed(1) : result.wall_clock_s}s</td>
      <td><button type="button" data-compare-view="${index}">View</button></td>
    </tr>`;
  }).join("");

  $("compareTableWrap").innerHTML = `
    <table class="tt compare-table">
      <thead>
        <tr><th>Solver</th><th>Status</th><th>Hard</th><th>Soft</th><th>Wall</th><th></th></tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>`;

  $("compareTableWrap").querySelectorAll("button[data-compare-view]").forEach((button) => {
    button.addEventListener("click", () => viewCompareResult(parseInt(button.dataset.compareView, 10)));
  });

  viewCompareResult(compareActiveIndex, true);
}

function viewCompareResult(index, skipTable = false) {
  const result = compareResults[index];
  if (!result) return;
  compareActiveIndex = index;
  currentGrids = result.grids;
  currentRunId = null;
  activeDivision = 0;
  renderSummary(result);
  renderStages(result.stage_reports);
  renderTabs();
  renderGrid();
  $("legend").style.display = "flex";
  if (!skipTable) {
    renderCompare({ results: compareResults, best_index: compareActiveIndex, best_solver: compareResults[compareActiveIndex]?.solver });
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
  const wall = typeof run.wall_clock === "number" ? run.wall_clock : run.wall_clock_s;
  $("statWall").textContent = typeof wall === "number" ? wall.toFixed(1) + "s" : "—";
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

function renderTabs() {
  const tabs = $("tabs");
  if (!currentGrids || !currentGrids.divisions || currentGrids.divisions.length === 0) {
    tabs.style.display = "none";
    return;
  }
  tabs.style.display = "flex";
  tabs.innerHTML = "";
  currentGrids.divisions.forEach((div, i) => {
    const t = document.createElement("div");
    t.className = "tab" + (i === activeDivision ? " active" : "");
    t.textContent = "Division " + div.id;
    t.onclick = () => { activeDivision = i; renderTabs(); renderGrid(); };
    tabs.appendChild(t);
  });
}

function renderGrid() {
  const g = currentGrids;
  if (!g || !g.divisions || g.divisions.length === 0) {
    $("gridArea").innerHTML = "";
    return;
  }
  const div = g.divisions[activeDivision];
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
            (movedIds.has(e.session_id) ? " moved" : "");
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

  $("gridArea").innerHTML = "";
  $("gridArea").appendChild(table);
}

// ---------------------------------------------------------------- 5. holiday / rain adjustment
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

// ---------------------------------------------------------------- 6. history
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

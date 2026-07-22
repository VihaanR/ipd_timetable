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

$("seedBtn").addEventListener("click", loadSeed);
$("generateBtn").addEventListener("click", generate);

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
        activeDivision = 0;
        renderTabs();
        renderGrid();
        $("legend").style.display = "flex";
        enableExport(runId);
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
          s.className = "session " + (TYPE_CLASS[e.type] || "theory");
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

// ---------------------------------------------------------------- 5. history
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

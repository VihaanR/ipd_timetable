const $ = (id) => document.getElementById(id);
let currentGrids = null;
let activeDivision = 0;
let movedSessionIds = new Set();     // sessions relocated by the last adjustment
let blockedCells = null;             // {day, fromPeriod|null} of the last adjustment's blocked window

const TYPE_CLASS = { Theory: "theory", Practical: "lab", Tutorial: "tutorial", Break: "break" };

$("dataset").addEventListener("change", () => {
  $("scaleWrap").style.display = $("dataset").value === "synthetic" ? "flex" : "none";
  loadProblem();
});
$("scale").addEventListener("change", loadProblem);
$("generateBtn").addEventListener("click", generate);
$("adjustBtn").addEventListener("click", adjust);

function showBackendError(where) {
  const onFile = location.protocol === "file:";
  const msg = onFile
    ? "This page is open as a local file, so it can't reach the backend API. " +
      "Start the server and open it through the server URL instead:"
    : "Could not reach the backend API. Make sure the server is running:";
  $("gridArea").innerHTML =
    `<div class="notes" style="font-size:14px;line-height:1.7">
       <b>⚠ Backend not reachable</b> (${where}).<br>${msg}
       <pre style="background:var(--panel-2);padding:12px;border-radius:8px;margin-top:8px;white-space:pre-wrap">python -m uvicorn webapp.server:app --port 8750</pre>
       the server defaults to port 8750, but it will fall back to the next free port and print the URL if 8750 is busy.
     </div>`;
}

async function loadProblem() {
  const dataset = $("dataset").value;
  const scale = $("scale").value;
  try {
    const res = await fetch(`/api/problem?dataset=${dataset}&scale=${scale}`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    $("datasetLabel").textContent =
      `${data.label} · ${data.divisions.length} divisions · ${data.courses.length} courses · ${data.faculty.length} faculty`;
  } catch (e) {
    $("datasetLabel").textContent = "backend not reachable";
    showBackendError("loading dataset");
  }
}

let __timerId = null;

async function generate() {
  const btn = $("generateBtn");
  btn.disabled = true;
  $("loader").style.display = "block";
  const base = $("solver").value === "pipeline"
    ? "Running hybrid pipeline (Greedy → MIP → GA → CP-SAT)"
    : "Running solver";
  const started = Date.now();
  $("loaderText").textContent = base + "… 0s";
  clearInterval(__timerId);
  __timerId = setInterval(() => {
    const secs = Math.round((Date.now() - started) / 1000);
    $("loaderText").textContent = `${base}… ${secs}s (this can take up to ~90s for the full pipeline)`;
  }, 1000);
  $("summary").style.display = "none";
  $("stagesWrap").style.display = "none";
  $("tabs").style.display = "none";
  $("legend").style.display = "none";
  $("gridArea").innerHTML = "";

  const payload = {
    dataset: $("dataset").value,
    scale: $("scale").value,
    solver: $("solver").value,
    time_limit: parseFloat($("timeLimit").value) || 30,
  };

  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error("HTTP " + res.status + " — " + text.slice(0, 300));
    }
    const data = await res.json();
    renderSummary(data);
    renderStages(data.stages);
    currentGrids = data.grids;
    activeDivision = 0;
    movedSessionIds = new Set();      // a fresh generation clears any prior adjustment overlay
    blockedCells = null;
    renderTabs();
    renderGrid();
    $("legend").style.display = "flex";
    $("adjustPanel").style.display = "block";   // baseline exists -> disruption re-plan available
    $("adjustResult").style.display = "none";
  } catch (e) {
    if (e instanceof TypeError) {
      showBackendError("generating timetable");   // network/CORS/file:// failure
    } else {
      $("gridArea").innerHTML = `<p class="notes">Error: ${e.message || e}</p>`;
    }
  } finally {
    clearInterval(__timerId);
    $("loader").style.display = "none";
    btn.disabled = false;
  }
}

function renderSummary(d) {
  $("summary").style.display = "flex";
  $("statStatus").textContent = d.status;
  const hard = $("statHard");
  hard.textContent = d.hard_violations;
  hard.className = "stat-val " + (d.hard_violations === 0 ? "good" : "bad");
  $("statSoft").textContent = d.soft_cost;
  $("statWall").textContent = d.wall_clock_s + "s";
  $("statSolver").textContent = d.final_solver;
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
      ${s.improved ? '<span class="improved-badge">▲ improved best</span>' : ''}
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
      const isBlocked = blockedCells && dayIdx === blockedCells.day &&
        (blockedCells.fromPeriod === null || p.period >= blockedCells.fromPeriod);
      if (entries.length === 0) {
        td.innerHTML = `<div class="cell empty${isBlocked ? " blocked" : ""}"></div>`;
      } else {
        const cell = document.createElement("div");
        cell.className = "cell" + (isBlocked ? " blocked" : "");
        entries.forEach((e) => {
          const s = document.createElement("div");
          const moved = e.session_id && movedSessionIds.has(e.session_id);
          s.className = "session " + (TYPE_CLASS[e.type] || "theory") + (moved ? " moved" : "");
          if (e.is_break) {
            s.innerHTML = `<div class="s-course">BREAK</div>`;
          } else {
            const batch = e.batch ? ` · ${e.batch}` : "";
            s.innerHTML =
              `<div class="s-course">${moved ? "↳ " : ""}${e.course}${e.type === "Practical" ? " (Lab)" : ""}</div>` +
              `<div class="s-meta">${e.faculty ? e.faculty : ""}${e.room ? " · @" + e.room : ""}${batch}</div>`;
            s.title = `${e.course} — ${e.type}\n${e.faculty_name || ""}\n${e.room_name || ""}` +
              (moved ? "\n(relocated by the disruption re-plan)" : "");
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

async function adjust() {
  const btn = $("adjustBtn");
  btn.disabled = true;
  const scopeVal = $("adjScope").value;
  const day = parseInt($("adjDay").value, 10);
  const fromPeriod = scopeVal === "" ? null : parseInt(scopeVal, 10);
  $("adjustResult").style.display = "block";
  $("adjustResult").innerHTML = "Re-planning…";
  try {
    const res = await fetch("/api/adjust", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ day, from_period: fromPeriod }),
    });
    if (!res.ok) {
      const t = await res.text();
      $("adjustResult").innerHTML = `<span class="pill bad">error</span> ${res.status} — ${t.slice(0,200)}`;
      return;
    }
    const d = await res.json();
    // overlay the adjusted grids + highlight moved/blocked
    currentGrids = d.grids;
    movedSessionIds = new Set(d.moved.filter((m) => !m.dropped).map((m) => m.session_id));
    blockedCells = { day, fromPeriod };
    renderTabs();
    renderGrid();
    const validPill = d.is_valid
      ? '<span class="pill ok">✓ conflict-free</span>'
      : `<span class="pill bad">${d.conflict_violations} conflicts</span>`;
    const droppedPill = d.dropped_count
      ? `<span class="pill warn">${d.dropped_count} dropped (rained out)</span>` : "";
    $("adjustResult").innerHTML =
      `${validPill}<span class="pill">${d.disrupted_day}, ${d.scope}</span>${droppedPill}<br>` +
      `<b>${d.moved_count}</b> session(s) relocated within the day (outlined below), ` +
      `<b>${d.dropped_count}</b> dropped. Other days unchanged. ` +
      (d.notes && d.notes.length ? `<br><span style="color:var(--muted)">${d.notes.join(" ")}</span>` : "");
  } catch (e) {
    $("adjustResult").innerHTML = `<span class="pill bad">error</span> ${e.message || e}`;
  } finally {
    btn.disabled = false;
  }
}

loadProblem();

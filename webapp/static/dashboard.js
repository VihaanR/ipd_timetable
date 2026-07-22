const $ = (id) => document.getElementById(id);

const state = {
  branches: [],
  faculties: [],
  courses: [],
  rooms: [],
  divisionsByBranch: new Map(),
  slots: [],
  activeBranchId: null,
  activeDivisionId: null,
  slotDraft: [],
};

function showStatus(id, message, kind = "") {
  const el = $(id);
  el.textContent = message;
  el.className = kind ? `status-line ${kind}` : "status-line";
}

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: options.body ? { "Content-Type": "application/json", ...(options.headers || {}) } : (options.headers || {}),
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status} — ${text.slice(0, 250)}`);
  }
  if (res.status === 204) return null;
  const contentType = res.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return res.json();
  return res.text();
}

function setSection(sectionId) {
  document.querySelectorAll(".dash-section").forEach((section) => {
    section.style.display = section.id === sectionId ? "block" : "none";
  });
  document.querySelectorAll(".dash-tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.section === sectionId);
  });
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderSelectOptions(select, items, getValue, getLabel, placeholder = null) {
  const current = select.value;
  select.innerHTML = "";
  if (placeholder) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = placeholder;
    select.appendChild(opt);
  }
  items.forEach((item) => {
    const opt = document.createElement("option");
    opt.value = String(getValue(item));
    opt.textContent = getLabel(item);
    select.appendChild(opt);
  });
  if ([...select.options].some((opt) => opt.value === current)) {
    select.value = current;
  } else if (select.options.length) {
    select.selectedIndex = placeholder ? 0 : 0;
  }
}

function parseIdList(text) {
  return text
    .split(",")
    .map((part) => parseInt(part.trim(), 10))
    .filter((value) => Number.isInteger(value));
}

function currentBranchId() {
  return parseInt($("divBranchSelect").value || $("courseBranchSelect").value || $("allocBranchSelect").value || "", 10) || null;
}

function activeDivisions() {
  return state.divisionsByBranch.get(currentBranchId()) || [];
}

async function loadAll() {
  await Promise.all([
    loadBranches(),
    loadFaculty(),
    loadRooms(),
    loadSlots(),
  ]);
  await syncBranchScopedData();
  bindBranchSelectors();
}

async function loadBranches() {
  state.branches = await api("/api/branches");
  if (!state.activeBranchId && state.branches.length) {
    state.activeBranchId = state.branches[0].id;
  }
  renderBranches();
  refreshBranchSelectors();
}

async function loadFaculty() {
  state.faculties = await api("/api/faculty");
  renderFaculty();
  refreshFacultySelectors();
}

async function loadRooms() {
  state.rooms = await api("/api/rooms");
  renderRooms();
}

async function loadSlots() {
  state.slots = await api("/api/slots");
  state.slotDraft = state.slots.map((slot) => ({ ...slot }));
  renderSlots();
}

async function loadDivisions(branchId) {
  if (!branchId) {
    state.divisionsByBranch.set(branchId, []);
    return [];
  }
  const divisions = await api(`/api/branches/${branchId}/divisions`);
  state.divisionsByBranch.set(branchId, divisions);
  return divisions;
}

async function loadCourses(branchId) {
  const query = branchId ? `?branch_id=${branchId}` : "";
  state.courses = await api(`/api/courses${query}`);
  renderCourses();
  refreshCourseSelectors();
}

async function loadAllocations(branchId, divisionId) {
  const query = divisionId ? `?division_id=${divisionId}` : "";
  state.allocations = await api(`/api/allocations${query}`);
  renderAllocations();
}

async function syncBranchScopedData() {
  const branchId = currentBranchId() || state.activeBranchId;
  if (!branchId) return;
  state.activeBranchId = branchId;
  await Promise.all([
    loadDivisions(branchId),
    loadCourses(branchId),
  ]);
  const divisions = state.divisionsByBranch.get(branchId) || [];
  state.activeDivisionId = divisions.length ? divisions[0].id : null;
  refreshBranchSelectors();
  refreshDivisionSelector();
  await loadAllocations(branchId, state.activeDivisionId);
  renderDivisions();
}

function bindBranchSelectors() {
  ["divBranchSelect", "courseBranchSelect", "allocBranchSelect"].forEach((id) => {
    $(id).addEventListener("change", async () => {
      state.activeBranchId = parseInt($(id).value, 10) || null;
      await syncBranchScopedData();
    });
  });
  $("allocDivisionSelect").addEventListener("change", async () => {
    state.activeDivisionId = parseInt($("allocDivisionSelect").value, 10) || null;
    await loadAllocations(state.activeBranchId, state.activeDivisionId);
  });
}

function refreshBranchSelectors() {
  const branchLabel = (b) => `${b.code} — ${b.name}`;
  renderSelectOptions($("divBranchSelect"), state.branches, (b) => b.id, branchLabel, null);
  renderSelectOptions($("courseBranchSelect"), state.branches, (b) => b.id, branchLabel, null);
  renderSelectOptions($("allocBranchSelect"), state.branches, (b) => b.id, branchLabel, null);
  if (state.activeBranchId) {
    ["divBranchSelect", "courseBranchSelect", "allocBranchSelect"].forEach((id) => {
      $(id).value = String(state.activeBranchId);
    });
  }
  const branchesEmpty = state.branches.length === 0;
  ["divBranchSelect", "courseBranchSelect", "allocBranchSelect"].forEach((id) => {
    $(id).disabled = branchesEmpty;
  });
}

function refreshDivisionSelector() {
  const divisions = state.divisionsByBranch.get(state.activeBranchId) || [];
  renderSelectOptions($("allocDivisionSelect"), divisions, (d) => d.id, (d) => `${d.name} · ${d.program}` , null);
  if (state.activeDivisionId) {
    $("allocDivisionSelect").value = String(state.activeDivisionId);
  }
  $("allocDivisionSelect").disabled = divisions.length === 0;
}

function refreshFacultySelectors() {
  const label = (f) => `${f.code} — ${f.name}`;
  ["allocFaculty", "allocBatch1Faculty", "allocBatch2Faculty"].forEach((id) => {
    renderSelectOptions($(id), state.faculties, (f) => f.id, label, null);
    $(id).disabled = state.faculties.length === 0;
  });
}

function refreshCourseSelectors() {
  renderSelectOptions($("allocCourseSelect"), state.courses, (c) => c.id, (c) => `${c.code} — ${c.title}`, null);
  $("allocCourseSelect").disabled = state.courses.length === 0;
}

function renderBranches() {
  const rows = state.branches.length
    ? state.branches.map((branch) => `
        <tr>
          <td>${esc(branch.code)}</td>
          <td>${esc(branch.name)}</td>
          <td>${esc(branch.semester_label || "")}</td>
          <td><button data-delete-branch="${branch.id}">Delete</button></td>
        </tr>`).join("")
    : `<tr><td colspan="4" class="status-line">No branches yet</td></tr>`;
  $("branchTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Code</th><th>Name</th><th>Semester</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("branchTableWrap").querySelectorAll("button[data-delete-branch]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this branch? This also removes its divisions, courses, and allocations.")) return;
      await api(`/api/branches/${btn.dataset.deleteBranch}`, { method: "DELETE" });
      showStatus("branchStatus", "branch deleted", "ok");
      await loadAll();
    });
  });
}

function renderDivisions() {
  const divisions = state.divisionsByBranch.get(state.activeBranchId) || [];
  const rows = divisions.length
    ? divisions.map((division) => `
        <tr>
          <td>${esc(division.name)}</td>
          <td>${esc(division.program)}</td>
          <td>${esc(division.semester)}</td>
          <td>${esc(division.student_count)}</td>
          <td>${esc(division.batch1_name || "")}</td>
          <td>${esc(division.batch2_name || "")}</td>
          <td><button data-delete-division="${division.id}">Delete</button></td>
        </tr>`).join("")
    : `<tr><td colspan="7" class="status-line">No divisions for this branch</td></tr>`;
  $("divTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Name</th><th>Program</th><th>Sem</th><th>Students</th><th>Batch 1</th><th>Batch 2</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("divTableWrap").querySelectorAll("button[data-delete-division]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this division? This also removes its allocations.")) return;
      await api(`/api/divisions/${btn.dataset.deleteDivision}`, { method: "DELETE" });
      showStatus("divStatus", "division deleted", "ok");
      await syncBranchScopedData();
    });
  });
}

function renderFaculty() {
  const rows = state.faculties.length
    ? state.faculties.map((faculty) => `
        <tr>
          <td>${esc(faculty.code)}</td>
          <td>${esc(faculty.name)}</td>
          <td>${esc(faculty.max_load_hours_per_week)}</td>
          <td>${esc(faculty.max_consecutive_sessions)}</td>
          <td>${esc((faculty.unavailable_slot_ids || []).join(", "))}</td>
          <td><button data-delete-faculty="${faculty.id}">Delete</button></td>
        </tr>`).join("")
    : `<tr><td colspan="6" class="status-line">No faculty yet</td></tr>`;
  $("facTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Code</th><th>Name</th><th>Max hrs</th><th>Max consec</th><th>Unavailable slots</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("facTableWrap").querySelectorAll("button[data-delete-faculty]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this faculty member? You must reassign allocations first.")) return;
      await api(`/api/faculty/${btn.dataset.deleteFaculty}`, { method: "DELETE" });
      showStatus("facStatus", "faculty deleted", "ok");
      await loadFaculty();
      await syncBranchScopedData();
    });
  });
}

function renderCourses() {
  const rows = state.courses.length
    ? state.courses.map((course) => `
        <tr>
          <td>${esc(course.code)}</td>
          <td>${esc(course.title)}</td>
          <td>${esc(course.category)}</td>
          <td>${esc(course.theory_per_week)}</td>
          <td>${esc(course.practical_per_week)}</td>
          <td>${esc(course.tutorial_per_week)}</td>
          <td>${course.is_heavy ? "Yes" : "No"}</td>
          <td><button data-delete-course="${course.id}">Delete</button></td>
        </tr>`).join("")
    : `<tr><td colspan="8" class="status-line">No courses for this branch</td></tr>`;
  $("courseTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Code</th><th>Title</th><th>Category</th><th>Theory</th><th>Practical</th><th>Tutorial</th><th>Heavy</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("courseTableWrap").querySelectorAll("button[data-delete-course]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this subject? This also removes its allocations.")) return;
      await api(`/api/courses/${btn.dataset.deleteCourse}`, { method: "DELETE" });
      showStatus("courseStatus", "subject deleted", "ok");
      await syncBranchScopedData();
    });
  });
}

function renderAllocations() {
  const rows = (state.allocations || []).length
    ? state.allocations.map((alloc) => {
        const division = (state.divisionsByBranch.get(state.activeBranchId) || []).find((d) => d.id === alloc.division_id);
        const course = state.courses.find((c) => c.id === alloc.course_id);
        const faculty = state.faculties.find((f) => f.id === alloc.faculty_id);
        const b1 = state.faculties.find((f) => f.id === alloc.batch1_faculty_id);
        const b2 = state.faculties.find((f) => f.id === alloc.batch2_faculty_id);
        const facultyText = alloc.faculty_id ? (faculty ? faculty.name : alloc.faculty_id) : `${b1 ? b1.name : alloc.batch1_faculty_id || ""} / ${b2 ? b2.name : alloc.batch2_faculty_id || ""}`;
        return `
          <tr>
            <td>${esc(division ? division.name : alloc.division_id)}</td>
            <td>${esc(course ? course.code : alloc.course_id)}</td>
            <td>${esc(facultyText)}</td>
            <td><button data-delete-allocation="${alloc.id}">Delete</button></td>
          </tr>`;
      }).join("")
    : `<tr><td colspan="4" class="status-line">No allocations for this division</td></tr>`;
  $("allocTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Division</th><th>Subject</th><th>Faculty</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("allocTableWrap").querySelectorAll("button[data-delete-allocation]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this allocation?")) return;
      await api(`/api/allocations/${btn.dataset.deleteAllocation}`, { method: "DELETE" });
      showStatus("allocStatus", "allocation deleted", "ok");
      await loadAllocations(state.activeBranchId, state.activeDivisionId);
    });
  });
}

function renderRooms() {
  const rows = state.rooms.length
    ? state.rooms.map((room) => `
        <tr>
          <td>${esc(room.code)}</td>
          <td>${esc(room.name)}</td>
          <td>${esc(room.capacity)}</td>
          <td>${esc(room.room_type)}</td>
          <td><button data-delete-room="${room.id}">Delete</button></td>
        </tr>`).join("")
    : `<tr><td colspan="5" class="status-line">No rooms yet</td></tr>`;
  $("roomTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Code</th><th>Name</th><th>Capacity</th><th>Type</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("roomTableWrap").querySelectorAll("button[data-delete-room]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this room?")) return;
      await api(`/api/rooms/${btn.dataset.deleteRoom}`, { method: "DELETE" });
      showStatus("roomStatus", "room deleted", "ok");
      await loadRooms();
    });
  });
}

function renderSlots() {
  const rows = state.slotDraft.length
    ? state.slotDraft.map((slot, index) => `
        <tr>
          <td>${slot.day}</td>
          <td>${slot.period}</td>
          <td>${esc(slot.start)}</td>
          <td>${esc(slot.end)}</td>
          <td><button data-remove-slot="${index}">Remove</button></td>
        </tr>`).join("")
    : `<tr><td colspan="5" class="status-line">No slot rows yet</td></tr>`;
  $("slotTableWrap").innerHTML = `
    <table class="mini-table">
      <thead><tr><th>Day</th><th>Period</th><th>Start</th><th>End</th><th></th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
  $("slotTableWrap").querySelectorAll("button[data-remove-slot]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const index = parseInt(btn.dataset.removeSlot, 10);
      state.slotDraft.splice(index, 1);
      renderSlots();
    });
  });
}

function wireForms() {
  $("branchForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/branches", {
        method: "POST",
        body: JSON.stringify({
          code: $("branchCode").value.trim(),
          name: $("branchName").value.trim(),
          semester_label: $("branchSemLabel").value.trim(),
        }),
      });
      event.target.reset();
      showStatus("branchStatus", "branch added", "ok");
      await loadAll();
    } catch (err) {
      showStatus("branchStatus", err.message, "bad");
    }
  });

  $("divForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const branchId = parseInt($("divBranchSelect").value, 10);
    try {
      await api(`/api/branches/${branchId}/divisions`, {
        method: "POST",
        body: JSON.stringify({
          name: $("divName").value.trim(),
          program: $("divProgram").value,
          semester: parseInt($("divSemester").value, 10) || 1,
          student_count: parseInt($("divStudents").value, 10) || 60,
          batch1_name: $("divBatch1").value.trim(),
          batch2_name: $("divBatch2").value.trim(),
        }),
      });
      event.target.reset();
      showStatus("divStatus", "division added", "ok");
      await syncBranchScopedData();
    } catch (err) {
      showStatus("divStatus", err.message, "bad");
    }
  });

  $("facForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/faculty", {
        method: "POST",
        body: JSON.stringify({
          code: $("facCode").value.trim(),
          name: $("facName").value.trim(),
          max_load_hours_per_week: parseInt($("facMaxHours").value, 10) || 20,
          max_consecutive_sessions: parseInt($("facMaxConsec").value, 10) || 2,
          unavailable_slot_ids: parseIdList($("facUnavail").value),
        }),
      });
      event.target.reset();
      showStatus("facStatus", "faculty added", "ok");
      await loadFaculty();
    } catch (err) {
      showStatus("facStatus", err.message, "bad");
    }
  });

  $("courseForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const branchId = parseInt($("courseBranchSelect").value, 10);
    try {
      await api("/api/courses", {
        method: "POST",
        body: JSON.stringify({
          branch_id: branchId,
          code: $("courseCode").value.trim(),
          title: $("courseTitle").value.trim(),
          credits: parseInt($("courseCredits").value, 10) || 3,
          category: $("courseCategory").value,
          theory_per_week: parseInt($("courseTheory").value, 10) || 0,
          practical_per_week: parseInt($("coursePractical").value, 10) || 0,
          tutorial_per_week: parseInt($("courseTutorial").value, 10) || 0,
          is_heavy: $("courseHeavy").checked,
        }),
      });
      event.target.reset();
      showStatus("courseStatus", "subject added", "ok");
      await loadCourses(branchId);
    } catch (err) {
      showStatus("courseStatus", err.message, "bad");
    }
  });

  $("allocForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const courseId = parseInt($("allocCourseSelect").value, 10);
    const course = state.courses.find((c) => c.id === courseId);
    const divisionId = parseInt($("allocDivisionSelect").value, 10);
    const payload = {
      division_id: divisionId,
      course_id: courseId,
      faculty_id: null,
      batch1_faculty_id: null,
      batch2_faculty_id: null,
    };
    if (course && (course.practical_per_week || 0) > 0) {
      payload.batch1_faculty_id = parseInt($("allocBatch1Faculty").value, 10) || null;
      payload.batch2_faculty_id = parseInt($("allocBatch2Faculty").value, 10) || null;
    } else {
      payload.faculty_id = parseInt($("allocFaculty").value, 10) || null;
    }
    try {
      await api("/api/allocations", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      event.target.reset();
      showStatus("allocStatus", "allocation added", "ok");
      await loadAllocations(state.activeBranchId, state.activeDivisionId);
    } catch (err) {
      showStatus("allocStatus", err.message, "bad");
    }
  });

  $("roomForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api("/api/rooms", {
        method: "POST",
        body: JSON.stringify({
          code: $("roomCode").value.trim(),
          name: $("roomName").value.trim(),
          capacity: parseInt($("roomCapacity").value, 10) || 60,
          room_type: $("roomType").value,
        }),
      });
      event.target.reset();
      showStatus("roomStatus", "room added", "ok");
      await loadRooms();
    } catch (err) {
      showStatus("roomStatus", err.message, "bad");
    }
  });

  $("slotAddRowBtn").addEventListener("click", () => {
    state.slotDraft.push({
      day: parseInt($("slotDay").value, 10),
      period: parseInt($("slotPeriod").value, 10) || 0,
      start: $("slotStart").value.trim(),
      end: $("slotEnd").value.trim(),
    });
    renderSlots();
  });

  $("slotSaveBtn").addEventListener("click", async () => {
    try {
      await api("/api/slots", {
        method: "PUT",
        body: JSON.stringify(state.slotDraft),
      });
      showStatus("slotStatus", "slot grid saved", "ok");
      await loadSlots();
    } catch (err) {
      showStatus("slotStatus", err.message, "bad");
    }
  });
}

function initTabs() {
  document.querySelectorAll(".dash-tab").forEach((tab) => {
    tab.addEventListener("click", () => setSection(tab.dataset.section));
  });
  setSection("branchesSection");
}

async function init() {
  initTabs();
  wireForms();
  await loadAll();
}

init().catch((err) => {
  console.error(err);
  showStatus("branchStatus", err.message, "bad");
});

# design.md — DJ Sanghvi Dynamic Timetable Platform

**SIH problem statement 25091 · DJSCE, 2026 · Design specification v1.0 (July 2026)**

This document is the complete design for evolving this repository from a solver showcase into a
**web platform where every input is entered dynamically through the website** — no data files
edited in code. It is written to be executed phase-by-phase (§10) with acceptance criteria, and it
is deliberately honest about what already exists, what must be built, and what the current engine
cannot do (§12).

---

## 1. Vision & Scope

### 1.1 Requirements (from Goal.md, restated as testable statements)

| # | Requirement | Test |
|---|-------------|------|
| R1 | Faculty are created/edited/deleted in the website | Add a faculty member in the browser; they appear in the next generated timetable |
| R2 | Subjects (theory lectures, labs, tutorials) are managed in the website | Add a course with 3 theory + 1 practical sessions/week; the grid shows 3 lectures + one 2-slot batch-split lab |
| R3 | An academic calendar is uploaded as **pdf, jpg, or png** and its holidays/events become platform data | Upload the DJSCE 2026 calendar PDF; confirm holidays; they appear on the term view and drive the Adjust flow |
| R4 | Number of divisions per branch is set in the website | Change a branch from 2 to 3 divisions; the next run schedules D1–D3 |
| R5 | Number of branches is set in the website (DJ Sanghvi only, 2026) | Create a second branch (e.g. CSE-DS + IT); one conflict-free institution-wide timetable is produced |
| R6 | A window exists to dynamically adjust the timetable for sudden rain/holiday | Mark "rain from period 5 on Tuesday"; affected sessions are re-placed, everything else stays put, changes are highlighted |
| R7 | Zero data files created by hand in the repo | The only files the platform writes are `webapp/data/platform.db` and `webapp/uploads/*` |

### 1.2 Non-goals (unchanged from the MVP posture)

Authentication, multi-institution multi-tenancy, live AMS integration, and cloud infrastructure
are **explicitly out of scope** for this build. The design keeps the cloud path open (§14) but
builds none of it now. Single admin user, single institution (DJ Sanghvi), local deployment.

### 1.3 Design principles

1. **The engine is finished — don't touch it** (two small additive exceptions in §7).
   `timetable/` already solves the hard problem. The platform is a data-management and
   presentation layer around it.
2. **DB is the source of truth; `ProblemInstance` is a derived, per-run snapshot.**
3. **Nothing machine-extracted affects scheduling until a human confirms it** (§6).
4. **The master weekly timetable is never mutated by disruptions** — adjustments are dated
   overlays (§7).
5. **Additive-only evolution**: every engine change is a defaulted field; every new feature
   extends an existing module rather than adding a new subsystem.

---

## 2. What Already Exists (reused as-is)

| Module | Role | Platform reuse |
|--------|------|----------------|
| `timetable/models.py` | `ProblemInstance` + entities + `expand_requirements()` + `validate()` | **As-is** (+ one defaulted field, §7). `validate()` becomes the UI "readiness" linter |
| `timetable/scoring.py` | Single source of truth for hard/soft quality | **As-is** (+ blocked-slot rule and `relaxed_days`, §7) |
| `timetable/solvers/*` | Greedy, MIP, GA, CP-SAT — all warm-startable | **As-is** (candidate pruning gains blocked-slot filter, §7) |
| `timetable/pipeline.py` | `run_pipeline` 4-stage hybrid, `run_ensemble` baseline | **As-is**; UI exposes cpsat (default)/pipeline/greedy, plus Compare mode running all three at once (§9) |
| `timetable/view.py` | `solution_to_grids()` → grid JSON | **As-is**; the existing grid renderer keeps its contract |
| `timetable/export.py` | `.xlsx` / `.pdf` export | **As-is**, wired to new export endpoints |
| `timetable/io_json.py` | `problem_from_dict(dict)` → `ProblemInstance` | **The keystone.** It accepts a plain dict, so DB-built problems need **zero model changes**. Missing inverse `problem_to_dict` gets added (P0) |
| `webapp/static` grid renderer + stage track | Weekly grid + pipeline stage cards | **As-is** inside the new Generate page |
| `data/reference/djsce_cse_ds_sy_sem4.json` | Real DJSCE CSE-DS SY Sem IV data | Becomes the **seed fixture** behind `POST /api/seed/reference` — one-click onboarding, then fully editable in the UI |
| `cli.py`, `benchmarks/`, `tests/` | Dev/benchmark tooling | Untouched; must stay green throughout |

What does **not** exist today and must be built: persistence, entity CRUD (UI + API), file
upload/multipart, any calendar/holiday/date concept, a disruption flow, and a non-blocking job
pattern. That is precisely the scope of §4–§8.

---

## 3. System Architecture

```
Browser (vanilla-JS SPA, hash-routed wizard, ES modules)
   │ fetch JSON / multipart
   ▼
FastAPI (port 8750)
   ├── routers/ (branches, divisions, faculty, courses, rooms,
   │             allocations, slots, calendar, runs, adjust, seed)
   ├── problem_builder.py   DB rows ──► dict ──► io_json.problem_from_dict()
   ├── jobs.py              BackgroundTasks + timetable_run rows (queued→running→done/failed)
   └── SQLModel ── SQLite (webapp/data/platform.db)   +   webapp/uploads/ (calendar files)
                      │
                      ▼
        timetable/ engine (UNCHANGED contract)
        expand_requirements → solvers/pipeline → Solution → scoring / view / export
```

**Request lifecycle — Generate:** UI `POST /api/runs` → server snapshots the DB into a
problem dict (stored on the run row), inserts `timetable_run(status=queued)`, schedules a
background task, returns `{run_id}` immediately → task runs the pipeline in the threadpool,
writes solution + grids + stage reports + scores onto the row → UI polls `GET /api/runs/{id}`
every 2 s until `done`/`failed`, then renders the stored grids.

**Request lifecycle — Adjust:** UI `POST /api/adjust {run_id, date, scope, reason}` → server
loads the run's `problem_snapshot` + baseline solution → disruption engine (§7) pins, blocks,
relaxes, re-solves warm-started → writes an `adjustment` row with the new grids and the
moved-sessions diff → UI shows before/after with moves highlighted.

**Import direction rule:** `webapp` imports `timetable`; `timetable` never imports `webapp`.

---

## 4. Data Model & Persistence

### 4.1 Decision: SQLite via SQLModel

- **SQLite** — zero-install, zero-daemon, single file (`webapp/data/platform.db`), works
  immediately on the dev Windows box. The data is tiny (tens of faculty, dozens of courses) and
  strongly relational (Branch→Division→Allocation foreign keys). CLAUDE.md deferred MongoDB for
  good reason; nothing here needs documents.
- **SQLModel** — SQLAlchemy + Pydantic in one class; table models double as API request/response
  schemas, halving CRUD boilerplate across ~7 entity types. `SQLModel.metadata.create_all()` at
  startup.
- **Cloud portability** — swapping the connection URL to Postgres is the only change needed
  later; models are untouched. That is the entire cloud persistence story for now.
- **No Alembic yet.** Pre-cloud, single-user: "migrations" = delete the db file + re-seed.
  Documented posture, revisited only at cloud time.
- **Blobs on disk, rows in DB.** Uploaded calendar files live in `webapp/uploads/` with a DB row
  (path, mime, sha256). Problem snapshots, solutions, grids, and stage reports are JSON `TEXT`
  columns — fine at this scale and keeps runs perfectly reproducible.

### 4.2 Tables

| Table | Fields (key ones) | Notes |
|-------|-------------------|-------|
| `branch` | id, code ("CSE-DS"), name, semester_label | **New concept** — R5. User creates N branches |
| `division` | id, branch_id FK, name ("D1"), program enum, semester, student_count, batch1_name, batch2_name | 1:1 with engine `Division`; batch names default `{name}1`/`{name}2` — R4 |
| `faculty` | id, code ("AR"), name, max_load_hours_per_week (20), max_consecutive_sessions (2), unavailable_slot_ids JSON | **Global** — faculty are physically shared across branches — R1 |
| `course` | id, branch_id FK, code, title, credits, category enum, theory_per_week, practical_per_week, tutorial_per_week, is_heavy | Branch-scoped — R2 |
| `allocation` | id, division_id FK, course_id FK, faculty_id FK (nullable), batch1_faculty_id FK (nullable), batch2_faculty_id FK (nullable) | Encodes `Division.faculty_by_course`, incl. the (batch1, batch2) tuple form for split-batch practicals |
| `room` | id, code, name, capacity, room_type ("classroom"/"lab") | **Global** — rooms shared across branches |
| `slot_template` | id, day (0–4), period, start, end | One institutional weekly grid = engine `time_slots`; editable, seeded with the DJSCE 5×8 default |
| `term` | id, name ("Sem IV Jan–May 2026"), start_date, end_date | Created/edited on the Calendar page |
| `calendar_upload` | id, filename, mime, path, sha256, uploaded_at | Raw artifact — never modified or deleted by parsing |
| `calendar_event` | id, term_id FK, date, name, kind (holiday/exam/event), source ("manual"/"extracted"), confirmed bool | Extraction lands here **unconfirmed**; only `confirmed=1` rows do anything — §6 |
| `timetable_run` | id, label, solver, time_limit, status (queued/running/done/failed), created_at, problem_snapshot JSON, solution JSON, grids JSON, stage_reports JSON, hard, soft, error | The generate job record. The snapshot makes adjust/re-solve reproducible |
| `adjustment` | id, run_id FK, date, affected_slot_ids JSON, reason ("rain"/"holiday"/other), solution JSON, grids JSON, moved_sessions JSON, status ("active"/"reverted"), created_at | Dated overlay on a run — §7. `status` added 2026-07-21 for the undo flow; the run's effective view is baseline + active overlays only |

### 4.3 DB → ProblemInstance mapping contract

One new module, **`webapp/problem_builder.py`**:

```
build_problem_dict(session, branch_ids: list[int] | None = None) -> dict
```

assembles exactly the JSON shape `io_json.problem_from_dict` already parses:

- `slot_template` rows → `time_slots` (ids assigned in (day, period) order).
- `room` rows → `rooms`.
- `faculty` rows → `faculty` (codes become engine ids; `unavailable_slot_ids` → `unavailable_slots`).
- `course` rows → `courses` (enum values match `CourseCategory` strings).
- `division` + `allocation` rows → `divisions` with `faculty_by_course`: single faculty code, or a
  2-list `[batch1, batch2]` when both batch FKs are set (`problem_from_dict` already converts
  lists to tuples via `_faculty_value_from_json`).

Then: `problem_from_dict(d)` → `ProblemInstance` → **`problem.validate()`** — surfaced to the UI
*before* any solve as the readiness banner ("D2 has no faculty allocated for PBC"). The engine's
own validator is the platform's input linting, for free.

**Solve scope: the whole institution at once.** Rooms and faculty are genuinely shared across
branches, so per-branch solves would silently permit cross-branch clashes. The engine already
treats divisions as a flat list — multi-branch is just more divisions. Branch filtering in the UI
is a **view filter on the resulting grids only**, never a solve scope. Caveat: instance size grows
with branch count; the CP-SAT time budget is the knob (§12).

---

## 5. API Specification

All endpoints under `/api`. Port standardized to **8750** (8000 is OS-reserved on the dev
machine; the current server-default-8000 vs JS-hint-8750 mismatch is fixed in P0). Errors return
`{"detail": "..."}` (FastAPI default shape) with 4xx for validation and 404 for missing ids.

### 5.1 Entity CRUD (uniform SQLModel pattern)

`GET`/`POST` on the collection, `GET`/`PUT`/`DELETE` on the item:

| Resource | Routes | Validation highlights |
|----------|--------|----------------------|
| Branches | `/api/branches`, `/api/branches/{id}` | Deleting a branch cascades to its divisions/courses/allocations (confirm dialog in UI) |
| Divisions | `/api/branches/{id}/divisions`, `/api/divisions/{id}` | Unique name per branch |
| Faculty | `/api/faculty`, `/api/faculty/{id}` | Unique code; delete blocked while referenced by an allocation |
| Courses | `/api/courses?branch_id=`, `/api/courses/{id}` | Category must be a valid `CourseCategory`; at least one of theory/practical/tutorial > 0 |
| Rooms | `/api/rooms`, `/api/rooms/{id}` | room_type ∈ {classroom, lab} |
| Allocations | `/api/allocations?division_id=`, `/api/allocations/{id}` | If the course has practicals, both batch faculty FKs are required; else the single faculty FK |
| Slots | `GET /api/slots`, `PUT /api/slots` | Replace-all semantics on the whole weekly grid (simplest correct thing) |

**`POST /api/seed/reference`** — imports `data/reference/djsce_cse_ds_sy_sem4.json` into the DB
as one starter branch ("CSE-DS") with its divisions, faculty, courses, rooms, slot grid, and
allocations. This turns the currently-hardcoded dataset into an onboarding fixture: one click,
then everything is editable in the browser. Refuses to run twice unless `?force=true` (wipes and
re-seeds).

### 5.2 Calendar

| Route | Behavior |
|-------|----------|
| `POST /api/calendar/upload` | Multipart (**requires `python-multipart`**). Accepts pdf/jpg/png; validates magic bytes + opens with pillow (images) / pypdf (PDFs); rejects >20 MB; stores file + `calendar_upload` row; returns `{upload_id}` |
| `GET /api/calendar/uploads/{id}/file` | Serves the raw file so the frontend can render it beside the entry form |
| `POST /api/calendar/extract/{upload_id}` | Optional AI extraction (§6 Layer 1). Returns draft `calendar_event` rows, `source="extracted", confirmed=false`. 501 with a clear message if `ANTHROPIC_API_KEY` unset |
| `GET/POST/PUT/DELETE /api/calendar/events` | Review CRUD; `PUT /api/calendar/events/{id}/confirm` flips the flag |
| `GET/POST/PUT /api/terms` | Term dates |

### 5.3 Generation (background job + polling)

**Decision: FastAPI `BackgroundTasks` + polling. Not blocking, not Celery.**

| Route | Behavior |
|-------|----------|
| `POST /api/runs` `{solver, time_limit, label?, branch_ids?}` | Builds + validates the problem dict (400 with the validation list if not ready), stores it as `problem_snapshot`, inserts `timetable_run(status=queued)`, schedules the background task, returns `{run_id}` **immediately**. Rejects with 409 if a run is already `running` (single-flight guard). **Path note (2026-07-21 impl):** the DB-backed generate lives at `POST /api/runs` (create-run), not `/api/generate` — the latter stays the legacy in-memory showcase endpoint until the SPA Generate page retires it |
| `GET /api/runs/{id}` | `{status, hard, soft, grids, stage_reports, error}` — UI polls every 2 s |
| `GET /api/runs` | Run history (id, label, solver, status, scores, created_at) |
| `GET /api/runs/{id}/export.xlsx` / `.pdf` | Reconstructs `ProblemInstance` from the snapshot + solution and calls the existing `export.py` |
| `POST /api/runs {solver: "compare", solvers: ["cpsat","pipeline","greedy"], time_limit, label?}` | **New (2026-07-21, revised same day to add solver selection).** Runs each solver named in `solvers` on the same problem snapshot in one job; the run row stores one solution/grid/score per selected solver, keyed by name. UI renders a comparison table (`hard`/`soft`/`wall_clock_seconds` per solver) plus tabs to view each grid — the live version of the CLAUDE.md §13 benchmark table |

The solver code is synchronous; a background task with a sync function runs in Starlette's
threadpool, so the event loop stays responsive. **Solver choices exposed (revised 2026-07-21):**
`cpsat` is now the **default** (owner decision, following the team's own comparative-study
observation in CLAUDE.md §13 that CP-SAT produces the best-quality timetable). `pipeline` and
`greedy` remain selectable. See §9 for why `mip`/`ga`/`ensemble` stay engine-only.

**Compare mode — solver-selectable (revised 2026-07-21).** `solver: "compare"` runs multiple
solvers on the same snapshot in one job and returns them side by side. Which ones run is **not
fixed** — the request carries an explicit `solvers: [...]` list (checkboxes in the UI, §8.1) drawn
from the platform-exposed set `{cpsat, pipeline, greedy}`; the user picks any subset of ≥2 (a
single-item "compare" is just a regular generate). Defaults to all three if `solvers` is omitted.
Each selected solver gets its own row in `stage_reports`/the run's per-solver score map
(`hard`/`soft`/`wall_clock_seconds`), rendered as a comparison table with a grid tab per solver.
Raw `mip`/`ga`/`ensemble` are **not** offered in the Compare checklist — they stay engine+CLI-only
per §9's existing reasoning (fewer choices, less support surface on the platform).

**Honest limits (also in §12):** background tasks die with the process; no cancellation; one run
at a time. The cloud upgrade is swapping task scheduling for a worker queue **without changing
this API contract** — that is the "enhance, don't add maintenance surface" posture.

### 5.4 Disruption

| Route | Behavior |
|-------|----------|
| `POST /api/adjust` `{run_id, date, scope: "whole_day" \| {from_period: N}, reason}` | Same job pattern; date's weekday + scope → `affected_slot_ids`; runs the disruption engine (§7); writes an `adjustment` row |
| `GET /api/adjustments?run_id=` | List overlays for a run (each row tagged `status: "active"` \| `"reverted"`, §7 undo) |
| `GET /api/adjustments/{id}` | Grids + `moved_sessions` diff |
| `GET /api/adjustments/{id}/export.xlsx` / `.pdf` | Export of the adjusted day/week |
| `POST /api/adjustments/{id}/revert` | **New (2026-07-21) — "no holiday" undo.** Marks the adjustment `status="reverted"`; the run's *effective* current view falls back to the immediately-preceding overlay (or the master baseline if none). Does not delete the row — the disruption stays in history, just switched off. See §7 "Undo" |

---

## 6. Academic Calendar Ingestion — layered, manual-first

**Trust principle: extraction only ever produces draft rows; nothing affects scheduling until a
human confirms it in the review UI.**

- **Layer 0 — upload + side-by-side manual entry (always works; ships in P3).** The browser
  renders the uploaded PDF natively in an `<embed>` and images in an `<img>`, served from
  `GET /api/calendar/uploads/{id}/file`. Next to it: a date/name/kind entry table. This alone
  fully satisfies R3. Server-side needs only `python-multipart` plus cheap validation —
  `pillow` (is it really an image? downscale oversized photos), `pypdf` (valid PDF? page count;
  and a free bonus: text-layer extraction for born-digital PDFs feeds the same draft pipeline).
- **Layer 1 — optional Claude-vision extraction (same UI, additive).** If `ANTHROPIC_API_KEY`
  is set, `POST /api/calendar/extract/{id}` sends the image (or PDF as a document block) to the
  Claude API with a structured-output prompt → `[{date, name, kind}]` → inserted as
  `source="extracted", confirmed=false` drafts into the **same** review table the manual flow
  uses. Best quality on the dense, table-heavy layouts colleges actually publish. Degrades
  gracefully: no key → the Extract button is hidden and Layer 0 is unaffected.
- **Rejected: local tesseract (+ pdfplumber).** Painful Windows install, weak on scanned tabular
  calendars, and it would still need the review UI anyway — an added dependency and accuracy risk
  that saves nothing.

**What confirmed events actually do (honest scope):** the engine is a **weekly template solver
with no date axis** (§12). Confirmed holidays therefore do **not** reshape weekly generation.
They (a) render on the term calendar view, and (b) **pre-fill the Adjust flow** — clicking a
holiday date pre-populates `POST /api/adjust` for that date's weekday. That is exactly the R6
behavior, and it is the truthful description of the coupling.

---

## 7. Disruption Engine ("rain day" flow)  — ⚠️ SUPERSEDED, REBUILD PLANNED

**Status as of 2026-07-21: the minimal-change patcher below is BUILT and IMPLEMENTED (9 golden
tests in `tests/test_disruption.py`, full suite green), but the design decision it encodes has
been overridden — see "Revised decision" immediately below. The old design is kept in this section
for the historical record and because `timetable/disruption.py` still matches it until the rebuild
lands; treat everything under "Original (minimal-change) design" as describing current code, not
the target.**

**Revised decision (owner call, 2026-07-21): full re-solve from scratch, not minimal-change
patching.** On disruption the platform blocks the affected slots and re-runs a solver over the
**whole week**, producing a brand-new timetable rather than a same-day patch that silently drops
sessions it can't relocate. Rationale: dropping sessions (the old behavior) is worse than
re-optimizing globally — a rained-out lab should be findable a slot somewhere else in the week,
not discarded. This trades away the "every unaffected day is byte-identical to baseline" guarantee
in exchange for "nothing is silently lost."

**What changes in the rebuild:**

1. `replan()` no longer copies undisrupted days verbatim by default. Instead: `blocked_slot_ids`
   is set to the disruption window, `relaxed_days` covers the affected day (so its day-shaped hard
   rules don't falsely trip), and the **whole problem is resolved end-to-end** — same mechanism as
   a normal `POST /api/runs`, just with those two fields set on the snapshot.
2. **Warm start from the baseline solution is kept** (this part of the old design was already
   correct and stays): `AddHint`/`SetHint` biases the new solve toward the previous placement, so
   in practice most unaffected sessions *do* end up back where they were — but this is now an
   emergent property of warm-starting, not a hard guarantee enforced by pinning.
3. **No more silent drops.** Because the whole week is back in play, a displaced session almost
   always has somewhere to go; only a genuinely over-constrained instance (e.g. removing an entire
   day's capacity from an already-tight schedule) would still fail to place everything, and that
   surfaces as `hard_violations > 0` on the resulting run, not a quiet "dropped" list.
4. **`moved_sessions` diff is still computed** (baseline vs new assignment, same mechanism as
   today) so the UI can highlight everything that changed — expect this list to be much longer
   than under the old patcher, since a full re-solve can reshuffle far more than the disrupted day.
5. Solver choice for the re-solve follows §9's revised solver-exposure decision: default is
   **CP-SAT** (best quality, and the "comparison mode" — §9 — is available here too if the admin
   wants to see pipeline/greedy/CP-SAT side by side before accepting the re-plan).
6. This is a heavier operation than the old same-day patch (full-week CP-SAT solve vs. an
   effectively-instant direct construction) — the `time_limit_s` parameter now matters for real and
   should default to something in the 60–300 s range depending on instance size, not the old "30 s,
   pinned instance is tiny" assumption.

**Rebuild work (not yet started — design only, per owner instruction to update design.md first):**
`timetable/disruption.py`'s `replan()` needs a new code path that builds a `blocked_slot_ids` +
`relaxed_days` snapshot and calls the pipeline/CP-SAT solver instead of the direct-construction
greedy-style algorithm below; the 9 existing golden tests will need rewriting since they assert the
old "unaffected days untouched, overflow dropped" behavior.

**Undo ("no holiday" reversion) — new (2026-07-21).** Since a full re-solve (above) is a much
bigger perturbation than the old same-day patch, an admin needs an easy way to say "actually, no
disruption today" and get back to the working timetable — not just live with the reshuffled
result:

1. `POST /api/adjustments/{id}/revert` (§5.4) flips the `adjustment` row's `status` from `active`
   to `reverted`. **Nothing is deleted** — the row, its stored solution/grids, and its
   `moved_sessions` diff stay in the run's adjustment history for audit purposes.
2. **"Effective view" resolution rule:** for a given `run_id`, the timetable the UI shows by
   default is the run's baseline solution overlaid with only its `active` adjustments, most recent
   first. A `reverted` adjustment is skipped entirely when computing this view — so reverting the
   only/most-recent adjustment for a date takes the UI straight back to the master baseline grid,
   with zero re-solve needed (revert is a metadata flip, not a new job).
3. **Multiple adjustments per run:** if a run has had two disruptions overlaid (e.g. rain on
   Tuesday, then a holiday declared for Thursday), reverting the Tuesday one leaves Thursday's
   adjustment in effect — revert targets one `adjustment` row, not "reset the whole run."
4. **Re-reverting:** `POST .../revert` on an already-`reverted` row is idempotent (toggles nothing
   further; returns the current state). A separate `POST .../reactivate` is *not* added — per
   design principle 5 (additive-only, no new subsystem for a rare edge case); an admin who wants
   the disruption back just re-submits `POST /api/adjust` for the same date, which naturally
   produces a fresh `active` overlay.
5. **UI (Adjust page, §8.1):** each history row gets an "Undo" button when `status=active` and a
   greyed "Reverted" badge (with a "Re-apply" shortcut that re-fills the `POST /api/adjust` form
   with the same date/scope/reason) when `status=reverted`.

---

### Original (minimal-change) design — describes current code, superseded above

The only phase that touches `timetable/` (P4). New module: **`timetable/disruption.py`** — built,
with `POST /api/adjust` and a frontend Adjust panel, and 9 golden tests (`tests/test_disruption.py`,
full suite green). See CLAUDE.md §12 for the as-built summary. The design below is what was built,
with one deliberate deviation noted: the re-plan is constructed **directly** rather than via a
warm-started exact solver, because `AddExactlyOne` in MIP/CP-SAT cannot express "this rained-out
session is simply dropped" — it would report the model infeasible instead. Direct construction is
always feasible, deterministic, and truly minimal-change. The `blocked_slot_ids` / `relaxed_days` /
`pinned_slots` engine fields were still added and are honored by all four solvers, so an admin can
also manually re-solve a blocked-window scenario with any solver for a full re-optimization.

**What exists already:** `SessionRequirement.fixed_time_slot_id` (solver-honored pin — hard
constraint 13), warm starts in all four solvers (CP-SAT `AddHint` is reliable), the pipeline's
monotonic-best safety net, deterministic `expand_requirements` ordering, and
`problem_from_dict` for snapshot reload.

**What must be added (both additive, both defaulted, both backward-compatible):**

1. **`ProblemInstance.blocked_slot_ids: frozenset[int] = frozenset()`** — slots nothing may be
   scheduled into. Enforced once in `solvers/candidates.py` pruning (covers MIP + CP-SAT), a
   one-line check in greedy, a repair rule in GA, and a **hard-violation rule in `scoring.py`**
   (scoring stays the single source of truth, per repo convention). Default empty = today's
   behavior; all existing tests unaffected.
2. **`relaxed_days: frozenset[int]`** — exempts a disrupted day from the day-shaped hard rules
   (daily load 6–8, labs-every-day, one-break-per-day). Without this, every holiday adjustment
   would score as infeasible. Implemented once in `scoring.py`, mirrored by skipping those
   constraint groups for relaxed days in the MIP/CP-SAT builders.

**The algorithm (`replan(problem, baseline_solution, affected_slot_ids, time_limit)`):**

1. Reload `problem_snapshot` → `problem_from_dict` → `expand_requirements` (deterministic order —
   this is why snapshots re-expand identically).
2. Compute `affected_slot_ids` from the request (whole day, or all periods ≥ N for "rain from
   2 pm").
3. **Pin the untouched majority:** every session whose baseline slot(s) don't intersect
   `affected_slot_ids` gets `fixed_time_slot_id = its baseline slot`. Exact minimal-change for
   everything outside the disruption, reusing the existing protected-block mechanism — no solver
   changes needed for this part.
4. Set `blocked_slot_ids = affected_slot_ids` and `relaxed_days = {affected day}` so displaced
   sessions can't land back in the blocked window and the disrupted day's shape rules don't
   declare the result infeasible.
5. Solve with `warm_start = baseline` (biases moved sessions toward familiar placements) and a
   short budget (30–60 s — the pinned instance is tiny). CP-SAT alone or the pipeline.
6. Store as an **`adjustment` overlay**; the master run is never mutated. The response includes
   `moved_sessions` (computed by diffing baseline vs new assignment maps) so the UI highlights
   exactly what changed.

**Explicit non-goal (stated honestly):** a true minimal-change objective term
(`soft += w · count(assignment ≠ baseline)`). Pinning + warm start approximates it well; the
exact CP-SAT objective term is future feature F7 (§13).

---

## 8. Frontend

**Decision: extend the existing vanilla-JS SPA.** No React, no node toolchain, no build step —
the current renderer is ~200 lines of clean fetch/render code and the platform needs forms +
tables + the grid that already works. Growth plan: split `app.js` into native ES modules
(`static/js/api.js`, `static/js/pages/*.js`) loaded via `<script type="module">`. A future
framework migration is not foreclosed because the API is the contract.

### 8.1 Pages (left-nav wizard, hash-routed `#/faculty` etc., in natural data-entry order)

1. **Branches & Divisions** — R4/R5: create branches, set division count/names per branch.
2. **Faculty** — R1: code, name, load caps, unavailable slots (grid picker).
3. **Subjects** — R2: per branch; theory/practical/tutorial per week, credits, category, heavy flag.
4. **Allocations** — division × course → faculty matrix; batch-pair inputs appear **only** for
   courses with practicals.
5. **Rooms & Slots** — room list (classroom/lab, capacity) + weekly slot-grid editor.
6. **Calendar** — R3: upload pane | native file preview | events review table (drafts vs
   confirmed, Extract button when available).
7. **Generate & View** — solver/time-budget controls (CP-SAT default). A **Compare** toggle
   reveals a checklist (cpsat/pipeline/greedy, any subset of ≥2) letting the admin pick exactly
   which solvers to run simultaneously for that generation, rather than a fixed trio → poll with
   progress → existing color-coded division grids + pipeline stage track + run history + export
   buttons. When Compare is used, a comparison table (hard/soft/wall-clock per selected solver,
   §5.3) renders above tabs for each solver's grid. A **readiness banner** (from server-side
   `validate()`) lists what's missing before Generate is enabled.
8. **Adjust** — R6: pick run → pick date (holiday dates pre-listed from confirmed events) →
   scope (whole day / from period N) + reason → run → **side-by-side before/after** grids with
   moved sessions highlighted in the accent color. Adjustment history list has an **Undo** button
   per active overlay (instantly reverts to baseline/prior overlay, no re-solve) and a **Re-apply**
   shortcut on reverted ones (§7 Undo).

### 8.2 DJ Sanghvi palette (light theme; extracted from djsce.ac.in CSS — no logo per Goal.md)

Brand tokens: navy `#003877` (dominant), orange `#f26d21` (single accent), white, black, greys
`#5f5f5f`/`#58595b`, light greys `#dedede`/`#f5f5f5`/`#f6f7f8`.

All theming flows through the existing `:root` CSS variables — the grid renderer needs **no JS
changes**:

| Variable | Current (dark) | New (DJSCE light) |
|----------|----------------|-------------------|
| `--bg` | `#0f1220` | `#f6f7f8` |
| `--panel` | `#191d30` | `#ffffff` |
| `--panel-2` | `#212642` | `#f5f5f5` |
| `--line` | `#2c3352` | `#dedede` |
| `--text` | `#e8ebf5` | `#000000` |
| `--muted` | `#9aa3c7` | `#5f5f5f` |
| `--accent` | `#6d7cff` | `#003877` — buttons, topbar, active nav |
| `--accent-2` | `#35d0a5` | `#f26d21` — highlights, CTAs, moved-session marker |
| `--theory` | `#3b82f6` | navy tint (12% `#003877` fill, navy text) |
| `--lab` | `#a855f7` | orange tint of `#f26d21` |
| `--tut` | `#f59e0b` | `#58595b` grey |
| `--brk` | `#4b5266` | `#dedede` |
| `--good` / `--bad` | keep | keep — green/red are status semantics, not brand |

Topbar becomes solid `#003877` with white text (mirrors the djsce.ac.in header); the radial
gradients and gradient buttons are replaced with flat brand surfaces.

---

## 9. Redundancy Removal Proposals (conservative)

| Candidate | Decision | Rationale |
|-----------|----------|-----------|
| `SINGLE_SOLVERS` dict + pipeline-config block duplicated in `cli.py` and `webapp/server.py` | **Consolidate** into a `SOLVERS` registry in `timetable/solvers/__init__.py`; both import it | Also gives the currently-empty `__init__.py` a purpose (it stays — package marker) |
| `io_json` solution save/load (`save_solution`/`load_solution`/`solution_to_dict`/`from_dict`) | **Keep** | `tests/test_export.py` round-trips through them, and the platform now stores solutions as JSON — they become load-bearing |
| Missing `problem_to_dict` (docstring at `io_json.py:4` references it; it doesn't exist) | **Add** (P0) | Anti-redundancy: fixes a documented lie, and it is required for `timetable_run.problem_snapshot` |
| `run_ensemble` | **Keep in engine, drop from platform UI** | Its purpose is the benchmark narrative (`compare_solvers.py`, CLAUDE.md). The UI exposes `cpsat` (default) / `pipeline` / `greedy`, plus the new **Compare mode** (§5.3) that runs all three at once for a side-by-side comparison table. Raw `mip`/`ga` stay engine + CLI only |
| Hardcoded `_load()` + reference-dataset path in `server.py` | **Retire**; dataset becomes the `POST /api/seed/reference` fixture | Satisfies R7 while keeping the valuable real-world data |
| Port mismatch (server default 8000; `app.js` hint 8750) | **Fix**: 8750 everywhere (P0) | 8000 is OS-reserved on the dev machine |
| `expand_requirements` tutorial loop reuses stale `sync_group_id` (`models.py:282–292`) | **Fix** (P0): compute per-loop | Verified latent bug: `NameError` if the first course has tutorials but no theory; wrong OE sync group otherwise |
| `sample_data.py` synthetic generator, `benchmarks/`, `references/` | **Keep untouched** | The benchmark harness is a core stated asset, not redundancy |

Rule: never delete anything tests import; run `pytest -q` after every removal.

---

## 10. Phased Roadmap (ASAP ordering, with acceptance criteria)

| Phase | Scope | Files | Done when |
|-------|-------|-------|-----------|
| **P0 — Groundwork** (½ day) | Solver registry; port 8750 default; add `problem_to_dict`; fix `sync_group_id` bug | `timetable/solvers/__init__.py`, `webapp/server.py`, `webapp/static/app.js`, `timetable/io_json.py`, `timetable/models.py`, `cli.py` | `pytest -q` green; `cli.py` and server share one registry |
| **P1 — Persistence + CRUD** (2–3 days) | SQLModel tables, entity routers, seed endpoint, SPA nav shell + entry pages 1–5 | `webapp/db.py`, `webapp/models_db.py`, `webapp/routers/*.py`, `webapp/seed.py`, `webapp/static/js/api.js`, `webapp/static/js/pages/*.js` | Reference dataset seeded with one click; every entity editable in the browser |
| **P2 — Generate + Grids** (1–2 days) | `problem_builder`, jobs, generate/poll/runs/export endpoints, Generate page, readiness banner | `webapp/problem_builder.py`, `webapp/jobs.py`, `webapp/routers/runs.py`, Generate page JS | DB-entered data produces a rendered, exportable timetable with `hard = 0` |
| **P3 — Calendar** (1–2 days) | Upload + validation + `uploads/`, events review CRUD + UI (Layer 0), optional Claude extraction (Layer 1) | `webapp/routers/calendar.py`, `webapp/extract_calendar.py`, Calendar page JS | A real DJSCE calendar PDF uploaded, holidays confirmed, term view renders |
| **P4 — Disruption** ✅ **DONE** (built ahead of P1–P3, against an in-memory baseline) | `disruption.py`; `blocked_slot_ids` + `relaxed_days` + `pinned_slots`; `/api/adjust`; Adjust panel with moved/dropped diff | `timetable/disruption.py`, `timetable/models.py`, `timetable/scoring.py`, `timetable/view.py`, `timetable/solvers/candidates.py` + `greedy.py` + `ga.py` + `mip.py` + `cpsat.py`, `webapp/server.py`, `webapp/static/*` | ✅ "Rain from period 5 on Tuesday" re-plans instantly, other days untouched, overflow dropped; 9 golden tests + full suite green |
| **P5 — Polish** (1 day) | DJSCE light theme, wizard UX pass, docs sync | `webapp/static/style.css`, `index.html` | Palette matches §8.2; end-to-end walkthrough of R1–R7 passes |

Total: **~8–11 working days.**

---

## 11. Testing Strategy

- **Engine tests stay green at every phase** — they are the regression net for the only shared
  asset. `python -m pytest tests/ -q` after each phase.
- **API tests per router** with `fastapi.testclient.TestClient` (needs `httpx`), on a temp
  SQLite file: CRUD round-trips, seed endpoint idempotence, generate→poll happy path with
  `greedy` + tiny time limits (keep tests fast, per repo convention), 400-on-invalid-problem,
  409-on-concurrent-run.
- **Disruption golden test** (P4): seed the reference dataset, greedy baseline, block Tuesday
  periods 5–8, assert (a) no assignment lands in blocked slots, (b) every unaffected session kept
  its baseline slot, (c) `hard_violations == 0` under `relaxed_days`, (d) `moved_sessions` lists
  exactly the displaced set.
- **Upload validation tests**: wrong magic bytes rejected, oversized file rejected, pdf/jpg/png
  accepted.
- Extraction (Layer 1) is **not** unit-tested against the live API; the review/confirm state
  machine is tested with fabricated draft rows instead.

---

## 12. Risks & Honest Gaps

1. **No date axis in the engine.** It solves a weekly template. Holidays don't reshape weekly
   generation; they surface on the term view and pre-fill Adjust. A true dated-semester schedule
   (each calendar week materialized, holidays removing real days) would be a significant engine
   redesign — out of scope, and not required by R1–R7 as stated.
2. **BackgroundTasks die with the process.** A server restart orphans a `running` run (startup
   sweep marks stale `running` rows as `failed`). No cancellation. Acceptable single-admin
   posture; the worker-queue swap is the cloud-phase fix and doesn't change the API.
3. **Extraction trust boundary.** AI/OCR output is never auto-confirmed; the review UI is the
   gate. This is a feature, not a limitation — a wrong holiday silently entering scheduling is
   worse than a click.
4. **Institution-wide solve scaling.** More branches → bigger instance → longer CP-SAT
   convergence (the team observed 5–10 min at realistic sizes). Mitigations: time-budget knob,
   greedy preview, `validate()` catching structural errors before burning solver time.
5. **Single-flight generation.** One run at a time by design; concurrent admins would queue
   behind a 409. Fine for one institution, revisit at cloud time.
6. **SQLite concurrency.** Single-writer; irrelevant single-admin, revisit with Postgres.

---

## 13. Future Features (additive-only, per Goal.md)

Each extends an existing module; none creates a new subsystem to maintain:

| # | Feature | Extends |
|---|---------|---------|
| F1 | Per-faculty / per-room timetable views | Second pivot next to `solution_to_grids` in `view.py`; same renderer |
| F2 | Branded letterhead exports (navy header, per-division sheets) | `export.py` |
| F3 | Substitute-faculty suggestions in Adjust (rank free faculty by availability/load) | Read-only pass over existing `scoring.py` availability data |
| F4 | "Improve this timetable" button (longer CP-SAT budget, warm-started from the stored solution) | Existing warm-start machinery end-to-end |
| F5 | Faculty `preferred_slots` editing (field exists, no UI uses it) | Faculty page + existing soft costs |
| F6 | iCal (.ics) export of confirmed holidays + weekly grid | Data already in DB/grids |
| F7 | Exact minimal-change objective term (`assignment ≠ baseline` penalty) | CP-SAT objective only; upgrades §7's approximation |
| F8 | Benchmark tab surfacing `compare_solvers.py` results | Read-only UI over existing CSV output |
| F9 | Attendance-friendly export (per-division daily session lists) | `export.py` variant |

---

## 14. Cloud Deployment Sketch (future-only, zero work now)

- **DB:** change the SQLModel connection URL from SQLite to managed Postgres; models unchanged;
  adopt Alembic at that point.
- **Jobs:** replace BackgroundTasks scheduling with a worker (RQ/arq) behind the **same**
  `/api/runs` → `/api/runs/{id}` contract.
- **Files:** `webapp/uploads/` → object storage behind the same upload/serve endpoints.
- **Serving:** uvicorn behind a reverse proxy; the SPA is already static files.
- **Then and only then:** authentication and multi-tenancy (still deferred).

---

## 15. Research Contributions & Experimental Validation

This section is a **separate track from the P0–P5 platform build** — it exists to convert the
project's implicit research claims into demonstrated, honestly-caveated ones for the report/paper.
It lives in a new top-level **`research/`** directory (adapter scripts, ablation runner, survey
instrument + collected data, Pareto-sweep scripts, and a written `research/REPORT.md`), kept
separate from `timetable/`'s additive-only production discipline. It neither blocks nor is
blocked by the platform roadmap. The one exception that *does* touch production code is §15.1,
and even that is a drop-in behind an interface that already exists.

**Honest framing of what's currently novel vs. asserted:** the individual solvers (MIP, GA,
CP-SAT) are each well-studied in isolation; they are not the contribution. Two things are:
(a) the **cooperative 4-stage hybrid** — chaining an exact solver, a metaheuristic, and a second
exact solver via warm-start handoffs — which is less common than same-paradigm hybrids in the
literature, and (b) the **NEP 2020 domain adaptation** (batch-split labs solved simultaneously in
different rooms, cross-division open-elective sync, credit-category coverage as a hard
constraint), since most UCTTP literature targets Western credit structures. One current claim is
**overclaimed and should be fixed first**: `ml_predictors.py`'s "GA+ML hybrid" has no trained
model anywhere — the three predictors are heuristic rule-based stand-ins (correctly documented as
such in CLAUDE.md §8). §15.1 is the fix.

### 15.1 Trained ML Predictors (replace the heuristic stand-ins)

No historical academic-performance dataset exists for DJSCE — the fix is small-scale primary data
collection, not waiting for one:

- **Data:** a short survey (target N=50–150 responses) to DJSCE students/faculty rating
  self-reported difficulty/fatigue against described schedule patterns (subject category,
  sessions/day, consecutive-lab count, time-of-day). Feature set mirrors the existing predictor
  input dicts — no interface change.
- **Models:** simple, interpretable regressors appropriate to the small N — Ridge regression or a
  shallow gradient-boosted tree. Deliberately not deep learning, consistent with the team's own
  rationale in CLAUDE.md §12 for rejecting GNN/DRL (data-hungry, unexplainable, and this project
  doesn't have — or need — that scale of data).
- **Validation:** k-fold CV given the small N; report R²/MAE honestly, and document limitations
  (self-report bias, DJSCE-specific, small sample) plainly. Transparency about a modest result is
  more defensible than an overclaimed heuristic presented as "ML."
- **Swap-in:** models still expose `predict(features: dict) -> float in [0,1]`; `ga.py` requires
  **zero changes** — the interface already anticipated this swap.
- **Deliverable:** methodology write-up, fitted model artifacts, and a before/after comparison
  (heuristic vs. trained predictor) on the same benchmark instance.

### 15.2 Equal-Compute-Budget Ablation Study

Tests the pipeline's core claim ("the hybrid beats any single solver") under a fair comparison
rather than an anecdote:

- **Design:** fix a total wall-clock budget T (e.g. 60s, 300s). Conditions: (a) CP-SAT alone for
  T, (b) MIP alone for T, (c) GA alone for T, (d) the pipeline with T split across its stages.
  N=10–20 seeds × each of small/medium/large/reference scale.
- **Metrics:** `hard_violations`, `soft_cost` (mean ± std); significance via **Wilcoxon
  signed-rank** (paired per seed) — a nonparametric test that fits the small-N setting better than
  a t-test.
- **Sharper secondary ablation:** pipeline with warm-starts disabled vs. enabled, at the same
  total budget. This isolates whether the **cooperative handoff** — not merely "more solvers" —
  drives the improvement, which is the actual mechanism CLAUDE.md §10 claims.
- **Deliverable:** results table + convergence-over-time plots (soft_cost vs. elapsed seconds) per
  condition/scale, replacing the current single-run anecdote in CLAUDE.md §13.

### 15.3 Multi-Objective Pareto Analysis

`soft_cost` today is a single weighted scalarization, which hides real trade-offs between
competing goals:

- **Objectives:** pick 2–3 genuinely competing soft terms, e.g. faculty-workload balance vs.
  student idle-gap minimization vs. room-capacity efficiency.
- **Method:** **epsilon-constraint sweep on CP-SAT** — fix objective A ≤ ε as an added constraint,
  optimize objective B, sweep ε to trace the frontier. Reuses the existing CP-SAT model with one
  added constraint; stays exact and reproducible, no new algorithm required.
- **Stretch goal (documented, not required):** a true multi-objective GA (NSGA-II-style
  non-dominated sorting + crowding distance) on top of the existing hand-rolled GA — feasible
  later since it isn't library-locked, but the epsilon-constraint version is the actual deliverable
  now.
- **Deliverable:** Pareto front plots per objective pair on the reference dataset, framed as
  evidence against arbitrary fixed-weight scalarization.

### 15.4 Infeasibility Diagnosis

`scoring.py` currently returns `hard_violations` as a bare count with no explanation of the
bottleneck:

- **Method:** wrap each hard-constraint group as a CP-SAT assumption literal (`AddAssumption`) and
  call the solver's `SufficientAssumptionsForInfeasibility()` to extract the minimal conflicting
  subset when a run can't reach `hard=0`. No bespoke IIS logic needed — CP-SAT exposes this
  directly.
- **CBC asymmetry (documented, not fixed):** `pywraplp`/CBC has no equivalent tooling, so this
  feature is **CP-SAT-only**. Acceptable: CP-SAT already produces the platform's best solutions
  and is the one the UI leans on.
- **Presentation:** map the returned constraint-group identifiers back to human terms
  ("Faculty AR needs 26h in Division D2 but is capped at 20h — reduce allocations or raise the
  cap"), surfaced in the readiness banner / failed-run view (§8.1 page 7).
- Research framing: explainable constraint satisfaction — strengthens the platform's UX and its
  research legitimacy at the same time.

### 15.5 External Validation Against Public Benchmarks

All current validation is against one real dataset (DJSCE) plus internally-generated synthetic
scales — no comparison against the wider academic literature:

- **Adapter:** a one-off `research/itc_import.py` (kept out of `timetable/` and `webapp/` — it's
  an evaluation tool, not a platform feature) mapping **ITC-2007 Curriculum-Based Course
  Timetabling** instances into `ProblemInstance` (curriculum→division, course→course,
  room/period→engine equivalents). Document simplifying assumptions explicitly where NEP
  2020-specific structure (batch-split labs, credit categories) has no ITC equivalent — do not
  gloss over the mismatch.
- **Run:** all four solvers + the pipeline on a handful of standard ITC instances; report scores
  against published best-known results from the competition/literature.
- **Cheaper complementary option:** obtain a second real dataset from another DJSCE
  department/branch and test whether the same solver configuration generalizes without extra
  tuning — directly relevant now that the platform supports multiple branches (§1.1 R5).
- **Deliverable:** a comparison table (this project's result vs. published best-known) plus the
  honest caveat list for any structural mismatches.

### 15.6 Rejected: Proactive Slack for Disruption Robustness

Considered and rejected: pre-building buffer/idle periods into the base timetable to absorb future
disruptions. Rejected because it directly fights the existing soft constraints (idle-gap
minimization, daily load 6–8h) — it would make every ordinary day worse (longer days, more idle
time, more student fatigue) to hedge against occasional rain/holiday events. The reactive
re-solve design in §7 is the correct trade-off: disruptions are rare, so pay the cost only when
one actually happens, not every day.

---

## 16. Scoring Refinements — Break Placement & Day-Span (design decision, not yet implemented)

**Status: decision recorded 2026-07-21; `timetable/scoring.py` is unchanged so far — this is scope
for a dedicated implementation pass, not a description of current code.**

**Observation that prompted this:** the greedy solver already varies the break's period across a
division's five days (`greedy.py:143-157` rotates through mid-day candidates via `day %
len(by_closeness)`), but the soft-cost term that steers MIP/GA/CP-SAT
(`scoring.py:290-306`, `soft["break_not_midmorning"]`) nudges every day toward one fixed
`target_period` (~35% into the day, every day, identically). Since the exact solvers optimize
against that fixed target, they converge on placing the break at essentially the same period on
every day — which reads as "hardcoded" even though `is_break`/`fixed_day` (hard constraints) never
actually pinned the *period*, only the day.

### 16.1 Dynamic break period across days

**Decision:** replace the single fixed `target_period` with a per-day-varying target, mirroring
what greedy already does, so CP-SAT/MIP/GA are rewarded for spreading break periods across the
week rather than converging on one.

- **Where:** `scoring.py`'s `soft["break_not_midmorning"]` loop (currently keys the target purely
  off `day_slots[0].period` and day length, which is identical for every day in practice).
- **Approach:** derive the per-day target the same way greedy does — rotate through the mid-day
  band candidates keyed by `day` (e.g. `target_period = mid_choices[day % len(mid_choices)].period`
  instead of one constant) — so the soft cost actively rewards day-to-day variation instead of
  merely tolerating it.
- **Constraint to preserve:** this is a **soft** nudge only; the existing hard rule
  (`break_outside_midday_band`, scoring.py:110-118 — never first two or last period of the day)
  must keep governing correctness. Only the *target* within that legal band becomes day-varying.
- **Mirror in MIP/CP-SAT builders:** wherever the objective linearizes this term (per §7/§8/§9 MIP
  and CP-SAT sections of CLAUDE.md), the per-day target needs to flow through identically so both
  exact solvers see the same varying incentive, not just the reference scorer.

### 16.2 Discourage the full 8 AM–6 PM span

**Observation:** the institutional `slot_template` spans the full 8 AM–6 PM window (per CLAUDE.md
§11/design.md §4.2), but hard constraint 4 only requires **6–8 academic hours/day** per division —
a 10-hour slot grid leaves up to 2–4 hours of legal slack per day that nothing currently discourages
a solver from using if it's cheap soft-cost-wise (e.g. spreading sessions thin across the whole
day rather than compacting them).

**Decision:** add an explicit soft-cost term that penalizes a division's occupied span (`max
period − min period` for the day, already computed for `idle_gaps`/`earliest_latest_same_day` at
scoring.py:279-288) the closer it gets to the full slot-template width, on top of the existing
idle-gap and earliest+latest-same-day soft terms — i.e. treat "uses nearly the whole day" as its
own soft cost, not just an emergent effect of idle-gap counting (a compact 6-hour day with zero
gaps scores identically to a spread 10-hour day with the same zero gaps today, which is the actual
gap this closes).

- **Where:** extend the same `for (div_id, day), periods in division_day_periods.items():` loop
  (scoring.py:279) that already derives `s, e = min(periods), max(periods)`.
- **Formula sketch:** `soft["day_span"] += max(0, (e - s + 1) - target_span)`, where `target_span`
  is chosen near the middle of the legal 6–8h range (so an 8h day-length-in-periods worth of span
  isn't penalized, but stretching toward the full slot-template width is).
- **Interacts with existing soft weights:** needs its own entry in the soft-cost weight table
  (scoring.py's weight dict, alongside `break_not_midmorning: 1.5` etc.) — weight TBD during
  implementation, tuned against the benchmark suite so it doesn't dominate the existing gap/
  workload terms.
- **Not a hard constraint:** deliberately kept soft — a division genuinely needing 8h on a given
  day (allowed by constraint 4) must remain feasible; this only discourages *unnecessary* spread,
  it doesn't forbid using the full legal range when the credit load requires it.

### 16.3 Implementation checklist (for the dedicated pass, when scheduled)

1. `scoring.py`: day-varying break target (§16.1) + new `day_span` soft term (§16.2) + weight entry.
2. `solvers/mip.py` / `solvers/cpsat.py`: mirror both terms in the linearized/CP-SAT objective
   (per repo convention — scoring.py is the source of truth, solvers reference it, not re-derive).
3. `solvers/ga.py`: no change needed — GA's fitness already calls `scoring.score()` directly.
4. `tests/test_breaks.py`: extend to assert break periods vary across a division's week (not just
   legality), and add a day-span regression test.
5. Re-run `benchmarks/compare_solvers.py` to confirm the new soft terms don't regress overall
   `soft_cost` ranking (CP-SAT still best) — update the CLAUDE.md §13 log if numbers shift.

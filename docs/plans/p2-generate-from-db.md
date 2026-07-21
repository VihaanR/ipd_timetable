# Plan: P2 — Generate a timetable from database data

## Goal

Turn the DB entities (seeded/edited via the P1 CRUD API) into an actual solver run: build a
`ProblemInstance` snapshot from the DB, run a solver as a background job, store the solution +
rendered grids + scores on a run row, let the client poll for completion, and export the result to
xlsx/pdf. This is the "DB data → a rendered, exportable timetable" milestone (design.md §10 P2).

Scope is **backend only** (a browser Generate page is a later frontend pass). Everything here is
verifiable with `fastapi.testclient.TestClient` against a temp SQLite DB.

## Context / current state

- P0 + P1 are done and green (53 tests). The engine (`timetable/`) is finished and must NOT be
  modified. `webapp/` already has: `db.py` (SQLite engine + `get_session` dependency + `set_engine`
  for tests + `init_db`), `models_db.py` (branch/division/faculty/course/allocation/room/
  slot_template tables, each with Base/table/Create/Update classes), `routers/` (entity CRUD +
  `_crud.py` helpers), `seed.py` (`POST /api/seed/reference`), and `server.py` (FastAPI app assembled
  with a lifespan `init_db()`, routers included, plus LEGACY showcase endpoints /api/problem,
  /api/generate, /api/adjust that read synthetic/reference data — leave those legacy endpoints
  alone; this plan adds new DB-backed endpoints).
- Engine entry points to reuse (do not reimplement):
  - `from timetable.io_json import problem_from_dict, problem_to_dict, solution_to_dict, solution_from_dict`
  - `from timetable.solvers import SOLVERS` (name→class: greedy/mip/ga/cpsat)
  - `from timetable.pipeline import run_pipeline, PipelineConfig`
  - `from timetable.scoring import score` (returns ScoreResult with `.hard_violations`, `.soft_cost`)
  - `from timetable.view import solution_to_grids` (Solution, ProblemInstance) → grids JSON
  - `from timetable.export import export_xlsx, export_pdf` (Solution, ProblemInstance, path)
  - `ProblemInstance.validate()` → `list[str]` of structural issues (empty = ready)

## Global Constraints (binding — copy into every reviewer dispatch)

- **The engine (`timetable/`) is finished and MUST NOT be modified.** `webapp` imports `timetable`,
  never the reverse. If a task seems to need an engine change, that is a BLOCKED escalation.
- **The DB is the source of truth; `ProblemInstance` is a derived per-run snapshot.** Each run row
  stores its own `problem_snapshot` JSON (via `problem_to_dict`) so the run is reproducible.
- **Solvers never raise on infeasible input** — they return a `Solution` with a status; scoring
  penalizes it. Background jobs must still wrap the solve in try/except and record `status=failed`
  + `error` on any exception.
- **Port is 8750. The only filesystem locations code may write are `webapp/data/` and
  `webapp/uploads/`.** Export endpoints must write temp files under the OS temp dir or stream
  bytes — not into the repo tree.
- **Engine additions are forbidden here, but `webapp/models_db.py` may gain new tables** (additive).
- **Tests must stay green:** `python -m pytest tests/ -q`. New API tests use `TestClient` + a temp
  SQLite DB via `webapp.db.set_engine(create_engine(f"sqlite:///{tmp}", connect_args={"check_same_thread": False}))`
  then `init_db()` — follow the existing pattern in `tests/test_api_entities.py` exactly (fixture
  named `client`, `engine.dispose()` on teardown). Keep solver time limits tiny (`greedy`,
  `time_limit<=3`) so tests are fast — do NOT assert `hard==0` in fast tests (that needs cpsat/
  pipeline and minutes); assert `status=="done"` and grids present instead.
- **API error shape is FastAPI default `{"detail": ...}`**; 400 for validation, 404 for missing id,
  409 for conflict. `detail` may be a list for validation issues.
- Follow existing code style: `from __future__ import annotations`, type hints, `APIRouter(prefix="/api")`,
  `Depends(get_session)`. Match the structure of the existing routers.

## Task 1: problem_builder — assemble a ProblemInstance from the DB

**File:** `webapp/problem_builder.py` (new). **Tests:** `tests/test_problem_builder.py` (new).

Implement two functions:

```python
def build_problem_dict(session: Session, branch_ids: list[int] | None = None) -> dict
def readiness(session: Session, branch_ids: list[int] | None = None) -> tuple[ProblemInstance | None, list[str]]
```

`build_problem_dict` assembles exactly the JSON shape `io_json.problem_from_dict` parses (see
`data/reference/djsce_cse_ds_sy_sem4.json` and `problem_from_dict` for the target shape):

- **time_slots:** all `SlotTemplate` rows sorted by `(day, period)`; the engine `id` is the 0-based
  index in that sorted order (so slot ids are 0..N-1 in day-major order — this matches the reference
  dataset's convention and the ids stored in `faculty.unavailable_slot_ids`). Emit `{id, day, period,
  start, end}`.
- **rooms:** all `Room` rows → `{id: room.code, name, capacity, room_type}`.
- **faculty:** all `Faculty` rows → `{id: faculty.code, name, max_load_hours_per_week,
  max_consecutive_sessions, unavailable_slots: faculty.unavailable_slot_ids, preferred_slots: []}`.
- **courses:** `Course` rows (filtered to `branch_ids` when provided) →
  `{code, title, credits, category, theory_sessions_per_week: theory_per_week,
  practical_sessions_per_week: practical_per_week, tutorial_sessions_per_week: tutorial_per_week,
  is_heavy}`.
- **divisions:** `Division` rows (filtered to `branch_ids` when provided). For each division:
  - `course_codes`: the distinct course codes it has an `Allocation` for, in a stable order.
  - `faculty_by_course`: `{course_code: value}` where value is the single faculty **code** when the
    allocation has `faculty_id`, or a 2-list `[batch1_code, batch2_code]` when it has both batch FKs.
    (problem_from_dict converts the 2-list to a tuple.) Skip allocations whose referenced faculty
    row is missing (leave that course out of faculty_by_course — validate() will flag it).
  - `batches`: `[batch1_name or f"{name}1", batch2_name or f"{name}2"]`.
  - `program, semester, student_count` from the row; `id: division.name`.
- **days_per_week:** `max(day for slots) + 1` if any slots exist, else `5`.
- Also emit `protected_notes: []` and `special_sessions: []`.

Global rooms/faculty/slots are always included in full (they are shared across branches — design.md
§4.3); only courses and divisions are branch-filtered.

`readiness` calls `build_problem_dict` → `problem_from_dict` → returns `(problem, problem.validate())`.
If the DB is too empty to build a problem (no slots, or no divisions), it must NOT raise — return
`(None, ["no divisions defined", ...])` style issues (build the issue list defensively; e.g. check
for zero divisions / zero slots up front and return a clear message).

**Tests (use the `client`-style temp-DB setup, but call the functions directly with a `Session`):**
1. Seed the reference dataset (call `webapp.seed`'s logic or POST via TestClient, then open a
   Session), build the dict, `problem_from_dict` it: assert 3 divisions, 20 faculty, 40 time_slots,
   8 courses; assert `problem.validate()` is empty (reference data is complete).
2. `readiness` on an empty DB returns `(None, non-empty issues)` and does not raise.
3. `branch_ids` filter: create a second branch with its own division+course; `build_problem_dict(session,
   branch_ids=[first_branch_id])` yields only the first branch's divisions/courses, but still all
   global faculty/rooms/slots.
4. A division with a course allocation whose faculty is absent (or a course with no allocation) →
   `readiness` surfaces a non-empty issue (validate() catches the missing faculty).

## Task 2: generate job + runs router (background solve + poll + history)

**Files:** add a `TimetableRun` table to `webapp/models_db.py`; new `webapp/jobs.py`; new
`webapp/routers/runs.py`; wire the new router + a startup stale-sweep into `webapp/server.py`.
**Tests:** `tests/test_api_runs.py` (new).

**`TimetableRun` table** (design.md §4.2) — columns:
`id` (pk), `label` (str, default ""), `solver` (str), `time_limit` (float), `status` (str:
queued/running/done/failed), `created_at` (datetime, default utcnow), `problem_snapshot` (JSON),
`solution` (JSON, nullable), `grids` (JSON, nullable), `stage_reports` (JSON, nullable),
`hard` (int, nullable), `soft` (float, nullable), `error` (str, nullable). Use
`sa_column=Column(JSON)` for the JSON columns, matching how `Faculty.unavailable_slot_ids` is done.

**`webapp/jobs.py`:**
- `run_generation(run_id: int) -> None` — the background worker (sync; runs in Starlette's
  threadpool). Opens its OWN `Session(get_engine())`. Loads the run; sets `status="running"`,
  commit. Reconstructs the problem via `problem_from_dict(run.problem_snapshot)`. Dispatches:
  - `solver == "pipeline"` → `run_pipeline(problem, PipelineConfig(cpsat_time_limit_s=run.time_limit,
    ga_time_limit_s=min(run.time_limit, 30), mip_time_limit_s=min(run.time_limit, 60)))`; use
    `result.final` and serialize `result.reports` into `stage_reports` (list of dicts:
    name/status/wall_clock_s/hard/soft/best_hard/best_soft/improved — same shape the legacy
    /api/generate builds in server.py, reuse that shape).
  - `solver in SOLVERS` → `SOLVERS[solver]().solve(problem, time_limit_s=run.time_limit)`;
    `stage_reports = None`.
  - Compute `sc = score(solution, problem)`; `grids = solution_to_grids(solution, problem)`. Store
    `solution=solution_to_dict(solution)`, `grids`, `hard=sc.hard_violations`,
    `soft=sc.soft_cost`, `status="done"`. Wrap the whole solve in try/except → on exception set
    `status="failed"`, `error=str(exc)`, commit, and do not re-raise.
- `sweep_stale_running(session: Session) -> int` — set every row with `status=="running"` to
  `status="failed"`, `error="orphaned by restart"`; return the count. (Startup recovery — design.md
  §5.3 honest-limits.)
- `has_active_run(session: Session) -> bool` — True if any row has status queued or running.

**`webapp/routers/runs.py`** (`APIRouter(prefix="/api")`):
- `POST /api/generate` body `{solver: str = "cpsat", time_limit: float = 30, label: str = "", branch_ids: list[int] | None = None}`,
  and `background: BackgroundTasks`:
  1. `problem, issues = readiness(session, branch_ids)`; if `issues` or `problem is None` → `400
     {"detail": issues}`.
  2. If `has_active_run(session)` → `409 {"detail": "a run is already in progress"}`.
  3. Validate solver: must be `"pipeline"` or a key in `SOLVERS`; else `400`.
  4. Insert `TimetableRun(status="queued", solver=..., time_limit=..., label=...,
     problem_snapshot=problem_to_dict(problem))`; commit; refresh.
  5. `background.add_task(run_generation, run.id)`; return `{"run_id": run.id}`.
- `GET /api/runs` → list of `{id, label, solver, status, hard, soft, created_at}` (newest first).
- `GET /api/runs/{run_id}` → `{id, status, solver, label, hard, soft, grids, stage_reports, error,
  created_at}` (404 if missing).
- `GET /api/readiness` optional `?branch_ids=` (repeatable query param) → `{ready: bool, issues:
  list[str]}` from `readiness()`.

**Wire into `server.py`:** include `runs.router`; in the lifespan startup (after `init_db()`) open a
`Session(get_engine())` and call `sweep_stale_running(session)`.

**Tests (`tests/test_api_runs.py`, reuse the `client` fixture pattern from test_api_entities.py):**
1. Seed reference; `POST /api/generate {"solver": "greedy", "time_limit": 3}` → 200 with `run_id`.
   (Starlette's TestClient runs BackgroundTasks synchronously after the response, so the job has
   finished by the time the call returns.) Then `GET /api/runs/{run_id}` → `status=="done"`, `grids`
   is a non-empty dict/list, `hard` is an int. Do NOT assert hard==0.
2. Empty DB (no seed) → `POST /api/generate` → 400, and `detail` is a non-empty list.
3. `GET /api/readiness` on empty DB → `{ready: False, issues: [...]}`; after seeding → `ready: True`.
4. Single-flight: insert a `TimetableRun(status="running", ...)` directly via a Session, then
   `POST /api/generate` → 409.
5. Unknown solver (`"banana"`) after seeding → 400.
6. `GET /api/runs` after a generate lists the run.

## Task 3: export endpoints (xlsx / pdf from a stored run)

**File:** add two routes to `webapp/routers/runs.py`. **Tests:** add to `tests/test_api_runs.py`.

- `GET /api/runs/{run_id}/export.xlsx` and `GET /api/runs/{run_id}/export.pdf`:
  - 404 if the run is missing; 409 if `status != "done"` (nothing to export yet).
  - Reconstruct `problem = problem_from_dict(run.problem_snapshot)` and
    `solution = solution_from_dict(run.solution)`; call `export_xlsx`/`export_pdf` to a temp file
    under the OS temp dir (e.g. `tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")`), then
    return `fastapi.responses.FileResponse(path, filename="timetable_run_{id}.xlsx", media_type=...)`.
    (xlsx media type: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`; pdf:
    `application/pdf`.) Do NOT write into the repo tree.

**Tests:**
1. Seed + generate (greedy, time_limit 3) to a done run; `GET /api/runs/{id}/export.xlsx` → 200,
   `content-type` is the xlsx media type, body length > 0.
2. Same for `.pdf` → 200, `application/pdf`, body length > 0.
3. Export of a non-existent run id → 404. Export while a run is not done (insert a `queued` row) → 409.

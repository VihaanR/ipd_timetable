# CLAUDE.md — DJ Sanghvi Dynamic Timetable Platform (NEP 2020)

Project memory for Claude Code. Read this first when working in this repo.
The full design specification lives in `design.md` — this file is the working summary.

## 1. Project Overview

AI-assisted academic timetable **platform** for **SIH problem statement 25091** (Govt. of Jammu &
Kashmir, Higher Education Dept.): generate conflict-free, optimized timetables for NEP 2020
multidisciplinary programs (FYUP, B.Ed., M.Ed., ITEP) with flexible credit-based
Major/Minor/Skill/AEC/VAC course structures.

**Team** (DJSCE, Dept. of CSE — Data Science; project "NEP Aligned Timetable Optimization",
guide Prof. Aditi Raut): Danish Jain, Zane Nazareth, Vihaan Raut, Abhishekkumar Rathod. The team
independently prototyped three solver approaches and observed that **CP-SAT produces the
best-quality timetable but is the slowest (~5–10 min), while MIP and GA are faster but lower
quality.** This repo turns that observation into one comparable codebase:

- **MIP** (Mixed Integer Programming) — Zane
- **GA** (Genetic Algorithm, with a GA+ML hybrid fitness) — Abhish
- **CP-SAT** (OR-Tools constraint solver) — Danish

**Platform mission (current build, per `Goal.md` + `design.md`):** a web platform for
DJ Sanghvi (2026) where **all input is dynamic via the website** — faculty, subjects
(lectures/labs/tutorials), branches and divisions-per-branch, an uploaded academic calendar
(pdf/jpg/png), and a disruption window to re-adjust the timetable for sudden rain/holiday. No
data files are hand-edited in the repo; the platform's only writes are `webapp/data/platform.db`
and `webapp/uploads/`. Deferred (§14): auth, multi-tenancy, cloud infra, live AMS integration.

## 2. Architecture

```
timetable/           THE ENGINE — UI-agnostic, finished; only additive defaulted changes allowed
  models.py          Shared data model + expand_requirements() (the one contract all solvers use)
  scoring.py         Hard/soft constraint scorer — single source of truth for solution quality
  sample_data.py     Synthetic instance generator + load_reference_instance() (real DJSCE data)
  ml_predictors.py   Difficulty/Fatigue/InfraStress heuristic models feeding GA fitness
  pipeline.py        run_pipeline() (sequential hybrid) and run_ensemble() (cold 3-way)
  export.py          Solution -> .xlsx / .pdf
  io_json.py         problem_from_dict()/problem_to_dict() + Solution JSON round-trip
  view.py            solution_to_grids() — grid JSON consumed by the frontend renderer
  disruption.py      (P4) pin-to-baseline + blocked-slots re-solve for rain/holiday adjustments
  solvers/
    __init__.py      SOLVERS registry (single source for cli + webapp)
    base.py          SolverBase ABC: solve(problem, time_limit_s, warm_start) -> Solution
    candidates.py    Shared candidate pruning (MIP + CP-SAT; enforces blocked_slot_ids)
    greedy.py        Deterministic constructive heuristic (fast seed)
    mip.py           MIP solver (Zane)
    ga.py            GA + ML solver (Abhish)
    cpsat.py         CP-SAT solver (Danish)
webapp/              THE PLATFORM — imports timetable/, never the reverse
  server.py          FastAPI app assembly (port 8750)
  db.py              SQLModel engine/session (SQLite: webapp/data/platform.db)
  models_db.py       Tables: branch, division, faculty, course, allocation, room, slot_template,
                     term, calendar_upload, calendar_event, timetable_run, adjustment
  problem_builder.py DB rows -> dict -> io_json.problem_from_dict() -> ProblemInstance
  jobs.py            BackgroundTasks job runner (queued -> running -> done/failed)
  seed.py            POST /api/seed/reference: loads the DJSCE dataset as an editable starter branch
  extract_calendar.py Optional Claude-vision holiday extraction (hidden without ANTHROPIC_API_KEY)
  routers/           branches, divisions, faculty, courses, rooms, allocations, slots,
                     calendar, runs (generate/poll/export), adjust
  uploads/           Stored calendar files (pdf/jpg/png)
  static/            Vanilla-JS SPA: index.html, style.css, js/api.js, js/pages/*.js (ES modules,
                     hash-routed wizard; no framework, no build step)
cli.py               `python cli.py generate --solver ... --out ...` (dev/benchmark tool)
benchmarks/          compare_solvers.py — head-to-head harness, CSV to results/
data/reference/      djsce_cse_ds_sy_sem4.json — real dataset, now the SEED FIXTURE
references/          Source PDFs/docx (constraint specs, GA+ML architecture, research paper)
tests/               pytest suite (engine + API TestClient tests)
design.md            Full platform design spec (architecture, API, schema, roadmap)
```

> **Build status (updated 2026-07-21):** the tree above is the target state. **Done:** the engine,
> `cli.py`, benchmarks, tests, `timetable/disruption.py`; **P0** (`solvers/__init__.py` registry,
> port 8750, `io_json.problem_to_dict`/`save_problem`, `sync_group_id` fix); **P1** persistence +
> CRUD (`webapp/db.py`, `models_db.py`, `routers/` = branches+divisions/faculty/courses/rooms/
> allocations/slots + `_crud.py`, `seed.py`); **P2 backend** generate-from-DB (`problem_builder.py`,
> `jobs.py`, `routers/runs.py` = `POST /api/runs` create-run + poll + history + `GET /api/readiness`
> + xlsx/pdf export + `POST /api/runs/{id}/adjust` holiday/rain overlay); a **`/platform` SPA page**
> (`static/platform.html`+`platform.js`) driving seed→readiness→generate→view→export→adjust; and the
> **§16 scoring refinements** (per-day break variety + `day_span` compact-day term, in `scoring.py` +
> mirrored in `cpsat.py`). **Not yet built:** entity-CRUD frontend forms (faculty/subjects/etc. are
> API-only — no data-entry UI), `extract_calendar.py` + calendar router (P3), the DJSCE light theme
> (P5), and the remaining design-change decisions (disruption **full re-solve** — `/adjust` is still
> the minimal-change patcher that drops un-relocatable sessions — plus compare mode + undo; see
> design.md §7/§5.3). **Note:** DB-backed generate lives at `POST /api/runs` (not `/api/generate`,
> the legacy in-memory showcase at `/`). The DB-backed adjust is `POST /api/runs/{id}/adjust`.

**Engine data flow:** `ProblemInstance` → `expand_requirements()` → ordered
`list[SessionRequirement]` → *(solver)* → `Solution` (list of `Assignment`) →
`scoring.score()` / `view` / `export`.

**Platform data flow:** SPA → FastAPI routers → SQLite (source of truth) →
`problem_builder.build_problem_dict()` → `problem_from_dict()` → `validate()` (surfaced as the
UI readiness banner) → background job runs the pipeline → run row stores snapshot + solution +
grids → SPA polls `GET /api/runs/{id}` and renders.

Every solver consumes the identical `SessionRequirement` list and produces a `Solution` scored by
the identical `scoring.py`. That is what makes the three approaches directly comparable.

## 3. Data Model

### Engine (`timetable/models.py`)

- **`ProblemInstance`** — time_slots, rooms, faculty, courses, divisions, special_sessions;
  plus `blocked_slot_ids: frozenset[int]` (P4, default empty — slots nothing may occupy).
- **`Division`** — a class section (D1/D2/D3), with `batches` (e.g. `("D11","D12")`) and
  `faculty_by_course`. Faculty value is either a single id (theory/whole-division) or a
  `(batch1_faculty, batch2_faculty)` tuple for practicals run as two parallel sections. Resolve
  via `Division.faculty_for(course_code, batch_id)`.
- **`Course`** — code, credits, `category` (`CourseCategory` enum: Major/Minor/Skill/AEC/VAC/
  Open-Elective/…), and weekly `theory_sessions_per_week` / `practical_sessions_per_week` /
  `tutorial_sessions_per_week`. Practicals are 2-slot lab blocks; `is_heavy` feeds a soft
  constraint.
- **`SessionRequirement`** — the atomic schedulable unit. Key fields:
  - `duration_slots` (1 for lectures/tutorials, 2 for labs),
  - `batch_id` / `batch_group_id` — the two halves of one lab share a `batch_group_id` and must
    resolve to the **same time slot** but **different rooms** (hard constraint 16),
  - `sync_group_id` — sessions that must share the same slot across divisions (open electives),
  - `is_break` — must not land in the day's first/last period,
  - `fixed_time_slot_id` — protected/fixed blocks the solver may not move (also the disruption
    engine's pinning mechanism).
- **`Assignment`** = `(session_id, time_slot_id, room_id)`; **`Solution`** wraps the assignment
  list plus `solver_name`, `wall_clock_seconds`, `status`.

Faculty→course assignment is **fixed input**, not a solver decision (matches the real
timetables). Solvers decide only *when* (slot) and *where* (room).

### Platform (`webapp/models_db.py`) — see design.md §4 for full specs

`branch` (NEW concept) → `division` → `allocation` (division×course→faculty, incl. batch-pair
labs); global `faculty` and `room`; one institutional `slot_template` weekly grid; `term` +
`calendar_upload` + `calendar_event` (draft→confirmed); `timetable_run` (job record with
`problem_snapshot`/`solution`/`grids` JSON) and `adjustment` (dated disruption overlay).

**Rule: the DB is the source of truth; `ProblemInstance` is a derived, per-run snapshot.**
Solves always cover the whole institution (rooms/faculty are shared across branches); branch
filtering is a view-only concern.

## 4. Constraints Specification

Authoritative source: `references/TIMETABLE_CONSTRAINTS.pdf` and
`references/NEP_Optimized_Timetable_Constraints.pdf`.
Implemented once in `timetable/scoring.py`; solvers reference these by name, not by re-deriving.

**Hard constraints (must never be violated):**
1. 5 working days (Mon–Fri); Saturday reserved for IPD/Project work (not modeled as slots).
2. Lecture = 1 hour; Lab = 2 consecutive hours.
3. Exactly one 1-hour break per day, never first/last slot.
4. Division daily load 6–8 academic hours; daily hours should vary.
5. Weekly subject-credit requirements satisfied.
6. NEP credit structure present (Major/Minor/Skill/AEC/VAC).
7. A subject occurs at most once per day per division.
8. No faculty double-booking.
9. Faculty daily/weekly workload within caps; no excessive consecutive sessions.
10. Faculty teaching multiple divisions have no cross-division clashes.
11. A subject's lab and theory cannot clash.
12. Open electives: no cross-division clash; single slot/day; consistent slot across divisions.
13. Protected blocks (teaching practice/fieldwork/internship) are fixed.
14. Labs every working day; exactly 2 days have 2 labs, 3 days have 1 lab (soft-scored pattern).
15. Each lab occupies 2 consecutive slots.
16. Each division splits into 2 batches for labs; both run **simultaneously in different labs**.
17. A lab room / classroom hosts one group at a time.
18. Lectures only in classrooms, labs only in labs.
19. No all-theory day for a division (needs ≥1 lab/skill component).
20. *(P4)* Nothing may be scheduled into `ProblemInstance.blocked_slot_ids`.

**Disruption relaxation (P4):** `relaxed_days` exempts a disrupted day from the day-shaped hard
rules (daily load 6–8, labs-every-day, one-break-per-day) so a rain/holiday re-solve is not
scored infeasible. Implemented in `scoring.py` first, mirrored in the MIP/CP-SAT builders.

**Soft constraints (minimized in `soft_cost`):** avoid >2 heavy subjects in a row; daily
theory/lab mix; spread faculty workload; minimize idle gaps; avoid earliest+latest same day;
prefer labs before final two slots; balance difficulty; consistent elective slots; room-capacity
efficiency; equitable daily load; even lab distribution across the week.

`scoring.score()` returns `ScoreResult(hard_violations, soft_cost)`; `.key()` compares
lexicographically — **hard violations dominate, soft breaks ties.** A valid timetable has
`hard_violations == 0`.

> Note: the daily faculty-hour cap is set to 6h (not the PDFs' literal "1–3h"), because the real
> per-batch lab structure legitimately stacks a faculty's 2-hour sections; the NEP-optimized PDF
> itself softens this to "within institutional norms". See the comment in `scoring.py` / `mip.py`.

## 5. Reference Dataset (`data/reference/djsce_cse_ds_sy_sem4.json`)

Transcribed from the team's three real timetable photos: **DJSCE CSE-DS, SY Sem IV, divisions
D1/D2/D3, term Jan–May 2026.** Contains 8 subjects (DS, ML-I, SDS, WE, PBC, EFM, CMPM, OE),
~20 faculty (short codes + names), 4 classrooms (CR51/52/53/46) + 4 labs (L1–L4 = Data Analytics
/ ML / Computer Vision / HPC labs), and the batch split per division.

Per the team's own report, these timetables are used as a **structural/constraint reference and
benchmark, not ML training data** — the exact weekly grid from the photos is *not* reproduced
cell-for-cell; instead the underlying entities are captured so each solver generates its own
schedule against the same real-world constraints. Some faculty short-code→name mappings and a few
per-batch instructor pairings were only partially legible in the photos and are best-effort (see
the `_transcription_note` field in the JSON).

**Platform role:** this file is the **seed fixture**. `POST /api/seed/reference` imports it into
the DB as a starter branch, after which everything is edited in the browser. For engine/dev work
it is still loadable directly via `sample_data.load_reference_instance()`.

## 6. How to Run

```bash
pip install -r requirements.txt

# THE PLATFORM (primary path) — port 8750 (8000 is OS-reserved on this Windows box)
python -m uvicorn webapp.server:app --port 8750
# then open http://127.0.0.1:8750 — seed the reference branch, edit entities, generate, adjust

# Engine CLI (dev/benchmark tool)
python cli.py generate --solver pipeline --reference --out tt.xlsx --pdf tt.pdf
python cli.py generate --solver cpsat --scale medium --time-limit 300

# Head-to-head benchmark (reproduces the team's observation)
python benchmarks/compare_solvers.py --scale medium --cpsat-time-limit 300
python benchmarks/compare_solvers.py --reference --cpsat-time-limit 300

# Tests
python -m pytest tests/ -q
```

CLI `--solver` ∈ `{greedy, mip, ga, cpsat, pipeline, ensemble}`; `--scale` ∈ `{small, medium,
large}` (ignored with `--reference`). Exit code 0 only if the final solution has zero hard
violations. The **platform UI** deliberately exposes only `pipeline` / `cpsat` / `greedy`
(`mip`/`ga`/`ensemble` remain engine+CLI-only — benchmark narrative, not end-user choices).
Optional: set `ANTHROPIC_API_KEY` to enable AI calendar extraction (feature hides without it).

## 7. Solver: MIP (Zane) — `timetable/solvers/mip.py`

- **Backend:** OR-Tools `pywraplp` with CBC (no second dependency like PuLP).
- **Variables:** boolean `x[req, start_slot, room]`, pruned to structurally-valid combos only
  (faculty availability, room type/capacity, break exclusion, fixed slots) via `candidates.py`.
- **Hard constraints:** exactly-one assignment per session; no room/faculty/division(+batch)
  double-booking; subject-once-per-day; batch-pair same-slot (different room guaranteed by
  candidate pruning — each half draws from a disjoint half of the lab pool); cross-division OE
  sync; faculty daily/weekly caps; division daily load ∈ [6,8].
- **Objective:** minimize room-capacity waste + late-lab-slot penalty (the soft terms that
  linearize cleanly).
- **Warm start:** `SetHint` (CBC hint support is backend-limited — best-effort).
- **Known limitations:** gap-minimization and the lab-distribution-pattern soft constraint are
  **not** linearized here (they need auxiliary occupied-indicator vars). This is a documented
  reason CP-SAT scores better on quality.

## 8. Solver: GA (Abhish) — `timetable/solvers/ga.py` + `timetable/ml_predictors.py`

Hand-rolled (not DEAP) to keep scheduling-aware operators transparent. Implements the team's
GA+ML architecture (`references/GA_ML_*` docs):

- **Chromosome:** fixed-position gene list over `expand_requirements()` — gene `i` is the
  `(time_slot, room)` for requirement `i`, so crossover positions are stable.
- **Initialization:** staged — assign breaks → allocate lab blocks (consecutive + batch-pair
  sync) → theory/tutorials (credit-respecting) → repair. `warm_start` (one seed) or `warm_starts`
  (several, e.g. greedy + MIP in the hybrid pipeline) seed generation 0 with those solutions plus
  mutated copies of each, so the GA builds on every upstream solver.
- **Selection:** tournament (k=3). **Crossover:** division-wise (primary) + uniform gene
  crossover (secondary). **Mutation:** reassign-gene, swap-slots, shift-lab-block, + repair pass.
- **Fitness** (from the architecture doc, verbatim weights):
  `10000*hard + 100*normalized_soft + 40*difficulty + 60*fatigue + 30*infra`.
  `hard`/`soft` come from `scoring.py` (keeps GA comparable to MIP/CP-SAT); the three `*`
  penalties come from `ml_predictors.py`.
- **ML predictors:** `DifficultyModel`, `FatigueModel`, `InfraStressModel` are **heuristic,
  rule-based stand-ins** — no real historical academic-performance dataset exists yet to train
  regressors. Each exposes `predict(features: dict) -> float in [0,1]` so a trained sklearn model
  can be dropped in later without touching the GA loop.
- **Params:** `POP_SIZE=80, GENERATIONS=150, CROSSOVER_RATE=0.8, MUTATION_RATE=0.15, ELITISM=2`.
- **Known limitations:** stochastic, no optimality guarantee, sensitive to seed quality.

## 9. Solver: CP-SAT (Danish) — `timetable/solvers/cpsat.py`

- **Backend:** `ortools.sat.python.cp_model`.
- **Variables/constraints:** boolean `x[req, slot, room]` with `AddExactlyOne` per session; the
  same hard constraints as MIP, expressed with CP-SAT's richer vocabulary (no-overlap-style
  reasoning over variable-duration lab vs lecture sessions in one formulation). Batch-pair sync
  via linear equalities on slot-indicator sums; different-room via disjoint candidate pools.
- **Objective — why it wins on quality:** in addition to capacity/late-lab penalties, CP-SAT adds
  **native per-(division,day) gap-minimization** via reified "occupied at period p" booleans and
  before/after indicators — a soft term MIP/GA cannot express cleanly. This is the concrete
  mechanism behind the team's "CP-SAT better output" observation.
- **Runtime:** default `time_limit_s=300`; the team observed 5–10 min to fully converge on
  realistic instances. `num_search_workers` uses available cores.
- **Warm start:** `model.AddHint(var, value)` — reliable, unlike CBC hints.

## 10. Orchestration Pipeline (`timetable/pipeline.py`)

**`run_pipeline` — cooperative 4-stage hybrid (recommended).** ALL four algorithms take part and
each hands its work to the next as a warm start; they are *not* run independently and then
compared. This is the genuine hybrid, not a beauty contest:

1. **Greedy** builds a fast feasible seed (sub-second).
2. **MIP** is warm-started from the greedy seed (`SetHint`) and solves the exact 0/1 model —
   nailing the hard constraints + linearizable soft terms.
3. **GA** initial population is seeded from **both** the greedy seed **and** the MIP solution
   (`GASolver.solve(..., warm_starts=[greedy, mip])`), then explores the soft-constraint / ML
   fitness space MIP can't express.
4. **CP-SAT** is warm-started (`AddHint`) from the best candidate so far and does the final
   polish with its richer objective (native gap-minimization).

`scoring.better()` keeps the running best monotonic (safety net): if a stage's solver returns
something worse (e.g. a timeout), the pipeline carries the earlier, better solution forward as the
next stage's warm start rather than losing it. `PipelineResult.reports` records each stage's own
output *and* the running-best after it, so you can watch quality climb (this is what the CLI table
and the web UI's stage track display). Each stage is individually switchable via
`PipelineConfig.run_mip/run_ga/run_cpsat`.

**`run_ensemble` — the contrast baseline.** Greedy, MIP, GA run independently/cold (no
cross-seeding; CP-SAT is excluded from this cold comparison per owner decision, 2026-07-21), picks
the best. Use it to
show how much the hybrid handoff buys over "just run them and compare." Engine/CLI-only — not in
the platform UI.

## 11. Web Platform (`webapp/`) — see design.md §3–§8 for the full spec

- **Run:** `python -m uvicorn webapp.server:app --port 8750`. Port **8750** everywhere (8000 is
  OS-reserved on this box).
- **API:** entity CRUD (`/api/branches`, `/api/faculty`, `/api/courses`, `/api/rooms`,
  `/api/allocations`, `/api/slots`), `POST /api/seed/reference`, calendar
  (`/api/calendar/upload` multipart → `/api/calendar/extract/{id}` optional →
  `/api/calendar/events` review/confirm), generation (`POST /api/generate` → `{run_id}` →
  poll `GET /api/runs/{id}`; 409 if a run is in flight), disruption (`POST /api/adjust`),
  exports (`GET /api/runs/{id}/export.xlsx|.pdf`, same for adjustments).
- **Job pattern:** FastAPI `BackgroundTasks` + run rows, statuses `queued → running →
  done/failed`. Tasks die with the process (startup sweep fails stale `running` rows); no
  cancellation; single-flight. Cloud upgrade = worker queue behind the same API contract.
- **Frontend:** vanilla-JS SPA, ES modules, hash-routed left-nav wizard: Branches & Divisions →
  Faculty → Subjects → Allocations → Rooms & Slots → Calendar → Generate & View → Adjust.
  Readiness banner = server-side `ProblemInstance.validate()` output.
- **DJ Sanghvi palette** (light theme, no logo) via `static/style.css` `:root` variables:
  `--accent:#003877` (navy — topbar/buttons), `--accent-2:#f26d21` (orange — highlights,
  moved-session marker), `--bg:#f6f7f8`, `--panel:#fff`, `--panel-2:#f5f5f5`, `--line:#dedede`,
  `--text:#000`, `--muted:#5f5f5f`; session colors as navy/orange/grey tints.

## 12. Disruption Flow (P4 — IMPLEMENTED) — `timetable/disruption.py`, design.md §7

**Status: built and tested** (engine + `POST /api/adjust` + frontend Adjust panel; 9 golden tests
in `tests/test_disruption.py`, full suite green). Engine additions (all additive/defaulted, so
every prior test/caller is unaffected): `ProblemInstance.blocked_slot_ids` (slots nothing may
occupy), `relaxed_days` (days exempt from the day-shaped hard rules — daily load 6–8, labs-every-
day, one-break-per-day, period-0-occupied), and `pinned_slots` (`session_id -> slot`, applied in
`expand_requirements` via the existing `fixed_time_slot_id` mechanism). Enforced once in
`scoring.py` (single source of truth) and mirrored in `candidates.py`/`greedy.py`/`ga.py` (blocked
slots) and the MIP/CP-SAT builders (both blocked slots and relaxed-day constraint-skipping).

`disruption.replan(problem, baseline, affected_slot_ids, relaxed_days)` does a **minimal-change**
overlay, built **directly** (not via the exact solvers — `AddExactlyOne` can't drop a rained-out
session): every UNDISRUPTED day is copied verbatim (identical slot AND room); the disrupted day's
surviving sessions keep their placement; blocked-window sessions are relocated into that day's free
non-blocked slots if they fit, else **dropped** for that occurrence (you can't overload another
day past its 6–8h cap). Returns an `AdjustmentResult` with a `moved_sessions` diff, `dropped_
session_ids`, and `conflict_violations`/`is_valid` (drops are expected; a valid adjustment has zero
*conflict* violations among placed sessions). **The master run is never mutated.** `affected_slots_
for_day(problem, day, from_period)` builds the window: `from_period=None` = whole-day holiday,
`from_period=N` = rain from period N. Confirmed calendar holidays would pre-fill this flow; they do
not reshape weekly generation (the engine has no date axis — design.md §12).

## 13. Benchmark Results Log

`benchmarks/compare_solvers.py` runs each solver cold + the pipeline and writes CSV to
`benchmarks/results/`. Representative run on **synthetic small** (2 divisions, 76 sessions;
short time limits) — reproduces the team's ranking (lower soft = better; hard must be 0):

| Solver           | wall(s) | hard | soft  |
|------------------|--------:|-----:|------:|
| Greedy (seed)    |    0.0  |  17  | 198.8 |
| MIP (Zane)       |   10.4  |   0  | 178.2 |
| GA (Abhish)      |    8.2  |   8  | 188.2 |
| CP-SAT (Danish)  |   15.6  |   0  | 155.2 |
| Pipeline (final) |   23.7  |   0  | **137.2** |

On the **DJSCE reference dataset**, CP-SAT reaches `hard=0, soft≈112` in ~13–20s. Takeaways match
the team: CP-SAT beats MIP on quality; the sequential pipeline beats every individual solver.
Re-run with larger `--cpsat-time-limit` (e.g. 300) on `--scale medium`/`--reference` to reproduce
the fuller 5–10 min CP-SAT convergence. Append notable runs to this table over time.

## 14. Literature Notes

Team's literature review (`references/NEP_Timetable_report.docx`) surveyed heuristic GA (ISICO
2023), GA+GNN, CNN scheduling, GA+Tabu Search, multi-objective DRL, ML-based and DL scheduling
frameworks, and DRL scheduling. Conclusion: GNN/DRL approaches need large training data and heavy
compute the team doesn't have, so **classical MIP / GA / CP-SAT were chosen** as data-light,
explainable, and tractable — hence this repo's three-solver design.

Academic grounding: **Davison, Kheiri & Zografos (2025),** *Journal of Scheduling* 28:195–215,
"Modelling and solving the university course timetabling problem with hybrid teaching
considerations" (`references/s10951-024-00817-w.pdf`) — a multi-objective binary-programming UCTTP
model with lexicographic solution ordering. This informs (a) the MIP binary-variable formulation
and (b) the lexicographic `(hard_violations, soft_cost)` comparison in `scoring.py`. Its
hybrid-teaching / student-level module-enrollment features are out of scope here (this project is
division/batch-based, not per-student).

## 15. Conventions

- Dataclasses with `slots=True`; frozen for immutable value objects; `Enum`s for categoricals.
- **Always pass an explicit RNG seed** (`random.Random(seed)`); never use global `random` state.
- Add a new constraint in ONE place first — `scoring.py` — then enforce it in each solver.
  Keep `scoring.py` the single source of truth for what "good" means.
- Solvers must never raise on an infeasible/partial result; return a `Solution` with an
  appropriate `status` and let `scoring.py` penalize it. The pipeline depends on this.
- Time limits are arguments, not hardcoded; keep tests fast with small limits.
- **The engine stays UI-agnostic: `webapp` imports `timetable`, never the reverse.**
- **Engine changes are additive-only with defaulted fields** (e.g. `blocked_slot_ids` defaults
  empty) so all existing tests and callers keep working.
- **The DB is the source of truth; `ProblemInstance` is a derived per-run snapshot** stored on
  the run row for reproducibility.
- **No files created by code** except `webapp/data/platform.db` and `webapp/uploads/`.
- Machine-extracted calendar data is never trusted: drafts require human confirmation in the UI.
- Engine tests must stay green after every phase; run `python -m pytest tests/ -q`.

## 16. Roadmap

Phased build (full acceptance criteria in design.md §10): **P0** groundwork (solver registry,
port 8750, `problem_to_dict`, `sync_group_id` bug fix — *the `sync_group_id` bug is now fixed*) →
**P1** SQLite persistence + entity CRUD + seed → **P2** generate jobs + grids + readiness banner →
**P3** calendar upload + review (with optional AI extraction) → **P4 disruption engine — DONE**
(`timetable/disruption.py` + `POST /api/adjust` + frontend Adjust panel; §12) → **P5** DJ Sanghvi
light theme + polish. Note P4 was built ahead of P1–P3: it runs against an in-memory baseline in
the current stateless showcase; when P1's persistence lands, the same `replan()` will consume a
stored run snapshot instead.

**Still explicitly deferred:** authentication, multi-institution multi-tenancy, cloud
infrastructure (Postgres/worker queue/object storage — sketched in design.md §14), live AMS
integration.

## 17. Research Track (`research/`, design.md §15)

A track separate from the platform build: replacing the heuristic ML predictors with models
trained on a small primary survey dataset (§15.1); an equal-compute-budget ablation proving the
hybrid pipeline beats single solvers under fair conditions, including a warm-start on/off
isolation (§15.2); an epsilon-constraint Pareto analysis exposing soft-objective trade-offs
`soft_cost`'s scalarization currently hides (§15.3); CP-SAT infeasibility diagnosis via
`SufficientAssumptionsForInfeasibility()` (§15.4); and external validation against ITC-2007
benchmark instances (§15.5). Lives in a new `research/` directory, decoupled from `timetable/`'s
additive-only discipline except the predictor swap, which stays interface-compatible.

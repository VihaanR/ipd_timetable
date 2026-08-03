# NEP 2020 Dynamic Timetable Platform (SIH 25091)

A web platform that generates conflict-free, credit-aware timetables for NEP 2020
multidisciplinary programs (FYUP, B.Ed., M.Ed., ITEP), and re-plans them when a day is disrupted
by rain or a holiday. Built for **DJ Sanghvi College of Engineering**.

Everything is entered through the browser — faculty, subjects, branches, divisions, rooms, and the
weekly slot grid. No data files are hand-edited.

Under the hood, three optimization approaches solve one shared, constraint-accurate problem model:

- **CP-SAT** — OR-Tools constraint solver (best quality; the platform default)
- **MIP** — Mixed Integer Programming (OR-Tools CBC)
- **GA** — Genetic Algorithm with a GA+ML hybrid fitness

plus a **cooperative hybrid pipeline** — Greedy → MIP → GA → CP-SAT, where each algorithm
warm-starts the next — that beats any single solver.

---

## 1. Install

Requires **Python 3.11+**.

```bash
pip install -r requirements.txt
```

## 2. Run the platform (the primary path)

```bash
python -m uvicorn webapp.server:app --port 8750
```

Then open **<http://127.0.0.1:8750/platform>**.

> **Use port 8750.** Port 8000 is OS-reserved on the project's dev machine.
>
> **Open `/platform`, not `/`.** The bare `/` is a legacy in-memory showcase kept for reference —
> it does not use the database and is not the platform.

### First run: seed, then generate

A fresh install has an **empty database**, so the Generate button is disabled and the readiness
banner reads *"no divisions defined, no time slots defined"*. Load the starter data first:

1. **① Starter data** → click **Load reference data**. This imports the real DJSCE CSE-DS
   SY Sem-IV dataset (1 branch, 3 divisions, 20 faculty, 8 subjects, 8 rooms, a 5×8 slot grid) as
   a normal, fully editable branch. It refuses to run twice unless you force a re-seed.
2. **② Readiness** → should now show **Ready to generate**. This banner is the engine's own
   `validate()` output, so it names exactly what is missing (e.g. *"D2 has no faculty allocated
   for PBC"*).
3. **③ Generate** → pick a solver and time budget, click Generate. The run happens in the
   background; the page polls until it finishes.
4. **④ Result** → the color-coded weekly grid per division, plus **Export .xlsx / .pdf**.

On the reference dataset, CP-SAT with a 60 s budget reaches **0 hard violations** in about
**20 seconds**.

### The other screens

| Screen | URL | What it's for |
|---|---|---|
| **Platform** | `/platform` | Seed → readiness → generate → view → export → compare → adjust. The main workflow. |
| **Dashboard** | `/dashboard` | Data entry for every entity: Faculty, Branches, Divisions, Subjects, Allocations, Rooms, Weekly slot grid. Use this to edit the seeded data or build a term from scratch. |
| **Legacy showcase** | `/` | The original stateless demo (in-memory, no database). Kept for the pipeline-stage visualization; not part of the platform. |

### Compare solvers (⑤)

Tick two or more of CP-SAT / Hybrid pipeline / Greedy and click **Run comparison** to run them on
the same problem snapshot. You get a comparison table (hard violations, soft cost, wall clock) with
the winner badged, and a grid tab per solver. Each row is an **independent** run — picking the
pipeline runs its own internal Greedy→MIP→GA→CP-SAT chain, unrelated to the standalone CP-SAT row
beside it.

Measured on the reference dataset (20 s budget): greedy `hard=21, soft=202.8` in under a second;
CP-SAT `hard=0, soft=126.1` in 21 s.

### Holiday / rain adjustment (⑥)

Pick a day and a scope — whole day (holiday) or from period N (rain) — and apply. The blocked
window is closed off and the **whole week is re-solved** around it, warm-started from the current
timetable, so a rained-out session is rescheduled elsewhere in the week rather than dropped. Moved
sessions are highlighted; **Restore original** reverts the view.

If a disruption removes more capacity than the remaining days can absorb — a whole-day holiday
removes ~20% of the week — the panel lists **every session it could not place**, by course,
division, and faculty, so you can rearrange them by hand. Ticking extra days under *"Also relax
these days"* eases the per-day load rules and lowers the violation count, but it cannot conjure
extra slots, so a full-day holiday will still report unplaced sessions. That is deliberate: the
platform reports the shortfall instead of silently dropping classes.

**The stored run is never modified** — an adjustment is an overlay on top of it.

## 3. Where state lives / how to reset

The platform writes to exactly two places:

| Path | Contents |
|---|---|
| `webapp/data/platform.db` | SQLite database — every entity, run, and result |
| `webapp/uploads/` | Uploaded academic-calendar files (pdf/jpg/png) |

To start over, stop the server and delete `webapp/data/platform.db`; it is recreated empty on the
next start. (There are no migrations by design — this is a single-admin, pre-cloud build.)

Optional environment variables:

| Variable | Effect |
|---|---|
| `TIMETABLE_DB_PATH` | Use a different SQLite file (e.g. a scratch DB for experiments) |
| `TIMETABLE_UPLOADS_PATH` | Use a different upload directory |
| `ANTHROPIC_API_KEY` | Enables optional Claude-vision holiday extraction from an uploaded calendar. Without it, the feature is simply hidden and manual entry works unchanged. |

## 4. Engine CLI (development / benchmarking)

The CLI runs the solver engine directly against the reference dataset or synthetic instances — no
database, no browser. It is a dev and benchmark tool, not the product.

```bash
# Generate with the cooperative hybrid and export both formats
python cli.py generate --solver pipeline --reference --out timetable.xlsx --pdf timetable.pdf

# A single solver with an explicit time budget
python cli.py generate --solver cpsat --reference --time-limit 60

# Synthetic instances instead of the real dataset
python cli.py generate --solver cpsat --scale medium --time-limit 300

# Head-to-head benchmark -> CSV in benchmarks/results/
python benchmarks/compare_solvers.py --reference --cpsat-time-limit 300
```

`--solver` ∈ `{greedy, mip, ga, cpsat, pipeline, ensemble}` ·
`--scale` ∈ `{small, medium, large}` (ignored with `--reference`).

**Exit code is 0 only if the final timetable has zero hard violations**, so the CLI works in
scripts and CI. (`greedy` alone on the reference dataset exits 1 — it is a fast seed, not a
finished timetable.)

Note the platform UI intentionally exposes only CP-SAT / pipeline / greedy. `mip`, `ga`, and
`ensemble` remain CLI-only: they exist for the benchmark narrative rather than as end-user choices.

## 5. Tests

```bash
python -m pytest tests/ -q
```

106 tests covering the engine, the disruption re-planner, and the API. The suite takes about five
minutes — the timetable tests run real solvers.

## 6. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Generate is disabled; banner says *"no divisions defined"* | Empty database — load the reference dataset in ① Starter data first. |
| `POST /api/runs` returns 400 with a list of issues | Same thing: the problem isn't complete yet. The list names each gap. |
| `POST /api/runs` returns 409 | A run is already in progress. Solves are single-flight because rooms and faculty are shared institution-wide. |
| Seeding returns 409 *"data already present"* | Intentional guard. Re-seed with `POST /api/seed/reference?force=true` (this wipes existing entities). |
| Port 8000 fails to bind | It's OS-reserved on the dev box. Use 8750. |
| A run is stuck in `running` after a server restart | Background tasks die with the process; stale rows are swept to `failed` on startup. Just run it again. |

## 7. Documentation

- **[PIPELINE_WALKTHROUGH.md](PIPELINE_WALKTHROUGH.md)** — plain-language explanation of how the
  hybrid pipeline works, with a worked example and benchmark results. Start here if you're
  presenting this project.
- **[design.md](design.md)** — the full platform design spec: architecture, API reference, database
  schema, disruption engine, phased roadmap, and honest gaps.
- **[CLAUDE.md](CLAUDE.md)** — technical working reference: data model, constraint specification,
  per-solver design, and benchmark log.

Source material (constraint specs, GA+ML architecture docs, research paper) lives in `references/`.

## 8. Project layout

| Path | Purpose |
|------|---------|
| `timetable/` | **The engine** — UI-agnostic, importable on its own |
| `timetable/models.py` | Shared data model + `expand_requirements()` |
| `timetable/scoring.py` | Hard/soft constraint scorer (single source of truth for quality) |
| `timetable/solvers/` | greedy, mip (Zane), ga (Abhish), cpsat (Danish) |
| `timetable/pipeline.py` | Cooperative 4-stage hybrid + ensemble orchestration |
| `timetable/disruption.py` | Rain/holiday re-planner |
| `timetable/export.py` | `.xlsx` / `.pdf` export |
| `webapp/` | **The platform** — FastAPI app; imports `timetable`, never the reverse |
| `webapp/routers/` | Entity CRUD, calendar, runs, adjust endpoints |
| `webapp/problem_builder.py` | Database rows → `ProblemInstance` |
| `webapp/static/` | Vanilla-JS frontend (no build step) |
| `data/reference/` | Real DJSCE D1/D2/D3 dataset — the seed fixture |
| `benchmarks/` | Solver comparison harness |
| `tests/` | pytest suite |

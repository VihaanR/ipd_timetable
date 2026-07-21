# NEP 2020 Timetable Generation System (SIH 25091)

AI-assisted academic timetable generator for NEP 2020 multidisciplinary programs (FYUP, B.Ed.,
M.Ed., ITEP). Implements and compares three optimization approaches against one shared,
constraint-accurate problem model:

- **MIP** — Mixed Integer Programming (OR-Tools CBC)
- **GA** — Genetic Algorithm with a GA+ML hybrid fitness
- **CP-SAT** — OR-Tools constraint solver (best quality, slowest)

plus a **cooperative hybrid pipeline** — Greedy → MIP → GA → CP-SAT, where each algorithm
warm-starts the next (MIP hints from the greedy seed; GA's population seeded from greedy + MIP;
CP-SAT polishes the GA best) — that beats any single solver.

## Quick start

```bash
pip install -r requirements.txt

# Generate a timetable and export it (shows the hybrid stage-by-stage breakdown)
python cli.py generate --solver pipeline --reference --out timetable.xlsx --pdf timetable.pdf

# Compare all solvers head-to-head
python benchmarks/compare_solvers.py --reference --cpsat-time-limit 300

# Launch the web showcase (interactive timetable viewer)
python -m uvicorn webapp.server:app --port 8750   # then open http://127.0.0.1:8750

# Run the tests
python -m pytest tests/ -q
```

`--solver` ∈ `{greedy, mip, ga, cpsat, pipeline, ensemble}`;
`--scale` ∈ `{small, medium, large}` for synthetic data, or `--reference` for the real
DJSCE CSE-DS D1/D2/D3 dataset.

## Documentation

- **[PIPELINE_WALKTHROUGH.md](PIPELINE_WALKTHROUGH.md)** — plain-language explanation of how the
  hybrid pipeline works, with a worked example and benchmark results. Start here if you're
  presenting this project (e.g. to a mentor/reviewer).
- **[CLAUDE.md](CLAUDE.md)** — full technical reference: architecture, data model, constraint
  specification, per-solver design, benchmark log, and roadmap.

Source material (constraint specs, GA+ML architecture docs, research paper) lives in
`references/`.

## Project layout

| Path | Purpose |
|------|---------|
| `timetable/models.py` | Shared data model |
| `timetable/scoring.py` | Hard/soft constraint scorer (single source of truth) |
| `timetable/solvers/` | greedy, mip (Zane), ga (Abhish), cpsat (Danish) |
| `timetable/pipeline.py` | Cooperative 4-stage hybrid + ensemble orchestration |
| `webapp/` | FastAPI backend + HTML/JS timetable showcase |
| `data/reference/` | Real DJSCE D1/D2/D3 dataset |
| `benchmarks/` | Solver comparison harness |
| `tests/` | pytest suite |

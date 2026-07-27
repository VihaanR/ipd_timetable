"""FastAPI backend for the timetable showcase.

Serves the single-page frontend and these endpoints:
  GET  /api/problem            -> metadata about the current dataset (divisions, courses, faculty)
  POST /api/generate           -> run a solver / the hybrid pipeline and return per-division grids
                                  plus the hybrid stage-by-stage breakdown and quality scores
  POST /api/adjust             -> minimal-change disruption re-plan (rain/holiday) over the last
                                  generated timetable; returns adjusted grids + a moved/dropped diff

The last generated timetable is cached in memory (single-admin showcase; real persistence is a
later roadmap phase per design.md §4/§10). /api/adjust re-plans against that cached baseline.

Run:  python -m uvicorn webapp.server:app --port 8750
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from timetable.sample_data import generate_sample_instance, load_reference_instance
from timetable.scoring import score
from timetable.view import solution_to_grids
from timetable.pipeline import run_pipeline, run_ensemble, PipelineConfig
from timetable.solvers import SOLVERS as SINGLE_SOLVERS  # single-source registry (design.md §9)
from timetable.disruption import replan, affected_slots_for_day

from contextlib import asynccontextmanager

from sqlmodel import Session

from webapp.db import init_db, get_engine
from webapp.jobs import sweep_stale_running
from webapp.routers import branches, faculty, courses, rooms, allocations, slots, runs, calendar
from webapp import seed

STATIC_DIR = Path(__file__).resolve().parent / "static"

# in-memory cache of the most recent generation, so /api/adjust has a baseline to re-plan over
_LAST = {"problem": None, "solution": None, "label": None}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create SQLite tables if the platform DB is new (design.md §4.1)
    calendar.get_upload_dir()  # ensure webapp/uploads/ exists (design.md §6, CLAUDE.md §15)
    with Session(get_engine()) as session:
        sweep_stale_running(session)  # fail any "running" row orphaned by a previous restart
    yield


app = FastAPI(title="NEP Timetable Platform", lifespan=lifespan)

# platform entity CRUD (P1) + generate job/runs (P2). The legacy showcase endpoints below stay
# until the SPA pages land.
for _router in (branches.router, faculty.router, courses.router, rooms.router,
                allocations.router, slots.router, seed.router, runs.router, calendar.router):
    app.include_router(_router)


def _load(dataset: str, scale: str):
    if dataset == "reference":
        return load_reference_instance(), "DJSCE CSE-DS SY Sem IV (D1/D2/D3)"
    return generate_sample_instance(scale), f"Synthetic {scale}"


class GenerateRequest(BaseModel):
    dataset: str = "reference"       # "reference" | "synthetic"
    scale: str = "small"             # used when dataset == "synthetic"
    solver: str = "pipeline"         # pipeline | ensemble | greedy | mip | ga | cpsat
    time_limit: float = 30.0


@app.get("/api/problem")
def api_problem(dataset: str = "reference", scale: str = "small"):
    problem, label = _load(dataset, scale)
    return {
        "label": label,
        "divisions": [
            {"id": d.id, "program": d.program.value, "semester": d.semester,
             "student_count": d.student_count, "batches": list(d.batch_pair())}
            for d in problem.divisions
        ],
        "courses": [
            {"code": c.code, "title": c.title, "credits": c.credits, "category": c.category.value}
            for c in problem.courses
        ],
        "faculty": [{"id": f.id, "name": f.name} for f in problem.faculty],
        "rooms": [{"id": r.id, "name": r.name, "type": r.room_type} for r in problem.rooms],
    }


@app.post("/api/generate")
def api_generate(req: GenerateRequest):
    problem, label = _load(req.dataset, req.scale)

    stage_reports = []
    notes = []
    if req.solver in ("pipeline", "ensemble"):
        config = PipelineConfig(
            cpsat_time_limit_s=req.time_limit,
            ga_time_limit_s=min(req.time_limit, 30),
            mip_time_limit_s=min(req.time_limit, 60),
        )
        result = (run_pipeline if req.solver == "pipeline" else run_ensemble)(problem, config)
        solution = result.final
        notes = result.notes
        for rep in result.reports:
            stage_reports.append({
                "name": rep.name, "status": rep.solver_status, "wall_clock_s": round(rep.wall_clock_s, 1),
                "hard": rep.hard_violations, "soft": round(rep.soft_cost, 1),
                "best_hard": rep.running_best_hard, "best_soft": round(rep.running_best_soft, 1),
                "improved": rep.improved,
            })
        total_wall = result.total_wall_clock_s
    else:
        solver = SINGLE_SOLVERS[req.solver]()
        solution = solver.solve(problem, time_limit_s=req.time_limit)
        total_wall = solution.wall_clock_seconds

    sc = score(solution, problem)
    grids = solution_to_grids(solution, problem)
    _LAST.update(problem=problem, solution=solution, label=label)  # baseline for /api/adjust
    return {
        "label": label,
        "solver": req.solver,
        "final_solver": solution.solver_name,
        "status": solution.status,
        "hard_violations": sc.hard_violations,
        "soft_cost": round(sc.soft_cost, 1),
        "wall_clock_s": round(total_wall, 1),
        "stages": stage_reports,
        "notes": notes,
        "grids": grids,
    }


class AdjustRequest(BaseModel):
    day: int                         # 0=Mon .. 4=Fri — the disrupted weekday
    from_period: int | None = None   # None => whole-day holiday; N => rain from period N onward


@app.post("/api/adjust")
def api_adjust(req: AdjustRequest):
    problem = _LAST["problem"]
    baseline = _LAST["solution"]
    if problem is None or baseline is None:
        raise HTTPException(status_code=409, detail="Generate a timetable first, then adjust it.")
    if not (0 <= req.day < problem.days_per_week):
        raise HTTPException(status_code=400, detail=f"day must be 0..{problem.days_per_week - 1}")

    affected = affected_slots_for_day(problem, req.day, from_period=req.from_period)
    result = replan(problem, baseline, affected)

    # render the adjusted timetable; annotate each moved/dropped session for UI highlighting
    grids = solution_to_grids(result.solution, problem)
    slots_by_id = {t.id: t for t in problem.time_slots}
    moved = [{
        "session_id": m.session_id,
        "from_slot": m.from_slot_id, "to_slot": m.to_slot_id,
        "from_room": m.from_room_id, "to_room": m.to_room_id,
        "dropped": m.to_slot_id is None,
    } for m in result.moved_sessions]

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    scope = "whole day (holiday)" if req.from_period is None else f"from period {req.from_period} (rain)"
    return {
        "disrupted_day": day_names[req.day] if req.day < len(day_names) else str(req.day),
        "scope": scope,
        "affected_slot_ids": sorted(affected),
        "moved_count": len([m for m in moved if not m["dropped"]]),
        "dropped_count": len(result.dropped_session_ids),
        "dropped_session_ids": result.dropped_session_ids,
        "conflict_violations": result.conflict_violations,
        "is_valid": result.is_valid,
        "soft_cost": round(result.soft_cost, 1),
        "notes": result.notes,
        "moved": moved,
        "grids": grids,
    }


@app.get("/")
def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/platform")
def platform_page():
    return FileResponse(str(STATIC_DIR / "platform.html"))


@app.get("/dashboard")
def dashboard_page():
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8750)  # 8000 is OS-reserved on the dev box
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

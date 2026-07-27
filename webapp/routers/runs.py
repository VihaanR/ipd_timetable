"""Generate job + poll + history (design.md §5.3, CLAUDE.md §11).

`POST /api/runs` builds and validates the `ProblemInstance` snapshot synchronously (the readiness
gate is cheap - no solving happens yet), stores it as `problem_snapshot`, then hands the actual
solve off to `BackgroundTasks` (`webapp.jobs.run_generation`) so the request returns immediately
with a `run_id` the SPA polls via `GET /api/runs/{id}`. Single-flight: solves cover the whole
institution's shared rooms/faculty, so only one run may be queued/running at a time.
"""
from __future__ import annotations

import os
import tempfile

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session, select
from starlette.background import BackgroundTask

from timetable.disruption import affected_slots_for_day, replan
from timetable.export import export_pdf, export_xlsx
from timetable.io_json import problem_from_dict, problem_to_dict, solution_from_dict
from timetable.pipeline import PipelineConfig, run_pipeline
from timetable.scoring import score
from timetable.solvers import SOLVERS
from timetable.view import solution_to_grids
from webapp.db import get_session
from webapp.jobs import has_active_run, run_generation
from webapp.models_db import TimetableRun
from webapp.problem_builder import readiness

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MEDIA_TYPE = "application/pdf"

router = APIRouter(prefix="/api", tags=["runs"])


class GenerateRequest(BaseModel):
    solver: str = "cpsat"
    time_limit: float = 30.0
    label: str = ""
    branch_ids: list[int] | None = None


class CompareRequest(BaseModel):
    time_limit: float = 30.0
    label: str = ""
    branch_ids: list[int] | None = None
    solvers: list[str] | None = None


@router.post("/runs")
def generate(
    body: GenerateRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    # Cheapest check first: the single-flight guard is one indexed row lookup, so it must reject
    # a busy platform (409) before we pay for the expensive readiness build below (which assembles
    # and validates the whole-institution ProblemInstance).
    if has_active_run(session):
        raise HTTPException(status_code=409, detail="a run is already in progress")

    if body.solver != "pipeline" and body.solver not in SOLVERS:
        raise HTTPException(status_code=400, detail=f"unknown solver {body.solver!r}")

    problem, issues = readiness(session, body.branch_ids)
    if problem is None or issues:
        raise HTTPException(status_code=400, detail=issues)

    run = TimetableRun(
        status="queued",
        solver=body.solver,
        time_limit=body.time_limit,
        label=body.label,
        problem_snapshot=problem_to_dict(problem),
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    background.add_task(run_generation, run.id)
    return {"run_id": run.id}


def _run_solver_compare(problem, solver_name: str, time_limit: float) -> dict:
    if solver_name == "pipeline":
        config = PipelineConfig(
            cpsat_time_limit_s=time_limit,
            ga_time_limit_s=min(time_limit, 30),
            mip_time_limit_s=min(time_limit, 60),
        )
        result = run_pipeline(problem, config)
        solution = result.final
        stage_reports = [{
            "name": rep.name,
            "status": rep.solver_status,
            "wall_clock_s": round(rep.wall_clock_s, 1),
            "hard": rep.hard_violations,
            "soft": round(rep.soft_cost, 1),
            "best_hard": rep.running_best_hard,
            "best_soft": round(rep.running_best_soft, 1),
            "improved": rep.improved,
        } for rep in result.reports]
        wall_clock_s = result.total_wall_clock_s
        notes = result.notes
    else:
        solver = SOLVERS[solver_name]()
        solution = solver.solve(problem, time_limit_s=time_limit)
        stage_reports = None
        wall_clock_s = solution.wall_clock_seconds
        notes = []

    sc = score(solution, problem)
    return {
        "solver": solver_name,
        "final_solver": solution.solver_name,
        "status": solution.status,
        "hard_violations": sc.hard_violations,
        "soft_cost": round(sc.soft_cost, 1),
        "wall_clock_s": round(wall_clock_s, 1),
        "stage_reports": stage_reports,
        "notes": notes,
        "grids": solution_to_grids(solution, problem),
    }


@router.post("/compare")
def compare(body: CompareRequest, session: Session = Depends(get_session)):
    problem, issues = readiness(session, body.branch_ids)
    if problem is None or issues:
        raise HTTPException(status_code=400, detail=issues)

    solvers = body.solvers or ["pipeline", "cpsat", "greedy"]
    unique_solvers: list[str] = []
    for solver_name in solvers:
        if solver_name not in unique_solvers:
            unique_solvers.append(solver_name)

    if len(unique_solvers) == 0:
        raise HTTPException(status_code=400, detail="compare mode needs at least one solver")

    invalid = [solver_name for solver_name in unique_solvers if solver_name != "pipeline" and solver_name not in SOLVERS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"unknown solver(s): {', '.join(invalid)}")

    results = [_run_solver_compare(problem, solver_name, body.time_limit) for solver_name in unique_solvers]
    best_index = min(range(len(results)), key=lambda i: (results[i]["hard_violations"], results[i]["soft_cost"]))
    return {
        "label": body.label,
        "solvers": unique_solvers,
        "best_index": best_index,
        "best_solver": results[best_index]["solver"],
        "results": results,
    }


@router.get("/runs")
def list_runs(session: Session = Depends(get_session)):
    runs = session.exec(select(TimetableRun).order_by(TimetableRun.created_at.desc())).all()
    return [
        {
            "id": r.id,
            "label": r.label,
            "solver": r.solver,
            "status": r.status,
            "hard": r.hard,
            "soft": r.soft,
            "created_at": r.created_at,
        }
        for r in runs
    ]


@router.get("/runs/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return {
        "id": run.id,
        "status": run.status,
        "solver": run.solver,
        "label": run.label,
        "hard": run.hard,
        "soft": run.soft,
        "wall_clock": run.wall_clock,
        "grids": run.grids,
        "stage_reports": run.stage_reports,
        "error": run.error,
        "created_at": run.created_at,
    }


def _get_done_run(run_id: int, session: Session) -> TimetableRun:
    """Shared lookup for the export routes: 404 if the run doesn't exist, 409 if it hasn't
    finished solving yet (nothing to export)."""
    run = session.get(TimetableRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    if run.status != "done":
        raise HTTPException(status_code=409, detail=f"run {run_id} is not done (status={run.status!r})")
    return run


def _export_response(run: TimetableRun, export_fn, suffix: str, media_type: str) -> FileResponse:
    """Shared body for the xlsx/pdf export routes. Reconstructs the problem+solution from the
    already-fetched `run`'s stored snapshot, writes them to a fresh temp file via `export_fn`, and
    streams it back. If `export_fn` raises, the just-created (still-empty) temp file is unlinked
    before re-raising — otherwise the FileResponse (and its unlink-after-streaming background task)
    is never constructed, and the empty temp file leaks on every failed export."""
    problem = problem_from_dict(run.problem_snapshot)
    solution = solution_from_dict(run.solution)

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.close()
    try:
        export_fn(solution, problem, tmp.name)
    except Exception:
        os.unlink(tmp.name)
        raise
    return FileResponse(
        tmp.name,
        filename=f"timetable_run_{run.id}{suffix}",
        media_type=media_type,
        background=BackgroundTask(os.unlink, tmp.name),  # delete the temp file after streaming
    )


@router.get("/runs/{run_id}/export.xlsx")
def export_run_xlsx(run_id: int, session: Session = Depends(get_session)):
    run = _get_done_run(run_id, session)
    return _export_response(run, export_xlsx, ".xlsx", XLSX_MEDIA_TYPE)


@router.get("/runs/{run_id}/export.pdf")
def export_run_pdf(run_id: int, session: Session = Depends(get_session)):
    run = _get_done_run(run_id, session)
    return _export_response(run, export_pdf, ".pdf", PDF_MEDIA_TYPE)


class AdjustRunRequest(BaseModel):
    day: int                          # 0=Mon .. 4=Fri — the disrupted weekday
    from_period: int | None = None    # None => whole-day holiday; N => rain from period N onward
    reason: str = ""                  # "holiday" | "rain" | free text (annotation only)


@router.post("/runs/{run_id}/adjust")
def adjust_run(run_id: int, body: AdjustRunRequest, session: Session = Depends(get_session)):
    """Disruption re-plan against a stored run (design.md §7). Loads the run's problem snapshot +
    baseline solution, blocks the disrupted window, and returns a minimal-change overlay: unaffected
    days are kept verbatim, displaced sessions are relocated within the disrupted day where they fit,
    and any that don't are reported as dropped. The stored run is never mutated (stateless overlay)."""
    run = _get_done_run(run_id, session)
    problem = problem_from_dict(run.problem_snapshot)
    baseline = solution_from_dict(run.solution)
    if not (0 <= body.day < problem.days_per_week):
        raise HTTPException(status_code=400, detail=f"day must be 0..{problem.days_per_week - 1}")

    affected = affected_slots_for_day(problem, body.day, from_period=body.from_period)
    result = replan(problem, baseline, affected)

    grids = solution_to_grids(result.solution, problem)
    moved = [
        {
            "session_id": m.session_id,
            "from_slot": m.from_slot_id, "to_slot": m.to_slot_id,
            "from_room": m.from_room_id, "to_room": m.to_room_id,
            "dropped": m.to_slot_id is None,
        }
        for m in result.moved_sessions
    ]
    scope = "whole day (holiday)" if body.from_period is None else f"from period {body.from_period} (rain)"
    return {
        "run_id": run_id,
        "disrupted_day": DAY_NAMES[body.day] if body.day < len(DAY_NAMES) else str(body.day),
        "scope": scope,
        "reason": body.reason,
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


@router.get("/readiness")
def get_readiness(
    branch_ids: list[int] | None = Query(default=None),
    session: Session = Depends(get_session),
):
    problem, issues = readiness(session, branch_ids)
    return {"ready": problem is not None and not issues, "issues": issues}

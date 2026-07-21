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

from timetable.export import export_pdf, export_xlsx
from timetable.io_json import problem_from_dict, problem_to_dict, solution_from_dict
from timetable.solvers import SOLVERS
from webapp.db import get_session
from webapp.jobs import has_active_run, run_generation
from webapp.models_db import TimetableRun
from webapp.problem_builder import readiness

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MEDIA_TYPE = "application/pdf"

router = APIRouter(prefix="/api", tags=["runs"])


class GenerateRequest(BaseModel):
    solver: str = "cpsat"
    time_limit: float = 30.0
    label: str = ""
    branch_ids: list[int] | None = None


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


@router.get("/readiness")
def get_readiness(
    branch_ids: list[int] | None = Query(default=None),
    session: Session = Depends(get_session),
):
    problem, issues = readiness(session, branch_ids)
    return {"ready": problem is not None and not issues, "issues": issues}

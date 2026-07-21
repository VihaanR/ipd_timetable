"""Background job runner for `POST /api/runs` (design.md §5.3, CLAUDE.md §11).

`run_generation` is handed to Starlette's `BackgroundTasks`, which executes it in a threadpool
*after* the request has already returned its response — so it cannot reuse the request's DB
session (that session is closed by then) and opens its own. It must never raise: a solver
exception is caught and recorded on the run row (`status="failed"`, `error=...`) so the SPA can
show it, rather than being lost to a background-thread traceback that nothing observes.
"""
from __future__ import annotations

from sqlmodel import Session, select

from timetable.io_json import problem_from_dict, solution_to_dict
from timetable.pipeline import PipelineConfig, run_pipeline
from timetable.scoring import score
from timetable.solvers import SOLVERS
from timetable.view import solution_to_grids
from webapp.db import get_engine
from webapp.models_db import TimetableRun


def _stage_reports_from(result) -> list[dict]:
    """Same shape the legacy `/api/generate` handler in server.py builds — reused here so pipeline
    reports render consistently whether they came from the old showcase endpoint or this job."""
    return [
        {
            "name": rep.name,
            "status": rep.solver_status,
            "wall_clock_s": round(rep.wall_clock_s, 1),
            "hard": rep.hard_violations,
            "soft": round(rep.soft_cost, 1),
            "best_hard": rep.running_best_hard,
            "best_soft": round(rep.running_best_soft, 1),
            "improved": rep.improved,
        }
        for rep in result.reports
    ]


def run_generation(run_id: int) -> None:
    """The background worker: load the run, solve, store the result. Opens its OWN session."""
    with Session(get_engine()) as session:
        run = session.get(TimetableRun, run_id)
        if run is None:
            return  # nothing to do; the row vanished (shouldn't happen in practice)

        run.status = "running"
        session.add(run)
        session.commit()

        try:
            problem = problem_from_dict(run.problem_snapshot)
            stage_reports = None

            if run.solver == "pipeline":
                config = PipelineConfig(
                    cpsat_time_limit_s=run.time_limit,
                    ga_time_limit_s=min(run.time_limit, 30),
                    mip_time_limit_s=min(run.time_limit, 60),
                )
                result = run_pipeline(problem, config)
                solution = result.final
                stage_reports = _stage_reports_from(result)
            else:
                solution = SOLVERS[run.solver]().solve(problem, time_limit_s=run.time_limit)

            sc = score(solution, problem)
            grids = solution_to_grids(solution, problem)

            run.solution = solution_to_dict(solution)
            run.grids = grids
            run.stage_reports = stage_reports
            run.hard = sc.hard_violations
            run.soft = sc.soft_cost
            run.status = "done"
            session.add(run)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
            run.status = "failed"
            run.error = str(exc)
            session.add(run)
            session.commit()


def sweep_stale_running(session: Session) -> int:
    """Startup recovery (design.md §5.3 honest-limits): `BackgroundTasks` jobs live only in this
    process's threadpool, so a `running` OR `queued` row found at startup means the process died
    either mid-solve or between `POST /api/runs` inserting the `queued` row and the background
    task flipping it to `running` — nothing in a fresh process will ever pick up a leftover
    `queued` row, so it is unambiguously an orphan too. Leaving it alone would keep `has_active_run`
    true forever, permanently 409-ing every future `POST /api/runs` with no recovery path short of
    editing the DB by hand. Fail both statuses so the SPA doesn't poll a run that will never finish.
    Returns the count swept."""
    stale = session.exec(
        select(TimetableRun).where(TimetableRun.status.in_(["running", "queued"]))
    ).all()
    for run in stale:
        run.status = "failed"
        run.error = "orphaned by restart"
        session.add(run)
    if stale:
        session.commit()
    return len(stale)


def has_active_run(session: Session) -> bool:
    """True if a run is queued or running — the single-flight guard for `POST /api/runs`."""
    active = session.exec(
        select(TimetableRun).where(TimetableRun.status.in_(["queued", "running"]))
    ).first()
    return active is not None

"""Turn a Solution into a JSON-friendly per-division weekly grid, shared by the web app and any
other presentation layer."""
from __future__ import annotations

from timetable.models import ProblemInstance, Solution, expand_requirements

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def solution_to_grids(solution: Solution, problem: ProblemInstance) -> dict:
    """Returns a dict describing the timetable for rendering:
    {
      "periods": [{"period", "start", "end"}...],
      "days": ["Monday", ...],
      "divisions": [
        {"id", "cells": { "<day>_<period>": [ {course, type, faculty, faculty_name, room,
                                                room_name, batch, is_break, division_id} ... ] }}
      ]
    }
    A cell holds a list because simultaneous lab batch-pairs occupy the same (day, period).
    `division_id` is included per-entry (not just at the division-grid level) so a per-faculty pivot
    that merges entries across multiple divisions can still say which division each session belongs
    to (webapp/routers/faculty.py's `/api/faculty/me/timetable`)."""
    requirements = {r.id: r for r in expand_requirements(problem)}
    slots_by_id = {t.id: t for t in problem.time_slots}
    rooms_by_id = problem.room_by_id()
    faculty_by_id = problem.faculty_by_id()

    periods = sorted({t.period for t in problem.time_slots})
    period_meta = {}
    for t in problem.time_slots:
        period_meta.setdefault(t.period, {"period": t.period, "start": t.start, "end": t.end})

    grids = {d.id: {} for d in problem.divisions}
    for a in solution.assignments:
        req = requirements.get(a.session_id)
        if req is None:
            continue
        ts = slots_by_id.get(a.time_slot_id)
        if ts is None:
            continue
        room = rooms_by_id.get(a.room_id)
        fac = faculty_by_id.get(req.faculty_id) if req.faculty_id else None
        entry = {
            "session_id": req.id,
            "course": req.course_code,
            "type": req.session_type.value,
            "faculty": req.faculty_id or "",
            "faculty_name": fac.name if fac else "",
            "room": a.room_id if room else "",
            "room_name": room.name if room else "",
            "batch": req.batch_id or "",
            "is_break": req.is_break,
            "division_id": req.division_id,
        }
        grid = grids.setdefault(req.division_id, {})
        for k in range(req.duration_slots):
            grid.setdefault(f"{ts.day}_{ts.period + k}", []).append(entry)

    return {
        "periods": [period_meta[p] for p in periods],
        "days": DAY_NAMES[:problem.days_per_week],
        "divisions": [{"id": d.id, "cells": grids.get(d.id, {})} for d in problem.divisions],
    }

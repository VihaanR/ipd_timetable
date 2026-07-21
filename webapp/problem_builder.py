"""DB rows -> `ProblemInstance` snapshot (design.md §4.2, CLAUDE.md "Platform data flow").

`build_problem_dict` assembles exactly the JSON shape `timetable.io_json.problem_from_dict`
parses (see `data/reference/djsce_cse_ds_sy_sem4.json` for a concrete instance of that shape).
`readiness` wraps that with `problem.validate()` so routers can surface a plain-English readiness
banner without ever raising on a partially-populated DB.

Global entities (faculty, rooms, slot template) are shared across the whole institution and are
therefore always emitted in full; only `courses` and `divisions` are filtered by `branch_ids`
(CLAUDE.md §3 — "Solves always cover the whole institution... branch filtering is a view-only
concern"). This module only reads; it never writes (CLAUDE.md §15 — no files/rows created by code
outside `platform.db` / `uploads/`).
"""
from __future__ import annotations

from sqlmodel import Session, select

from timetable.io_json import problem_from_dict
from timetable.models import ProblemInstance
from webapp.models_db import Allocation, Course, Division, Faculty, Room, SlotTemplate


def _faculty_by_course_value(alloc: Allocation, faculty_code_by_id: dict[int, str]):
    """Resolve one allocation's faculty payload for `Division.faculty_by_course`.

    Returns a single faculty code, a `[batch1_code, batch2_code]` 2-list, or `None` when no
    faculty is resolvable (missing FK entirely, or one referenced faculty row no longer exists) —
    callers skip the course on `None` so `ProblemInstance.validate()` flags the gap instead of this
    module papering over it.
    """
    if alloc.faculty_id is not None:
        return faculty_code_by_id.get(alloc.faculty_id)
    if alloc.batch1_faculty_id is not None and alloc.batch2_faculty_id is not None:
        b1 = faculty_code_by_id.get(alloc.batch1_faculty_id)
        b2 = faculty_code_by_id.get(alloc.batch2_faculty_id)
        if b1 is not None and b2 is not None:
            return [b1, b2]
        return None
    return None


def build_problem_dict(session: Session, branch_ids: list[int] | None = None) -> dict:
    """Assemble the `problem_from_dict`-shaped dict from the current DB state.

    `branch_ids=None` means "no filter" (used for the whole-institution solve); an explicit list
    filters `courses`/`divisions` to those branches while `faculty`/`rooms`/`time_slots` are always
    global (see module docstring).
    """
    slot_rows = sorted(session.exec(select(SlotTemplate)).all(), key=lambda s: (s.day, s.period))
    time_slots = [
        {"id": idx, "day": s.day, "period": s.period, "start": s.start, "end": s.end}
        for idx, s in enumerate(slot_rows)
    ]
    days_per_week = (max(s.day for s in slot_rows) + 1) if slot_rows else 5

    rooms = [
        {"id": r.code, "name": r.name, "capacity": r.capacity, "room_type": r.room_type}
        for r in session.exec(select(Room)).all()
    ]

    faculty_rows = session.exec(select(Faculty)).all()
    faculty = [
        {
            "id": f.code,
            "name": f.name,
            "max_load_hours_per_week": f.max_load_hours_per_week,
            "max_consecutive_sessions": f.max_consecutive_sessions,
            "unavailable_slots": list(f.unavailable_slot_ids),
            "preferred_slots": [],
        }
        for f in faculty_rows
    ]
    faculty_code_by_id = {f.id: f.code for f in faculty_rows}

    # Course/division lookups: course codes are resolved against ALL courses (an allocation's FK
    # is trustworthy regardless of which branch is being filtered into the emitted `courses` list),
    # but the emitted `courses` list itself is branch-filtered.
    all_course_rows = session.exec(select(Course)).all()
    course_code_by_id = {c.id: c.code for c in all_course_rows}

    course_stmt = select(Course)
    if branch_ids is not None:
        course_stmt = course_stmt.where(Course.branch_id.in_(branch_ids))
    courses = [
        {
            "code": c.code,
            "title": c.title,
            "credits": c.credits,
            "category": c.category,
            "theory_sessions_per_week": c.theory_per_week,
            "practical_sessions_per_week": c.practical_per_week,
            "tutorial_sessions_per_week": c.tutorial_per_week,
            "is_heavy": c.is_heavy,
        }
        for c in session.exec(course_stmt).all()
    ]

    division_stmt = select(Division)
    if branch_ids is not None:
        division_stmt = division_stmt.where(Division.branch_id.in_(branch_ids))
    division_rows = session.exec(division_stmt).all()

    divisions = []
    for d in division_rows:
        alloc_rows = session.exec(
            select(Allocation).where(Allocation.division_id == d.id).order_by(Allocation.id)
        ).all()

        course_codes: list[str] = []
        faculty_by_course: dict[str, object] = {}
        for alloc in alloc_rows:
            code = course_code_by_id.get(alloc.course_id)
            if code is None:
                continue
            if code not in course_codes:
                course_codes.append(code)
            value = _faculty_by_course_value(alloc, faculty_code_by_id)
            if value is not None:
                faculty_by_course[code] = value

        divisions.append({
            "id": d.name,
            "program": d.program,
            "semester": d.semester,
            "student_count": d.student_count,
            "course_codes": course_codes,
            "faculty_by_course": faculty_by_course,
            "batches": [d.batch1_name or f"{d.name}1", d.batch2_name or f"{d.name}2"],
        })

    return {
        "time_slots": time_slots,
        "rooms": rooms,
        "faculty": faculty,
        "courses": courses,
        "divisions": divisions,
        "days_per_week": days_per_week,
        "protected_notes": [],
        "special_sessions": [],
    }


def readiness(
    session: Session, branch_ids: list[int] | None = None
) -> tuple[ProblemInstance | None, list[str]]:
    """Build a `ProblemInstance` and validate it, without ever raising on a sparse DB.

    Returns `(None, issues)` when the DB is too empty to even attempt a build (no divisions, or no
    time slots) — checked defensively up front rather than relying on downstream code to fail
    gracefully. Otherwise returns `(problem, problem.validate())`; an empty issue list means the
    instance is ready to solve.
    """
    issues: list[str] = []

    division_stmt = select(Division)
    if branch_ids is not None:
        division_stmt = division_stmt.where(Division.branch_id.in_(branch_ids))
    division_rows = session.exec(division_stmt).all()
    if len(division_rows) == 0:
        issues.append("no divisions defined")

    slot_count = len(session.exec(select(SlotTemplate)).all())
    if slot_count == 0:
        issues.append("no time slots defined")

    if issues:
        return None, issues

    # Guard against whole-institution id collisions: `build_problem_dict` emits the bare
    # `Division.name`/`Course.code` as the engine id (unique only *within* a branch — see the
    # module docstring), so the default whole-institution solve (branch_ids=None) would silently
    # merge two branches' divisions/courses in the engine's id-keyed lookups
    # (`division_by_id()`/`course_by_code()`) if their names/codes collide. Detected here, against
    # the same branch_ids-filtered rows that will actually be emitted, and BEFORE `problem_from_dict`
    # so a collision surfaces as a readiness issue instead of corrupting the snapshot silently.
    division_name_counts: dict[str, int] = {}
    for d in division_rows:
        division_name_counts[d.name] = division_name_counts.get(d.name, 0) + 1
    for name in sorted(division_name_counts):
        if division_name_counts[name] > 1:
            issues.append(
                f"division name '{name}' is used by more than one included branch — rename so "
                "the whole-institution solve has unique division ids"
            )

    course_stmt = select(Course)
    if branch_ids is not None:
        course_stmt = course_stmt.where(Course.branch_id.in_(branch_ids))
    course_rows = session.exec(course_stmt).all()
    course_code_counts: dict[str, int] = {}
    for c in course_rows:
        course_code_counts[c.code] = course_code_counts.get(c.code, 0) + 1
    for code in sorted(course_code_counts):
        if course_code_counts[code] > 1:
            issues.append(
                f"course code '{code}' is used by more than one included branch — rename so "
                "the whole-institution solve has unique course ids"
            )

    if issues:
        return None, issues

    data = build_problem_dict(session, branch_ids=branch_ids)
    problem = problem_from_dict(data)
    return problem, problem.validate()

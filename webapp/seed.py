"""POST /api/seed/reference — import the real DJSCE dataset as an editable starter branch.

Turns `data/reference/djsce_cse_ds_sy_sem4.json` (the transcribed real timetable) into DB rows:
one branch, its divisions, the global faculty/room/slot grid, the branch's courses, and the
division×course→faculty allocations (single faculty, or a batch1/batch2 pair for split-batch labs).
After seeding, everything is editable through the normal entity endpoints — this is one-click
onboarding, not a hardcoded dataset (design.md §5.1, satisfies R7).

Refuses to run when data already exists unless `?force=true`, which wipes every entity table first.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from webapp.db import get_session
from webapp.models_db import (
    Allocation, Branch, Course, Division, Faculty, Room, SlotTemplate,
)

router = APIRouter(prefix="/api", tags=["seed"])

REFERENCE_JSON = Path(__file__).resolve().parent.parent / "data" / "reference" / "djsce_cse_ds_sy_sem4.json"

_ENTITY_TABLES = [Allocation, Course, Division, Room, Faculty, SlotTemplate, Branch]


def _wipe(session: Session) -> None:
    for model in _ENTITY_TABLES:  # children before parents
        for row in session.exec(select(model)).all():
            session.delete(row)
    session.flush()


@router.post("/seed/reference")
def seed_reference(force: bool = False, session: Session = Depends(get_session)):
    existing = session.exec(select(Branch)).first()
    if existing is not None and not force:
        raise HTTPException(
            status_code=409,
            detail="data already present; pass ?force=true to wipe and re-seed",
        )
    if not REFERENCE_JSON.exists():
        raise HTTPException(status_code=500, detail=f"reference dataset missing at {REFERENCE_JSON}")

    data = json.loads(REFERENCE_JSON.read_text(encoding="utf-8"))
    if force:
        _wipe(session)

    # --- branch ---
    branch = Branch(
        code="CSE-DS",
        name=data.get("institution", "DJ Sanghvi CSE-DS"),
        semester_label=data.get("class_term", ""),
    )
    session.add(branch)
    session.flush()  # assign branch.id

    # --- global slot template ---
    for t in data["time_slots"]:
        session.add(SlotTemplate(day=t["day"], period=t["period"], start=t["start"], end=t["end"]))

    # --- global rooms ---
    for r in data["rooms"]:
        session.add(Room(code=r["id"], name=r["name"], capacity=r["capacity"], room_type=r["room_type"]))

    # --- global faculty (engine id = short code) ---
    faculty_by_code: dict[str, Faculty] = {}
    for f in data["faculty"]:
        fac = Faculty(
            code=f["id"],
            name=f["name"],
            unavailable_slot_ids=list(f.get("unavailable_slots", [])),
        )
        session.add(fac)
        faculty_by_code[f["id"]] = fac

    # --- branch courses ---
    course_by_code: dict[str, Course] = {}
    for c in data["courses"]:
        course = Course(
            branch_id=branch.id,
            code=c["code"],
            title=c["title"],
            credits=c["credits"],
            category=c["category"],
            theory_per_week=c.get("theory_sessions_per_week", 0),
            practical_per_week=c.get("practical_sessions_per_week", 0),
            tutorial_per_week=c.get("tutorial_sessions_per_week", 0),
            is_heavy=c.get("is_heavy", False),
        )
        session.add(course)
        course_by_code[c["code"]] = course

    session.flush()  # assign faculty.id / course.id for the FK wiring below

    # --- divisions + allocations ---
    for d in data["divisions"]:
        batches = d.get("batches") or [f"{d['id']}1", f"{d['id']}2"]
        division = Division(
            branch_id=branch.id,
            name=d["id"],
            program=d.get("program", "FYUP"),
            semester=d["semester"],
            student_count=d["student_count"],
            batch1_name=batches[0],
            batch2_name=batches[1],
        )
        session.add(division)
        session.flush()  # assign division.id

        for course_code, value in d["faculty_by_course"].items():
            course = course_by_code.get(course_code)
            if course is None:
                continue
            alloc = Allocation(division_id=division.id, course_id=course.id)
            if isinstance(value, list):  # (batch1, batch2) split-batch practical
                b1 = faculty_by_code.get(value[0])
                b2 = faculty_by_code.get(value[1])
                alloc.batch1_faculty_id = b1.id if b1 else None
                alloc.batch2_faculty_id = b2.id if b2 else None
            else:                        # single whole-division faculty
                fac = faculty_by_code.get(value)
                alloc.faculty_id = fac.id if fac else None
            session.add(alloc)

    session.commit()

    return {
        "branch_id": branch.id,
        "branch_code": branch.code,
        "divisions": len(data["divisions"]),
        "faculty": len(data["faculty"]),
        "courses": len(data["courses"]),
        "rooms": len(data["rooms"]),
        "slots": len(data["time_slots"]),
        "forced": force,
    }

"""Student CRUD (global roster - Auth's individual-account requirement, design.md §11).
Mirrors `webapp/routers/faculty.py`'s exact pattern. Every read route uses
`response_model=StudentPublic` so `password_hash` never serializes (mirrors `FacultyPublic`)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from fastapi import HTTPException

from webapp.auth import hash_password, require_faculty, require_student
from webapp.db import get_session
from webapp.models_db import Division, Student, StudentCreate, StudentPublic, StudentUpdate, TimetableRun
from webapp.routers._crud import get_or_404, unique_or_400

router = APIRouter(prefix="/api", tags=["students"])


@router.get("/students/me/timetable")
def my_timetable(session: Session = Depends(get_session), principal: dict = Depends(require_student)):
    """Read-only: the caller's own division's most recent generated timetable. `division_id` on
    the Student row is the DB int PK - resolve it to the Division row's `.name` (the engine-side
    key that actually appears in a run's `grids`), per the type-precision note in the session plan."""
    student = get_or_404(session, Student, principal["id"])
    division = get_or_404(session, Division, student.division_id)

    run = session.exec(
        select(TimetableRun)
        .where(TimetableRun.status == "done")
        .order_by(TimetableRun.created_at.desc())
    ).first()
    if run is None or not run.grids:
        raise HTTPException(status_code=404, detail="no generated timetable available yet")

    entry = next((d for d in run.grids["divisions"] if d["id"] == division.name), None)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"division {division.name!r} not in this run")

    return {
        "run_id": run.id,
        "division": division.name,
        "days": run.grids["days"],
        "periods": run.grids["periods"],
        "cells": entry["cells"],
    }


@router.get("/students", response_model=list[StudentPublic])
def list_students(session: Session = Depends(get_session), _=Depends(require_faculty)):
    return session.exec(select(Student)).all()


@router.post("/students", status_code=201, response_model=StudentPublic)
def create_student(body: StudentCreate, session: Session = Depends(get_session),
                   _=Depends(require_faculty)):
    unique_or_400(session, Student, "roll_no", body.roll_no)
    unique_or_400(session, Student, "email", body.email)
    data = body.model_dump(exclude={"password"})
    student = Student(**data, password_hash=hash_password(body.password))
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.get("/students/{student_id}", response_model=StudentPublic)
def get_student(student_id: int, session: Session = Depends(get_session),
                _=Depends(require_faculty)):
    return get_or_404(session, Student, student_id)


@router.put("/students/{student_id}", response_model=StudentPublic)
def update_student(student_id: int, body: StudentUpdate, session: Session = Depends(get_session),
                   _=Depends(require_faculty)):
    student = get_or_404(session, Student, student_id)
    if body.roll_no is not None:
        unique_or_400(session, Student, "roll_no", body.roll_no, exclude_id=student_id)
    if body.email is not None:
        unique_or_400(session, Student, "email", body.email, exclude_id=student_id)
    for key, value in body.model_dump(exclude_unset=True, exclude={"password"}).items():
        setattr(student, key, value)
    if body.password:
        student.password_hash = hash_password(body.password)
    session.add(student)
    session.commit()
    session.refresh(student)
    return student


@router.delete("/students/{student_id}")
def delete_student(student_id: int, session: Session = Depends(get_session),
                   _=Depends(require_faculty)):
    student = get_or_404(session, Student, student_id)
    session.delete(student)
    session.commit()
    return {"deleted": student_id}

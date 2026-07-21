"""Faculty CRUD (global — shared across branches). design.md §5.1."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from webapp.db import get_session
from webapp.models_db import Allocation, Faculty, FacultyCreate, FacultyUpdate
from webapp.routers._crud import apply_update, get_or_404, unique_or_400

router = APIRouter(prefix="/api", tags=["faculty"])


@router.get("/faculty")
def list_faculty(session: Session = Depends(get_session)):
    return session.exec(select(Faculty)).all()


@router.post("/faculty", status_code=201)
def create_faculty(body: FacultyCreate, session: Session = Depends(get_session)):
    unique_or_400(session, Faculty, "code", body.code)
    faculty = Faculty.model_validate(body)
    session.add(faculty)
    session.commit()
    session.refresh(faculty)
    return faculty


@router.get("/faculty/{faculty_id}")
def get_faculty(faculty_id: int, session: Session = Depends(get_session)):
    return get_or_404(session, Faculty, faculty_id)


@router.put("/faculty/{faculty_id}")
def update_faculty(faculty_id: int, body: FacultyUpdate, session: Session = Depends(get_session)):
    faculty = get_or_404(session, Faculty, faculty_id)
    if body.code is not None:
        unique_or_400(session, Faculty, "code", body.code, exclude_id=faculty_id)
    apply_update(faculty, body)
    session.add(faculty)
    session.commit()
    session.refresh(faculty)
    return faculty


@router.delete("/faculty/{faculty_id}")
def delete_faculty(faculty_id: int, session: Session = Depends(get_session)):
    faculty = get_or_404(session, Faculty, faculty_id)
    # blocked while any allocation references this faculty (any of the three FK slots)
    for alloc in session.exec(select(Allocation)).all():
        if faculty_id in (alloc.faculty_id, alloc.batch1_faculty_id, alloc.batch2_faculty_id):
            raise HTTPException(
                status_code=400,
                detail=f"faculty {faculty_id} is referenced by an allocation; reassign it first",
            )
    session.delete(faculty)
    session.commit()
    return {"deleted": faculty_id}

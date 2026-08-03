"""Course CRUD (branch-scoped). design.md §5.1."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from webapp.auth import require_faculty
from webapp.db import get_session
from webapp.models_db import Allocation, Branch, Course, CourseCreate, CourseUpdate
from webapp.routers._crud import apply_update, get_or_404, validate_category

router = APIRouter(prefix="/api", tags=["courses"])


def _validate_session_counts(theory: int, practical: int, tutorial: int) -> None:
    if (theory or 0) + (practical or 0) + (tutorial or 0) <= 0:
        raise HTTPException(
            status_code=400,
            detail="course must have at least one of theory/practical/tutorial per week > 0",
        )


@router.get("/courses")
def list_courses(branch_id: int | None = None, session: Session = Depends(get_session),
                 _=Depends(require_faculty)):
    stmt = select(Course)
    if branch_id is not None:
        stmt = stmt.where(Course.branch_id == branch_id)
    return session.exec(stmt).all()


@router.post("/courses", status_code=201)
def create_course(body: CourseCreate, session: Session = Depends(get_session), _=Depends(require_faculty)):
    get_or_404(session, Branch, body.branch_id)
    validate_category(body.category)
    _validate_session_counts(body.theory_per_week, body.practical_per_week, body.tutorial_per_week)
    course = Course.model_validate(body)
    session.add(course)
    session.commit()
    session.refresh(course)
    return course


@router.get("/courses/{course_id}")
def get_course(course_id: int, session: Session = Depends(get_session), _=Depends(require_faculty)):
    return get_or_404(session, Course, course_id)


@router.put("/courses/{course_id}")
def update_course(course_id: int, body: CourseUpdate, session: Session = Depends(get_session),
                  _=Depends(require_faculty)):
    course = get_or_404(session, Course, course_id)
    if body.category is not None:
        validate_category(body.category)
    merged = course.model_copy(update=body.model_dump(exclude_unset=True))
    _validate_session_counts(merged.theory_per_week, merged.practical_per_week, merged.tutorial_per_week)
    apply_update(course, body)
    session.add(course)
    session.commit()
    session.refresh(course)
    return course


@router.delete("/courses/{course_id}")
def delete_course(course_id: int, session: Session = Depends(get_session), _=Depends(require_faculty)):
    course = get_or_404(session, Course, course_id)
    for alloc in session.exec(select(Allocation).where(Allocation.course_id == course_id)).all():
        session.delete(alloc)
    session.delete(course)
    session.commit()
    return {"deleted": course_id}

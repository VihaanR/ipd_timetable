 bbbvnhyu7bh6"""Small shared helpers for the entity routers (design.md §5.1 — uniform CRUD)."""
from __future__ import annotations

from fastapi import HTTPException
from sqlmodel import Session, select

from timetable.models import CourseCategory, ProgramType


def get_or_404(session: Session, model, obj_id: int):
    obj = session.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {obj_id} not found")
    return obj


def apply_update(obj, update) -> None:
    """Copy only the fields the client actually sent (exclude_unset) onto an ORM object."""
    for key, value in update.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)


def unique_or_400(session: Session, model, field: str, value, exclude_id: int | None = None) -> None:
    stmt = select(model).where(getattr(model, field) == value)
    for existing in session.exec(stmt).all():
        if existing.id != exclude_id:
            raise HTTPException(
                status_code=400,
                detail=f"{model.__name__} with {field}={value!r} already exists",
            )


def validate_program(value: str) -> None:
    try:
        ProgramType(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"program must be one of {[p.value for p in ProgramType]}",
        )


def validate_category(value: str) -> None:
    try:
        CourseCategory(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"category must be one of {[c.value for c in CourseCategory]}",
        )


def validate_room_type(value: str) -> None:
    if value not in ("classroom", "lab"):
        raise HTTPException(status_code=400, detail="room_type must be 'classroom' or 'lab'")

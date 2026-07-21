"""Slot-template CRUD — the one institutional weekly grid (design.md §5.1).

Replace-all semantics on PUT: the client sends the whole grid, we wipe and re-insert. Simplest
correct thing for a small, wholly-edited grid.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from webapp.db import get_session
from webapp.models_db import SlotTemplate, SlotTemplateCreate

router = APIRouter(prefix="/api", tags=["slots"])


@router.get("/slots")
def list_slots(session: Session = Depends(get_session)):
    rows = session.exec(select(SlotTemplate)).all()
    return sorted(rows, key=lambda s: (s.day, s.period))


@router.put("/slots")
def replace_slots(body: list[SlotTemplateCreate], session: Session = Depends(get_session)):
    for existing in session.exec(select(SlotTemplate)).all():
        session.delete(existing)
    session.flush()
    created = []
    for item in body:
        slot = SlotTemplate.model_validate(item)
        session.add(slot)
        created.append(slot)
    session.commit()
    for slot in created:
        session.refresh(slot)
    return sorted(created, key=lambda s: (s.day, s.period))

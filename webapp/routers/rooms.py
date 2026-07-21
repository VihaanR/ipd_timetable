"""Room CRUD (global — shared across branches). design.md §5.1."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from webapp.db import get_session
from webapp.models_db import Room, RoomCreate, RoomUpdate
from webapp.routers._crud import apply_update, get_or_404, unique_or_400, validate_room_type

router = APIRouter(prefix="/api", tags=["rooms"])


@router.get("/rooms")
def list_rooms(session: Session = Depends(get_session)):
    return session.exec(select(Room)).all()


@router.post("/rooms", status_code=201)
def create_room(body: RoomCreate, session: Session = Depends(get_session)):
    unique_or_400(session, Room, "code", body.code)
    validate_room_type(body.room_type)
    room = Room.model_validate(body)
    session.add(room)
    session.commit()
    session.refresh(room)
    return room


@router.get("/rooms/{room_id}")
def get_room(room_id: int, session: Session = Depends(get_session)):
    return get_or_404(session, Room, room_id)


@router.put("/rooms/{room_id}")
def update_room(room_id: int, body: RoomUpdate, session: Session = Depends(get_session)):
    room = get_or_404(session, Room, room_id)
    if body.code is not None:
        unique_or_400(session, Room, "code", body.code, exclude_id=room_id)
    if body.room_type is not None:
        validate_room_type(body.room_type)
    apply_update(room, body)
    session.add(room)
    session.commit()
    session.refresh(room)
    return room


@router.delete("/rooms/{room_id}")
def delete_room(room_id: int, session: Session = Depends(get_session)):
    room = get_or_404(session, Room, room_id)
    session.delete(room)
    session.commit()
    return {"deleted": room_id}

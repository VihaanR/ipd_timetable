"""Branch + Division CRUD (design.md §5.1). Divisions are nested under their branch."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from webapp.db import get_session
from webapp.models_db import (
    Allocation, Branch, BranchCreate, BranchUpdate,
    Course, Division, DivisionCreate, DivisionUpdate,
)
from webapp.routers._crud import apply_update, get_or_404, unique_or_400, validate_program

router = APIRouter(prefix="/api", tags=["branches"])


# ------------------------------------------------------------------ branches
@router.get("/branches")
def list_branches(session: Session = Depends(get_session)):
    return session.exec(select(Branch)).all()


@router.post("/branches", status_code=201)
def create_branch(body: BranchCreate, session: Session = Depends(get_session)):
    unique_or_400(session, Branch, "code", body.code)
    branch = Branch.model_validate(body)
    session.add(branch)
    session.commit()
    session.refresh(branch)
    return branch


@router.get("/branches/{branch_id}")
def get_branch(branch_id: int, session: Session = Depends(get_session)):
    return get_or_404(session, Branch, branch_id)


@router.put("/branches/{branch_id}")
def update_branch(branch_id: int, body: BranchUpdate, session: Session = Depends(get_session)):
    branch = get_or_404(session, Branch, branch_id)
    if body.code is not None:
        unique_or_400(session, Branch, "code", body.code, exclude_id=branch_id)
    apply_update(branch, body)
    session.add(branch)
    session.commit()
    session.refresh(branch)
    return branch


@router.delete("/branches/{branch_id}")
def delete_branch(branch_id: int, session: Session = Depends(get_session)):
    branch = get_or_404(session, Branch, branch_id)
    # cascade: allocations of this branch's divisions -> divisions -> courses -> branch
    div_ids = [d.id for d in session.exec(select(Division).where(Division.branch_id == branch_id)).all()]
    if div_ids:
        for alloc in session.exec(select(Allocation).where(Allocation.division_id.in_(div_ids))).all():
            session.delete(alloc)
    for div in session.exec(select(Division).where(Division.branch_id == branch_id)).all():
        session.delete(div)
    for course in session.exec(select(Course).where(Course.branch_id == branch_id)).all():
        session.delete(course)
    session.delete(branch)
    session.commit()
    return {"deleted": branch_id}


# ------------------------------------------------------------------ divisions (nested)
@router.get("/branches/{branch_id}/divisions")
def list_divisions(branch_id: int, session: Session = Depends(get_session)):
    get_or_404(session, Branch, branch_id)
    return session.exec(select(Division).where(Division.branch_id == branch_id)).all()


@router.post("/branches/{branch_id}/divisions", status_code=201)
def create_division(branch_id: int, body: DivisionCreate, session: Session = Depends(get_session)):
    get_or_404(session, Branch, branch_id)
    validate_program(body.program)
    # unique division name within the branch
    for existing in session.exec(select(Division).where(Division.branch_id == branch_id)).all():
        if existing.name == body.name:
            raise HTTPException(status_code=400, detail=f"division {body.name!r} already exists in this branch")
    data = body.model_dump()
    data["branch_id"] = branch_id  # path wins over body
    division = Division.model_validate(data)
    session.add(division)
    session.commit()
    session.refresh(division)
    return division


@router.get("/divisions/{division_id}")
def get_division(division_id: int, session: Session = Depends(get_session)):
    return get_or_404(session, Division, division_id)


@router.put("/divisions/{division_id}")
def update_division(division_id: int, body: DivisionUpdate, session: Session = Depends(get_session)):
    division = get_or_404(session, Division, division_id)
    if body.program is not None:
        validate_program(body.program)
    apply_update(division, body)
    session.add(division)
    session.commit()
    session.refresh(division)
    return division


@router.delete("/divisions/{division_id}")
def delete_division(division_id: int, session: Session = Depends(get_session)):
    division = get_or_404(session, Division, division_id)
    for alloc in session.exec(select(Allocation).where(Allocation.division_id == division_id)).all():
        session.delete(alloc)
    session.delete(division)
    session.commit()
    return {"deleted": division_id}

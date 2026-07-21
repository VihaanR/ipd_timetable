"""Tests for webapp/problem_builder.py — assembling a ProblemInstance from DB rows (P2 task 1).

Each test runs against a throwaway SQLite file via `set_engine`, mirroring the pattern in
`tests/test_api_entities.py`, so the real platform DB is never touched.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from timetable.io_json import problem_from_dict
from webapp.db import set_engine, init_db, get_engine
from webapp.models_db import Allocation, Branch, Course, Division, Faculty, Room, SlotTemplate
from webapp.problem_builder import build_problem_dict, readiness
from webapp.server import app


@pytest.fixture
def client(tmp_path):
    db_file = tmp_path / "test_platform.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    set_engine(engine)
    init_db()
    with TestClient(app) as c:
        yield c
    engine.dispose()


def test_build_problem_dict_from_reference_seed(client):
    r = client.post("/api/seed/reference")
    assert r.status_code == 200, r.text

    with Session(get_engine()) as session:
        data = build_problem_dict(session)
        problem = problem_from_dict(data)

    assert len(problem.divisions) == 3
    assert len(problem.faculty) == 20
    assert len(problem.time_slots) == 40
    assert len(problem.courses) == 8
    assert problem.validate() == []


def test_readiness_empty_db(client):
    with Session(get_engine()) as session:
        problem, issues = readiness(session)

    assert problem is None
    assert issues != []


def test_branch_ids_filter(client):
    r = client.post("/api/seed/reference")
    assert r.status_code == 200, r.text
    first_branch_id = r.json()["branch_id"]

    with Session(get_engine()) as session:
        second_branch = Branch(code="OTHER", name="Other Branch")
        session.add(second_branch)
        session.flush()

        other_course = Course(
            branch_id=second_branch.id, code="ZZZ", title="Filler Course",
            theory_per_week=2, category="Major",
        )
        session.add(other_course)
        session.flush()

        other_division = Division(
            branch_id=second_branch.id, name="Z1", program="FYUP", semester=1, student_count=10,
        )
        session.add(other_division)
        session.flush()

        other_faculty = Faculty(code="ZF", name="Zed Faculty")
        session.add(other_faculty)
        session.flush()

        session.add(Allocation(division_id=other_division.id, course_id=other_course.id,
                                faculty_id=other_faculty.id))
        session.commit()

        data = build_problem_dict(session, branch_ids=[first_branch_id])

    codes = {c["code"] for c in data["courses"]}
    div_ids = {d["id"] for d in data["divisions"]}
    assert "ZZZ" not in codes
    assert "Z1" not in div_ids
    assert len(data["divisions"]) == 3
    assert len(data["courses"]) == 8
    # global entities are never branch-filtered
    assert len(data["faculty"]) == 21
    assert len(data["rooms"]) == 8
    assert len(data["time_slots"]) == 40


def test_missing_faculty_reference_surfaces_readiness_issue(client):
    with Session(get_engine()) as session:
        branch = Branch(code="B1", name="Branch One")
        session.add(branch)
        session.flush()

        session.add(SlotTemplate(day=0, period=0, start="08:00", end="09:00"))
        session.add(SlotTemplate(day=0, period=1, start="09:00", end="10:00"))
        session.add(Room(code="CR1", name="Classroom 1", capacity=60, room_type="classroom"))

        course = Course(branch_id=branch.id, code="X1", title="Course X1",
                         theory_per_week=1, category="Major")
        session.add(course)
        session.flush()

        division = Division(branch_id=branch.id, name="D1", program="FYUP", semester=1,
                             student_count=10)
        session.add(division)
        session.flush()

        # allocation with no faculty assigned at all -> course is referenced by the division but
        # is left out of faculty_by_course, which validate() must flag.
        session.add(Allocation(division_id=division.id, course_id=course.id))
        session.commit()

        problem, issues = readiness(session)

    assert issues != []
    assert any("faculty" in issue.lower() for issue in issues)

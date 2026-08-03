"""API tests for the P1 entity CRUD + seed endpoints (design.md §11).

Each test runs against a throwaway SQLite file via `set_engine`, so the real platform DB is never
touched and tests stay isolated.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from webapp.db import set_engine, init_db
from webapp.models_db import Faculty
from webapp.server import app


@pytest.fixture
def client(tmp_path):
    """A TestClient already logged in as a teacher (Auth, design.md §11) - every existing test in
    this file predates auth and calls now-guarded routes with no login of its own, so this fixture
    bootstraps + logs in a throwaway teacher account before yielding, keeping every test body
    unchanged."""
    db_file = tmp_path / "test_platform.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    set_engine(engine)
    init_db()
    with Session(engine) as session:
        session.add(Faculty(code="TESTFAC", name="Test Teacher"))
        session.commit()
    with TestClient(app) as c:
        c.post("/api/auth/bootstrap",
               json={"faculty_code": "TESTFAC", "email": "test@test.local", "password": "testpass123"})
        yield c
    engine.dispose()


def test_branch_crud_roundtrip(client):
    # create
    r = client.post("/api/branches", json={"code": "CSE-DS", "name": "CSE Data Science"})
    assert r.status_code == 201, r.text
    branch = r.json()
    bid = branch["id"]

    # duplicate code rejected
    dup = client.post("/api/branches", json={"code": "CSE-DS", "name": "again"})
    assert dup.status_code == 400

    # list + get
    assert any(b["id"] == bid for b in client.get("/api/branches").json())
    assert client.get(f"/api/branches/{bid}").json()["code"] == "CSE-DS"

    # update
    up = client.put(f"/api/branches/{bid}", json={"name": "Renamed"})
    assert up.status_code == 200 and up.json()["name"] == "Renamed"

    # delete
    assert client.delete(f"/api/branches/{bid}").status_code == 200
    assert client.get(f"/api/branches/{bid}").status_code == 404


def test_division_nested_under_branch(client):
    bid = client.post("/api/branches", json={"code": "IT", "name": "Information Tech"}).json()["id"]
    r = client.post(f"/api/branches/{bid}/divisions", json={"name": "D1", "program": "FYUP", "semester": 4, "student_count": 60})
    assert r.status_code == 201, r.text
    # duplicate division name in the branch rejected
    assert client.post(f"/api/branches/{bid}/divisions", json={"name": "D1"}).status_code == 400
    # invalid program rejected
    assert client.post(f"/api/branches/{bid}/divisions", json={"name": "D2", "program": "NOPE"}).status_code == 400
    divs = client.get(f"/api/branches/{bid}/divisions").json()
    assert [d["name"] for d in divs] == ["D1"]


def test_faculty_delete_blocked_while_allocated(client):
    bid = client.post("/api/branches", json={"code": "B1", "name": "B1"}).json()["id"]
    did = client.post(f"/api/branches/{bid}/divisions", json={"name": "D1"}).json()["id"]
    cid = client.post("/api/courses", json={"branch_id": bid, "code": "DS", "title": "Data Structures", "theory_per_week": 3}).json()["id"]
    fid = client.post("/api/faculty", json={"code": "AR", "name": "Aditi Raut"}).json()["id"]
    # theory-only course -> single faculty allocation
    a = client.post("/api/allocations", json={"division_id": did, "course_id": cid, "faculty_id": fid})
    assert a.status_code == 201, a.text
    # cannot delete faculty referenced by an allocation
    assert client.delete(f"/api/faculty/{fid}").status_code == 400


def test_course_requires_a_session_type(client):
    bid = client.post("/api/branches", json={"code": "B2", "name": "B2"}).json()["id"]
    bad = client.post("/api/courses", json={"branch_id": bid, "code": "X", "title": "X"})
    assert bad.status_code == 400  # all session counts zero
    good = client.post("/api/courses", json={"branch_id": bid, "code": "Y", "title": "Y", "practical_per_week": 1})
    assert good.status_code == 201


def test_allocation_batch_faculty_rules(client):
    bid = client.post("/api/branches", json={"code": "B3", "name": "B3"}).json()["id"]
    did = client.post(f"/api/branches/{bid}/divisions", json={"name": "D1"}).json()["id"]
    lab = client.post("/api/courses", json={"branch_id": bid, "code": "L", "title": "Lab", "practical_per_week": 1}).json()["id"]
    f1 = client.post("/api/faculty", json={"code": "F1", "name": "F1"}).json()["id"]
    f2 = client.post("/api/faculty", json={"code": "F2", "name": "F2"}).json()["id"]
    # practical course with only a single faculty -> rejected
    assert client.post("/api/allocations", json={"division_id": did, "course_id": lab, "faculty_id": f1}).status_code == 400
    # both batch faculty -> accepted
    ok = client.post("/api/allocations", json={"division_id": did, "course_id": lab, "batch1_faculty_id": f1, "batch2_faculty_id": f2})
    assert ok.status_code == 201, ok.text


def test_rooms_and_slots(client):
    assert client.post("/api/rooms", json={"code": "CR1", "name": "Room 1", "capacity": 70, "room_type": "classroom"}).status_code == 201
    assert client.post("/api/rooms", json={"code": "CR2", "name": "Room 2", "room_type": "spaceship"}).status_code == 400
    grid = [{"day": 0, "period": 0, "start": "08:00", "end": "09:00"},
            {"day": 0, "period": 1, "start": "09:00", "end": "10:00"}]
    r = client.put("/api/slots", json=grid)
    assert r.status_code == 200 and len(r.json()) == 2
    # replace-all semantics: a second PUT with one slot leaves exactly one
    assert len(client.put("/api/slots", json=grid[:1]).json()) == 1
    assert len(client.get("/api/slots").json()) == 1


def test_seed_reference_idempotence(client):
    r = client.post("/api/seed/reference")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["divisions"] == 3 and body["faculty"] == 20 and body["courses"] == 8
    # second seed without force -> 409
    assert client.post("/api/seed/reference").status_code == 409
    # force re-seed wipes + rebuilds
    forced = client.post("/api/seed/reference?force=true")
    assert forced.status_code == 200 and forced.json()["forced"] is True
    # exactly one branch after a forced re-seed (no duplication)
    assert len(client.get("/api/branches").json()) == 1
    # allocations were created (split-batch labs -> batch faculty populated)
    allocs = client.get("/api/allocations").json()
    assert len(allocs) > 0
    assert any(a["batch1_faculty_id"] and a["batch2_faculty_id"] for a in allocs)

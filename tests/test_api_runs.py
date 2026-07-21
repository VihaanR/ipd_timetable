"""API tests for the P2 generate job + runs router (design.md §5.3, CLAUDE.md §11).

Background jobs run in Starlette's threadpool via BackgroundTasks. Under TestClient, background
tasks execute synchronously right after the response body is produced but before `.post()` returns
control to the caller — so by the time `POST /api/generate` comes back, the job has already
finished. No polling loop is needed in these tests; a single `GET /api/runs/{id}` right after is
enough to observe the final status.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from webapp.db import set_engine, init_db, get_engine
from webapp.models_db import TimetableRun
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


def _seed(client):
    r = client.post("/api/seed/reference")
    assert r.status_code == 200, r.text
    return r.json()


def test_generate_greedy_runs_to_done(client):
    _seed(client)
    r = client.post("/api/generate", json={"solver": "greedy", "time_limit": 3})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    got = client.get(f"/api/runs/{run_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "done"
    assert body["grids"]  # non-empty dict/list
    assert isinstance(body["hard"], int)


def test_generate_on_empty_db_rejected(client):
    r = client.post("/api/generate", json={"solver": "greedy", "time_limit": 3})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert isinstance(detail, list) and len(detail) > 0


def test_readiness_endpoint(client):
    empty = client.get("/api/readiness")
    assert empty.status_code == 200
    body = empty.json()
    assert body["ready"] is False
    assert isinstance(body["issues"], list) and len(body["issues"]) > 0

    _seed(client)
    ready = client.get("/api/readiness")
    assert ready.json()["ready"] is True


def test_generate_single_flight_conflict(client):
    _seed(client)
    with Session(get_engine()) as session:
        session.add(TimetableRun(status="running", solver="greedy", time_limit=3, problem_snapshot={}))
        session.commit()

    r = client.post("/api/generate", json={"solver": "greedy", "time_limit": 3})
    assert r.status_code == 409


def test_generate_unknown_solver_rejected(client):
    _seed(client)
    r = client.post("/api/generate", json={"solver": "banana", "time_limit": 3})
    assert r.status_code == 400


def test_list_runs_after_generate(client):
    _seed(client)
    r = client.post("/api/generate", json={"solver": "greedy", "time_limit": 3})
    run_id = r.json()["run_id"]

    runs = client.get("/api/runs").json()
    assert any(entry["id"] == run_id for entry in runs)

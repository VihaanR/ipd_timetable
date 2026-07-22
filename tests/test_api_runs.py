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
from webapp.jobs import sweep_stale_running
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
    r = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    got = client.get(f"/api/runs/{run_id}")
    assert got.status_code == 200
    body = got.json()
    assert body["status"] == "done"
    assert body["grids"]  # non-empty dict/list
    assert isinstance(body["hard"], int)


def test_generate_on_empty_db_rejected(client):
    r = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3})
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

    r = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3})
    assert r.status_code == 409


def test_sweep_clears_orphaned_queued_run_and_unblocks_generate(client):
    """A process that dies between `POST /api/runs` inserting a `queued` row and the background
    task flipping it to `running` leaves that row permanently `queued` — nothing in a fresh
    process will ever pick it up. Before the fix, `sweep_stale_running` only looked at `running`
    rows, so this `queued` row would keep `has_active_run` true forever and every subsequent
    `POST /api/runs` would 409 with no recovery path. Assert the sweep clears it and generate
    is unblocked afterward."""
    with Session(get_engine()) as session:
        run = TimetableRun(status="queued", solver="greedy", time_limit=1, problem_snapshot={})
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    with Session(get_engine()) as session:
        swept = sweep_stale_running(session)
        assert swept == 1

    with Session(get_engine()) as session:
        run = session.get(TimetableRun, run_id)
        assert run.status == "failed"
        assert run.error == "orphaned by restart"

    _seed(client)
    r = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3})
    assert r.status_code != 409, r.text


def test_generate_unknown_solver_rejected(client):
    _seed(client)
    r = client.post("/api/runs", json={"solver": "banana", "time_limit": 3})
    assert r.status_code == 400


def test_list_runs_after_generate(client):
    _seed(client)
    r = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3})
    run_id = r.json()["run_id"]

    runs = client.get("/api/runs").json()
    assert any(entry["id"] == run_id for entry in runs)


def test_compare_mode_returns_multiple_solver_results(client):
    _seed(client)
    r = client.post(
        "/api/compare",
        json={"time_limit": 3, "solvers": ["greedy", "cpsat"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"] and len(body["results"]) == 2
    assert body["best_solver"] in {"greedy", "cpsat"}
    assert isinstance(body["best_index"], int)
    assert body["results"][0]["grids"]


def test_compare_mode_with_pipeline_does_not_500(client):
    _seed(client)
    r = client.post(
        "/api/compare",
        json={"time_limit": 3, "solvers": ["pipeline", "greedy"]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 2
    by_name = {entry["solver"]: entry for entry in body["results"]}
    assert "pipeline" in by_name
    assert isinstance(by_name["pipeline"]["stage_reports"], list)


def test_compare_mode_rejects_invalid_solver(client):
    _seed(client)
    r = client.post(
        "/api/compare",
        json={"time_limit": 3, "solvers": ["greedy", "banana"]},
    )
    assert r.status_code == 400


def test_export_xlsx_of_done_run(client):
    _seed(client)
    run_id = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3}).json()["run_id"]

    r = client.get(f"/api/runs/{run_id}/export.xlsx")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert len(r.content) > 0


def test_export_pdf_of_done_run(client):
    _seed(client)
    run_id = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3}).json()["run_id"]

    r = client.get(f"/api/runs/{run_id}/export.pdf")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert len(r.content) > 0


def test_export_missing_run_404(client):
    r = client.get("/api/runs/999999/export.xlsx")
    assert r.status_code == 404

    r = client.get("/api/runs/999999/export.pdf")
    assert r.status_code == 404


def test_export_not_done_run_409(client):
    with Session(get_engine()) as session:
        run = TimetableRun(status="queued", solver="greedy", time_limit=3, problem_snapshot={})
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    r = client.get(f"/api/runs/{run_id}/export.xlsx")
    assert r.status_code == 409

    r = client.get(f"/api/runs/{run_id}/export.pdf")
    assert r.status_code == 409


def test_legacy_generate_still_reachable(client):
    """The DB-backed generate job now lives at POST /api/runs (see module docstring). This locks
    in that /api/generate still resolves to the legacy in-memory showcase handler in
    webapp/server.py (api_generate), not the new runs router - i.e. the new route no longer
    shadows the legacy endpoint."""
    _seed(client)
    r = client.post("/api/generate", json={"dataset": "reference", "solver": "greedy", "time_limit": 3})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "grids" in body
    assert "hard_violations" in body
    assert "run_id" not in body


def test_adjust_run_returns_overlay(client):
    _seed(client)
    run_id = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3}).json()["run_id"]
    # rain from period 5 on Tuesday (day 1)
    r = client.post(f"/api/runs/{run_id}/adjust", json={"day": 1, "from_period": 5, "reason": "rain"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["disrupted_day"] == "Tuesday"
    assert body["grids"] and body["grids"]["divisions"]
    assert isinstance(body["moved"], list)
    assert len(body["affected_slot_ids"]) > 0  # a from-period cut blocks the tail of the day


def test_adjust_run_validates_day_and_status(client):
    _seed(client)
    run_id = client.post("/api/runs", json={"solver": "greedy", "time_limit": 3}).json()["run_id"]
    assert client.post(f"/api/runs/{run_id}/adjust", json={"day": 9}).status_code == 400   # bad day
    assert client.post("/api/runs/9999/adjust", json={"day": 0}).status_code == 404          # missing run
    with Session(get_engine()) as s:
        s.add(TimetableRun(id=555, status="queued", solver="greedy", time_limit=3, problem_snapshot={}))
        s.commit()
    assert client.post("/api/runs/555/adjust", json={"day": 0}).status_code == 409            # not done

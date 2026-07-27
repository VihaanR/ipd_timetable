"""Smoke test for the P2 frontend page: `GET /platform` serves the new DB-backed generate SPA
(webapp/static/platform.html), distinct from the legacy in-memory showcase at `GET /`.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import create_engine

from webapp.db import set_engine, init_db
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


def test_platform_page_served(client):
    r = client.get("/platform")
    assert r.status_code == 200
    assert "Timetable Platform" in r.text


def test_legacy_showcase_still_served(client):
    r = client.get("/")
    assert r.status_code == 200


def test_dashboard_page_served(client):
    """The entity data-entry dashboard (faculty/branches/divisions/subjects/allocations/rooms/slots)."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    for heading in ("Faculty", "Branches", "Divisions", "Subjects", "Allocations", "Rooms"):
        assert heading in r.text, f"dashboard missing {heading} section"

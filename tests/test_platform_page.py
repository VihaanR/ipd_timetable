"""Smoke test for the platform's page routes. `GET /platform` serves the DB-backed generate SPA
(webapp/static/platform.html); `GET /` and `GET /dashboard` both serve the dashboard, now the
site's home page (the legacy in-memory showcase that used to live at `/` was retired - design.md
§11/§8.1). All three are teacher-only and redirect an anonymous visitor to `/login`.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from webapp.db import set_engine, init_db
from webapp.models_db import Faculty
from webapp.server import app


@pytest.fixture
def client(tmp_path):
    """A TestClient already logged in as a teacher (Auth, design.md §11) - see the identical note
    in tests/test_api_entities.py's fixture."""
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


def test_platform_page_served(client):
    r = client.get("/platform")
    assert r.status_code == 200
    assert "Timetable Platform" in r.text


def test_root_serves_the_dashboard(client):
    """`/` is now the site's home page and serves the same content as `/dashboard`."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Faculty" in r.text


def test_dashboard_page_served(client):
    """The entity data-entry dashboard (faculty/branches/divisions/subjects/allocations/rooms/slots)."""
    r = client.get("/dashboard")
    assert r.status_code == 200
    for heading in ("Faculty", "Branches", "Divisions", "Subjects", "Allocations", "Rooms"):
        assert heading in r.text, f"dashboard missing {heading} section"


def test_pages_redirect_anonymous_visitors_to_login(tmp_path):
    """Server-side page guard (Auth, design.md §11): a fresh, un-logged-in client is redirected to
    /login for every teacher-only page, not just shown a client-side flash of protected content."""
    db_file = tmp_path / "test_platform_anon.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    set_engine(engine)
    init_db()
    with TestClient(app) as anon:
        for path in ("/", "/dashboard", "/platform"):
            r = anon.get(path)
            assert r.status_code == 200
            assert r.request.url.path == "/login", f"{path} did not redirect to /login"
    engine.dispose()

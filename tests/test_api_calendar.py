"""API tests for the P3 academic-calendar ingestion endpoints (design.md §5.2/§6, CLAUDE.md §12).

Each test runs against a throwaway SQLite file via `set_engine` (same pattern as
tests/test_api_entities.py), and uploaded bytes are redirected to a tmp_path directory via the
TIMETABLE_UPLOADS_PATH env var override (mirrors webapp/db.py's TIMETABLE_DB_PATH pattern) so
tests never touch or leave junk in the real webapp/uploads/.

No live Anthropic API call is ever made here: ANTHROPIC_API_KEY is never set by these tests, so
extraction always short-circuits to the 501 path, and the trust-boundary test simulates a genuine
extraction-created draft directly at the DB layer instead of hitting the network.
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from fpdf import FPDF
from PIL import Image
from sqlmodel import Session, create_engine

from webapp.db import get_engine, set_engine, init_db
from webapp.models_db import CalendarEvent
from webapp.server import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test_platform.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    set_engine(engine)
    init_db()
    # Redirect calendar uploads to a throwaway directory for the duration of this test.
    monkeypatch.setenv("TIMETABLE_UPLOADS_PATH", str(tmp_path / "uploads"))
    # Never let a real key leak into these tests - extraction must always take the 501 path
    # unless a specific test explicitly wants otherwise (none do; see the trust-boundary test).
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with TestClient(app) as c:
        yield c
    engine.dispose()


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


def _pdf_bytes() -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(text="Academic calendar 2026")
    return bytes(pdf.output())


def _make_term(client, name="Sem IV Jan-May 2026"):
    r = client.post("/api/terms", json={"name": name, "start_date": "2026-01-05", "end_date": "2026-05-15"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --------------------------------------------------------------------------- upload validation
def test_upload_valid_png_accepted(client):
    r = client.post("/api/calendar/upload", files={"file": ("calendar.png", _png_bytes(), "image/png")})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "upload_id" in body and isinstance(body["upload_id"], int)


def test_upload_valid_pdf_accepted(client):
    r = client.post("/api/calendar/upload", files={"file": ("calendar.pdf", _pdf_bytes(), "application/pdf")})
    assert r.status_code == 201, r.text


def test_upload_wrong_magic_bytes_rejected(client):
    # named and claimed as a png, but the content is plain text - magic-byte sniff must catch this
    # even though the filename/mime claim looks legitimate
    r = client.post("/api/calendar/upload", files={"file": ("calendar.png", b"not-an-image", "image/png")})
    assert r.status_code == 400
    assert "detail" in r.json()


def test_upload_corrupt_pdf_rejected(client):
    # correct magic bytes, but not a real PDF structure underneath - must actually open/parse it
    r = client.post("/api/calendar/upload", files={"file": ("calendar.pdf", b"%PDF-1.4\ngarbage, not a real pdf", "application/pdf")})
    assert r.status_code == 400


def test_upload_oversized_file_rejected(client):
    huge = b"\x89PNG\r\n\x1a\n" + b"a" * (20 * 1024 * 1024 + 1)  # magic bytes + >20MB padding
    r = client.post("/api/calendar/upload", files={"file": ("big.png", huge, "image/png")})
    assert r.status_code == 400


def test_upload_empty_file_rejected(client):
    r = client.post("/api/calendar/upload", files={"file": ("empty.png", b"", "image/png")})
    assert r.status_code == 400


# --------------------------------------------------------------------------- serving the raw file
def test_get_upload_file_returns_same_bytes(client):
    original = _png_bytes()
    upload_id = client.post("/api/calendar/upload", files={"file": ("calendar.png", original, "image/png")}).json()["upload_id"]

    r = client.get(f"/api/calendar/uploads/{upload_id}/file")
    assert r.status_code == 200
    assert r.content == original
    assert r.headers["content-type"] == "image/png"


def test_get_upload_file_404_for_unknown_id(client):
    assert client.get("/api/calendar/uploads/999999/file").status_code == 404


def test_list_uploads(client):
    upload_id = client.post("/api/calendar/upload", files={"file": ("c.png", _png_bytes(), "image/png")}).json()["upload_id"]
    listed = client.get("/api/calendar/uploads").json()
    assert any(u["id"] == upload_id for u in listed)
    assert all("sha256" in u and "uploaded_at" in u for u in listed)


# --------------------------------------------------------------------------- term CRUD
def test_term_crud_roundtrip(client):
    tid = _make_term(client, "Sem IV Jan-May 2026")

    listed = client.get("/api/terms").json()
    assert any(t["id"] == tid for t in listed)

    fetched = client.get(f"/api/terms/{tid}").json()
    assert fetched["name"] == "Sem IV Jan-May 2026"
    assert fetched["start_date"] == "2026-01-05" and fetched["end_date"] == "2026-05-15"

    updated = client.put(f"/api/terms/{tid}", json={"name": "Sem IV (renamed)"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Sem IV (renamed)"
    assert updated.json()["start_date"] == "2026-01-05"  # untouched fields survive a partial PUT


def test_term_get_404_for_unknown_id(client):
    assert client.get("/api/terms/999999").status_code == 404


# --------------------------------------------------------------------------- event CRUD + confirm
def test_event_crud_roundtrip_and_confirm_flow(client):
    tid = _make_term(client)

    created = client.post("/api/calendar/events", json={
        "term_id": tid, "date": "2026-01-26", "name": "Republic Day", "kind": "holiday",
    })
    assert created.status_code == 201, created.text
    event = created.json()
    eid = event["id"]
    assert event["source"] == "manual"
    assert event["confirmed"] is False  # manual creation always starts unconfirmed

    listed = client.get("/api/calendar/events", params={"term_id": tid}).json()
    assert any(e["id"] == eid for e in listed)

    updated = client.put(f"/api/calendar/events/{eid}", json={"name": "Republic Day (India)"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Republic Day (India)"
    assert updated.json()["kind"] == "holiday"  # untouched field survives partial PUT

    # confirm flow: false -> true
    confirmed = client.put(f"/api/calendar/events/{eid}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["confirmed"] is True

    deleted = client.delete(f"/api/calendar/events/{eid}")
    assert deleted.status_code == 200
    assert client.get("/api/calendar/events", params={"term_id": tid}).json() == []


def test_event_invalid_kind_rejected(client):
    tid = _make_term(client)
    r = client.post("/api/calendar/events", json={
        "term_id": tid, "date": "2026-01-26", "name": "X", "kind": "not-a-real-kind",
    })
    assert r.status_code == 400


def test_event_unknown_term_rejected(client):
    r = client.post("/api/calendar/events", json={
        "term_id": 999999, "date": "2026-01-26", "name": "X", "kind": "event",
    })
    assert r.status_code == 404


# --------------------------------------------------------------------------- filtering
def test_filter_events_by_confirmed(client):
    tid = _make_term(client)
    e1 = client.post("/api/calendar/events", json={
        "term_id": tid, "date": "2026-01-26", "name": "Republic Day", "kind": "holiday",
    }).json()["id"]
    e2 = client.post("/api/calendar/events", json={
        "term_id": tid, "date": "2026-03-14", "name": "Holi", "kind": "holiday",
    }).json()["id"]
    client.put(f"/api/calendar/events/{e1}/confirm")

    unconfirmed = client.get("/api/calendar/events", params={"term_id": tid, "confirmed": False}).json()
    confirmed = client.get("/api/calendar/events", params={"term_id": tid, "confirmed": True}).json()

    assert [e["id"] for e in unconfirmed] == [e2]
    assert [e["id"] for e in confirmed] == [e1]


# --------------------------------------------------------------------------- the trust boundary
def test_manual_endpoint_cannot_fabricate_an_extracted_or_pre_confirmed_row(client):
    """Enforcing the trust rule (CLAUDE.md §12): only the extract endpoint may ever write
    source="extracted", and confirmed can only ever become True via PUT .../confirm - never at
    creation time, manual or otherwise."""
    tid = _make_term(client)

    # a client trying to smuggle source="extracted" through the manual endpoint is rejected
    # outright, not silently downgraded
    rejected = client.post("/api/calendar/events", json={
        "term_id": tid, "date": "2026-02-01", "name": "Fake extraction", "kind": "event",
        "source": "extracted",
    })
    assert rejected.status_code == 400

    # even a legitimate manual create can never start out confirmed=true, regardless of what the
    # client sends for that field
    created = client.post("/api/calendar/events", json={
        "term_id": tid, "date": "2026-02-02", "name": "Manual entry", "kind": "event",
        "confirmed": True,
    })
    assert created.status_code == 201
    assert created.json()["source"] == "manual"
    assert created.json()["confirmed"] is False


def test_extraction_created_rows_are_always_unconfirmed_and_only_confirm_flips_them(client):
    tid = _make_term(client)

    # Simulate exactly what webapp/routers/calendar.py's extract endpoint would insert - a genuine
    # extraction draft is never reachable through the public API without a live Anthropic call
    # (which these tests must not make), so we fabricate the row the same way that code path does.
    with Session(get_engine()) as s:
        draft = CalendarEvent(
            term_id=tid, date="2026-01-26", name="Republic Day", kind="holiday",
            source="extracted", confirmed=False,
        )
        s.add(draft)
        s.commit()
        s.refresh(draft)
        draft_id = draft.id

    unconfirmed = client.get("/api/calendar/events", params={"term_id": tid, "confirmed": False}).json()
    match = next(e for e in unconfirmed if e["id"] == draft_id)
    assert match["source"] == "extracted"
    assert match["confirmed"] is False

    # confirming is the only state transition available, and it only ever moves false -> true
    flipped = client.put(f"/api/calendar/events/{draft_id}/confirm")
    assert flipped.status_code == 200
    assert flipped.json()["confirmed"] is True
    assert flipped.json()["source"] == "extracted"  # provenance is untouched by confirming

    confirmed_list = client.get("/api/calendar/events", params={"confirmed": True}).json()
    assert any(e["id"] == draft_id for e in confirmed_list)


# --------------------------------------------------------------------------- optional AI extraction
def test_extract_returns_501_without_anthropic_api_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    upload_id = client.post(
        "/api/calendar/upload", files={"file": ("calendar.png", _png_bytes(), "image/png")}
    ).json()["upload_id"]

    r = client.post(f"/api/calendar/extract/{upload_id}")
    assert r.status_code == 501
    assert "ANTHROPIC_API_KEY" in r.json()["detail"]

    # and definitely didn't create any draft events as a side effect
    assert client.get("/api/calendar/events").json() == []


def test_extract_404_for_unknown_upload(client):
    assert client.post("/api/calendar/extract/999999").status_code == 404

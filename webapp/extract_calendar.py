"""Optional Claude-vision academic-calendar extraction (design.md §6 Layer 1, roadmap P3).

Layered, manual-first: Layer 0 (upload + manual entry, in webapp/routers/calendar.py) always
works and needs nothing from this module. This module adds two *additive* extraction sources.
Both land in the same `calendar_event` review table as `source="extracted", confirmed=False`
drafts - nothing in this module may ever produce a confirmed row or write anything other than
that (CLAUDE.md §12 / design.md §6 trust rule: machine-extracted data is never trusted until a
human confirms it in the review UI). Enforcing `confirmed=False` is this module's job just as
much as the router's - `extract_events()` below never reads or honors any caller-supplied
`confirmed` value because there isn't one: the return shape is `{date, name, kind}` only.

  1. A free, no-API-key-cost local fallback: for a born-digital PDF, pull the pypdf text layer and
     regex out date-shaped lines. No network call is made for this path.
  2. Claude-vision extraction (Layer 1): sends the image or PDF to Claude with a structured-output
     schema. Reached when the local text-layer pass finds nothing (a scanned/image-only PDF, or a
     jpg/png photo of a printed calendar) - or the upload isn't a PDF at all.

Both sources only ever run when `ANTHROPIC_API_KEY` is configured (`extraction_available()`) -
POST /api/calendar/extract/{upload_id} in webapp/routers/calendar.py returns 501 otherwise, never
a crash. The `anthropic` import is deferred into the one function that needs it, so this module -
and the whole app, since webapp/server.py imports webapp.routers.calendar unconditionally at
boot - stays importable even if the `anthropic` package were ever missing.
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Optional

import pypdf

from webapp.models_db import CalendarUpload

MODEL = "claude-opus-5"
VALID_KINDS = ("holiday", "exam", "event")


class ExtractionUnavailable(RuntimeError):
    """Raised when ANTHROPIC_API_KEY is not configured. Callers (webapp/routers/calendar.py)
    turn this into an HTTP 501 - never a 500, never a crash."""


def extraction_available() -> bool:
    """Whether the optional AI extraction feature is enabled at all. Checked live (not cached at
    import time) so tests can toggle it per-test with monkeypatch.setenv/delenv."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------- free local fallback
# A generous but bounded date-then-label matcher: "15 Jan 2026 - Republic Day", "15/01/2026: ...",
# "2026-01-15  Diwali", etc. This is a heuristic, not a full calendar parser - it only needs to
# surface plausible *draft* candidates for a human to review/edit/confirm, never to be perfectly
# correct on its own (trust rule).
_DATE_LINE_RE = re.compile(
    r"(?P<date>\d{1,2}[\/\-. ](?:[A-Za-z]{3,9}|\d{1,2})[\/\-. ]\d{2,4}"
    r"|\d{4}-\d{1,2}-\d{1,2})"
    r"\s*[:\-–—]?\s*"
    r"(?P<name>[A-Za-z][^\n]{2,80})"
)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _normalize_date(raw: str) -> Optional[str]:
    """Best-effort "whatever a printed calendar wrote" -> ISO 'YYYY-MM-DD', or None if it doesn't
    parse as a date at all (the caller then discards that candidate)."""
    raw = raw.strip()

    m = re.match(r"^(\d{1,2})[\/\-. ]([A-Za-z]{3,9})[\/\-. ](\d{2,4})$", raw)
    if m:
        day, mon_name, year = m.groups()
        month = _MONTHS.get(mon_name.lower())
        if month is None:
            return None
        year_i = int(year)
        if year_i < 100:
            year_i += 2000
        try:
            return f"{year_i:04d}-{month:02d}-{int(day):02d}"
        except ValueError:
            return None

    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", raw)
    if m:
        year_i, month, day = (int(x) for x in m.groups())
        return f"{year_i:04d}-{month:02d}-{day:02d}"

    m = re.match(r"^(\d{1,2})[\/\-.](\d{1,2})[\/\-.](\d{2,4})$", raw)
    if m:
        day, month, year_i = (int(x) for x in m.groups())
        if year_i < 100:
            year_i += 2000
        if month > 12 and day <= 12:
            day, month = month, day
        if not (1 <= month <= 12):
            return None
        return f"{year_i:04d}-{month:02d}-{day:02d}"

    return None


def _guess_kind(name: str) -> str:
    lowered = name.lower()
    if "exam" in lowered or "test" in lowered:
        return "exam"
    if "holiday" in lowered or "vacation" in lowered:
        return "holiday"
    return "event"


def _extract_via_pdf_text_layer(data: bytes) -> list[dict]:
    """Free, no-network fallback: pull candidate events from a born-digital PDF's text layer.
    Returns [] (not an error) if the PDF has no usable text layer - callers fall through to the
    Claude-vision path in that case."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return []

    events: list[dict] = []
    for match in _DATE_LINE_RE.finditer(text):
        iso = _normalize_date(match.group("date"))
        if iso is None:
            continue
        name = match.group("name").strip(" -:–—")
        if not name:
            continue
        events.append({"date": iso, "name": name[:120], "kind": _guess_kind(name)})
    return events


def _extract_via_claude_vision(data: bytes, mime: str) -> list[dict]:
    """Layer 1: send the upload to Claude with a structured-output schema and parse the JSON
    array it returns. Never called when ANTHROPIC_API_KEY is unset - `extract_events()` below
    guards on `extraction_available()` before reaching here."""
    import base64

    import anthropic  # deferred: only imported on the path that actually needs it

    client = anthropic.Anthropic()
    b64 = base64.standard_b64encode(data).decode("utf-8")
    if mime == "application/pdf":
        content_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        content_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": mime, "data": b64},
        }

    schema = {
        "type": "object",
        "properties": {
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "ISO 8601 date, YYYY-MM-DD"},
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": list(VALID_KINDS)},
                    },
                    "required": ["date", "name", "kind"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["events"],
        "additionalProperties": False,
    }

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        output_config={"format": {"type": "json_schema", "schema": schema}},
        messages=[{
            "role": "user",
            "content": [
                content_block,
                {
                    "type": "text",
                    "text": (
                        "This is an academic institution's calendar. Extract every holiday, "
                        "exam, and named event visible in it. For each, give its date (ISO 8601 "
                        "YYYY-MM-DD), a short name, and a kind of 'holiday', 'exam', or 'event'. "
                        "Return every event you can identify, even ones you are only somewhat "
                        "confident about - a human will review every entry before it is used."
                    ),
                },
            ],
        }],
    )

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    parsed = json.loads(text)

    events: list[dict] = []
    for item in parsed.get("events", []):
        kind = item.get("kind") if item.get("kind") in VALID_KINDS else "event"
        date = _normalize_date(str(item.get("date", ""))) or item.get("date")
        name = item.get("name")
        if not date or not name:
            continue
        events.append({"date": date, "name": str(name)[:120], "kind": kind})
    return events


def extract_events(upload: CalendarUpload, data: bytes) -> list[dict]:
    """Return candidate `{date, name, kind}` dicts for `upload`. This function's return shape has
    no `confirmed`/`source` field at all - the caller (webapp/routers/calendar.py) is solely
    responsible for inserting these as `source="extracted", confirmed=False` rows, and nothing
    downstream of this call may ever set confirmed=True on them.

    Raises `ExtractionUnavailable` if ANTHROPIC_API_KEY is not configured - callers turn that into
    an HTTP 501.
    """
    if not extraction_available():
        raise ExtractionUnavailable("set ANTHROPIC_API_KEY to enable calendar extraction")

    if upload.mime == "application/pdf":
        local = _extract_via_pdf_text_layer(data)
        if local:
            return local  # born-digital PDF: free win, no API call needed

    return _extract_via_claude_vision(data, upload.mime)

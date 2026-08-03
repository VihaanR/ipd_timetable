"""FastAPI backend for the DJ Sanghvi timetable platform.

The dashboard (`/`) is the site's home; `/platform` drives generate/view/export/adjust/compare;
`/login` and `/student` serve the Auth flow (design.md §11). Entity CRUD, generate/adjust/compare,
calendar, and auth all live in `webapp/routers/*.py` - this module only assembles the app, runs
the startup lifespan, and serves the handful of pages that aren't pure JSON APIs.

Run:  python -m uvicorn webapp.server:app --port 8750
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager

from sqlmodel import Session

from webapp.auth import get_current_principal
from webapp.db import init_db, get_engine
from webapp.jobs import sweep_stale_running
from webapp.routers import (
    auth as auth_router, branches, faculty, courses, rooms, allocations, slots, runs, calendar,
    students,
)
from webapp import seed

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # create SQLite tables if the platform DB is new (design.md §4.1)
    calendar.get_upload_dir()  # ensure webapp/uploads/ exists (design.md §6, CLAUDE.md §15)
    with Session(get_engine()) as session:
        sweep_stale_running(session)  # fail any "running" row orphaned by a previous restart
    yield


app = FastAPI(title="NEP Timetable Platform", lifespan=lifespan)

for _router in (auth_router.router, branches.router, faculty.router, students.router, courses.router,
                rooms.router, allocations.router, slots.router, seed.router, runs.router,
                calendar.router):
    app.include_router(_router)


def _teacher_page(filename: str, principal: dict | None):
    """Shared guard for teacher-only pages (Auth, design.md §11): the real gate, not just a
    client-side redirect that would flash protected content first. Anonymous -> /login; logged in
    as the wrong role -> /student (a student has no business on a teacher page)."""
    if principal is None:
        return RedirectResponse("/login")
    if principal["role"] != "faculty":
        return RedirectResponse("/student")
    return FileResponse(str(STATIC_DIR / filename))


@app.get("/")
def index(principal: dict | None = Depends(get_current_principal)):
    return _teacher_page("dashboard.html", principal)


@app.get("/platform")
def platform_page(principal: dict | None = Depends(get_current_principal)):
    return _teacher_page("platform.html", principal)


@app.get("/dashboard")
def dashboard_page(principal: dict | None = Depends(get_current_principal)):
    return _teacher_page("dashboard.html", principal)


@app.get("/my-timetable")
def my_timetable_page(principal: dict | None = Depends(get_current_principal)):
    return _teacher_page("faculty_timetable.html", principal)


@app.get("/guideline")
def guideline_page():
    # informational only - no login required (design.md §11 / NEP guideline page)
    return FileResponse(str(STATIC_DIR / "guideline.html"))


@app.get("/login")
def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))


@app.get("/student")
def student_page(principal: dict | None = Depends(get_current_principal)):
    if principal is None:
        return RedirectResponse("/login")
    if principal["role"] != "student":
        return RedirectResponse("/")
    return FileResponse(str(STATIC_DIR / "student.html"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8750)  # 8000 is OS-reserved on the dev box
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

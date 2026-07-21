"""SQLModel table definitions (design.md §4.2).

P1 covers the editable entity tables that feed the engine snapshot: branch -> division ->
allocation, plus global faculty / room / slot_template. Calendar, run, and adjustment tables are
added in their own phases (P2/P3/P4). Enum-valued columns (program, category, room_type) are stored
as plain strings and validated against the engine enums at the router layer, keeping the schema
portable.

Each entity follows the SQLModel pattern: a `*Base` with the shared fields, a `table=True` model
adding the primary key + FKs, a `*Create` for POST bodies, and a `*Update` (all-optional) for PUT.
The base doubles as the response schema.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


# --------------------------------------------------------------------------- branch
class BranchBase(SQLModel):
    code: str = Field(index=True)
    name: str
    semester_label: str = ""


class Branch(BranchBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class BranchCreate(BranchBase):
    pass


class BranchUpdate(SQLModel):
    code: Optional[str] = None
    name: Optional[str] = None
    semester_label: Optional[str] = None


# --------------------------------------------------------------------------- division
class DivisionBase(SQLModel):
    name: str                       # "D1"
    program: str = "FYUP"           # ProgramType value
    semester: int = 1
    student_count: int = 60
    batch1_name: str = ""           # default {name}1 applied in problem_builder if blank
    batch2_name: str = ""


class Division(DivisionBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # branch_id lives on the table + response, but is supplied by the URL path, not the POST body
    branch_id: int = Field(foreign_key="branch.id", index=True)


class DivisionCreate(DivisionBase):
    pass  # branch_id comes from the path (POST /api/branches/{id}/divisions)


class DivisionUpdate(SQLModel):
    name: Optional[str] = None
    program: Optional[str] = None
    semester: Optional[int] = None
    student_count: Optional[int] = None
    batch1_name: Optional[str] = None
    batch2_name: Optional[str] = None


# --------------------------------------------------------------------------- faculty (global)
class FacultyBase(SQLModel):
    code: str = Field(index=True)   # "AR"
    name: str
    max_load_hours_per_week: int = 20
    max_consecutive_sessions: int = 2


class Faculty(FacultyBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    unavailable_slot_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))


class FacultyCreate(FacultyBase):
    unavailable_slot_ids: list[int] = []


class FacultyUpdate(SQLModel):
    code: Optional[str] = None
    name: Optional[str] = None
    max_load_hours_per_week: Optional[int] = None
    max_consecutive_sessions: Optional[int] = None
    unavailable_slot_ids: Optional[list[int]] = None


# --------------------------------------------------------------------------- course (branch-scoped)
class CourseBase(SQLModel):
    branch_id: int = Field(foreign_key="branch.id", index=True)
    code: str = Field(index=True)
    title: str
    credits: int = 3
    category: str = "Major"         # CourseCategory value
    theory_per_week: int = 0
    practical_per_week: int = 0
    tutorial_per_week: int = 0
    is_heavy: bool = False


class Course(CourseBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class CourseCreate(CourseBase):
    pass


class CourseUpdate(SQLModel):
    code: Optional[str] = None
    title: Optional[str] = None
    credits: Optional[int] = None
    category: Optional[str] = None
    theory_per_week: Optional[int] = None
    practical_per_week: Optional[int] = None
    tutorial_per_week: Optional[int] = None
    is_heavy: Optional[bool] = None


# --------------------------------------------------------------------------- allocation
class AllocationBase(SQLModel):
    division_id: int = Field(foreign_key="division.id", index=True)
    course_id: int = Field(foreign_key="course.id", index=True)
    faculty_id: Optional[int] = Field(default=None, foreign_key="faculty.id")
    batch1_faculty_id: Optional[int] = Field(default=None, foreign_key="faculty.id")
    batch2_faculty_id: Optional[int] = Field(default=None, foreign_key="faculty.id")


class Allocation(AllocationBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class AllocationCreate(AllocationBase):
    pass


class AllocationUpdate(SQLModel):
    faculty_id: Optional[int] = None
    batch1_faculty_id: Optional[int] = None
    batch2_faculty_id: Optional[int] = None


# --------------------------------------------------------------------------- room (global)
class RoomBase(SQLModel):
    code: str = Field(index=True)
    name: str
    capacity: int = 60
    room_type: str = "classroom"    # "classroom" | "lab"


class Room(RoomBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class RoomCreate(RoomBase):
    pass


class RoomUpdate(SQLModel):
    code: Optional[str] = None
    name: Optional[str] = None
    capacity: Optional[int] = None
    room_type: Optional[str] = None


# --------------------------------------------------------------------------- slot_template (global weekly grid)
class SlotTemplateBase(SQLModel):
    day: int                        # 0=Mon .. 4=Fri
    period: int                     # 0-based within the day
    start: str
    end: str


class SlotTemplate(SlotTemplateBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)


class SlotTemplateCreate(SlotTemplateBase):
    pass


# --------------------------------------------------------------------------- timetable_run (P2)
class TimetableRun(SQLModel, table=True):
    """A generate job record (design.md §4.2). `problem_snapshot` makes the run reproducible
    (and is what `disruption.replan` will consume once P4's persistence lands); `solution`/
    `grids`/`stage_reports` are populated by `webapp.jobs.run_generation` once the background
    solve finishes. `status` moves queued -> running -> done/failed."""

    id: Optional[int] = Field(default=None, primary_key=True)
    label: str = ""
    solver: str
    time_limit: float
    status: str = "queued"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    problem_snapshot: dict = Field(default_factory=dict, sa_column=Column(JSON))
    solution: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    grids: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    stage_reports: Optional[list] = Field(default=None, sa_column=Column(JSON))
    hard: Optional[int] = None
    soft: Optional[float] = None
    error: Optional[str] = None

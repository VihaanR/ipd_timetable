"""Synthetic problem-instance generation, plus the loader for the real DJSCE reference dataset."""
from __future__ import annotations

import random
from pathlib import Path

from timetable.io_json import load_problem
from timetable.models import (
    Course, CourseCategory, Division, Faculty, ProblemInstance, ProgramType, Room, TimeSlot,
)

REFERENCE_DATASET_PATH = Path(__file__).resolve().parent.parent / "data" / "reference" / "djsce_cse_ds_sy_sem4.json"

_SCALE_CONFIG = {
    # periods_per_day=8 everywhere (matches the real reference dataset): 1 slot reserved for the
    # mandatory break leaves 7 teaching slots/day, comfortably able to satisfy the 6-8 hr/day
    # hard constraint. Scale only varies division count and room/faculty pool size.
    "small": dict(divisions=2, periods_per_day=8, classrooms=2, labs=2, faculty_pool=6),
    "medium": dict(divisions=6, periods_per_day=8, classrooms=4, labs=4, faculty_pool=14),
    "large": dict(divisions=15, periods_per_day=8, classrooms=8, labs=8, faculty_pool=30),
}


def _course_template() -> list[Course]:
    """Same subject mix as the real DJSCE CSE-DS Sem IV curriculum, used to keep synthetic data
    structurally consistent with the reference dataset."""
    return [
        Course(code="DS", title="Data Structures", credits=4, category=CourseCategory.MAJOR,
               theory_sessions_per_week=4, practical_sessions_per_week=1, is_heavy=True),
        Course(code="ML-I", title="Machine Learning - I", credits=4, category=CourseCategory.MAJOR,
               theory_sessions_per_week=3, practical_sessions_per_week=1, is_heavy=True),
        Course(code="SDS", title="Statistics for Data Science", credits=3, category=CourseCategory.MAJOR,
               theory_sessions_per_week=4, practical_sessions_per_week=1, is_heavy=True),
        Course(code="WE", title="Web Engineering Laboratory", credits=2, category=CourseCategory.SKILL,
               practical_sessions_per_week=2),
        Course(code="PBC", title="Professional and Business Communication", credits=1,
               category=CourseCategory.AEC, tutorial_sessions_per_week=1),
        Course(code="EFM", title="Economics and Financial Management", credits=3,
               category=CourseCategory.MINOR, theory_sessions_per_week=3),
        Course(code="CMPM", title="Computational Methods and Pricing Models", credits=3,
               category=CourseCategory.VAC, theory_sessions_per_week=4, practical_sessions_per_week=1),
        Course(code="OE", title="Open Elective", credits=2, category=CourseCategory.OPEN_ELECTIVE,
               theory_sessions_per_week=2),
    ]


def generate_sample_instance(scale: str = "small", seed: int = 42) -> ProblemInstance:
    if scale not in _SCALE_CONFIG:
        raise ValueError(f"Unknown scale {scale!r}, expected one of {list(_SCALE_CONFIG)}")
    cfg = _SCALE_CONFIG[scale]
    rng = random.Random(seed)

    days = 5
    time_slots = [
        TimeSlot(id=day * cfg["periods_per_day"] + period, day=day, period=period,
                  start=f"{8 + period:02d}:00", end=f"{9 + period:02d}:00")
        for day in range(days) for period in range(cfg["periods_per_day"])
    ]

    rooms = [Room(id=f"CR{i}", name=f"Classroom {i}", capacity=70, room_type="classroom")
             for i in range(1, cfg["classrooms"] + 1)]
    rooms += [Room(id=f"L{i}", name=f"Lab {i}", capacity=35, room_type="lab")
              for i in range(1, cfg["labs"] + 1)]

    max_load = 24
    faculty_pool = [Faculty(id=f"F{i}", name=f"Faculty {i}", max_load_hours_per_week=max_load)
                    for i in range(1, cfg["faculty_pool"] + 1)]
    faculty_ids = [f.id for f in faculty_pool]

    courses = _course_template()
    course_codes = tuple(c.code for c in courses)
    courses_by_code = {c.code: c for c in courses}

    # Load-balanced assignment via global largest-first (LPT) bin-packing: gather every
    # (division, course) assignment as one flat task list across the WHOLE institution, sort by
    # hours descending, and assign each task to the currently least-loaded eligible faculty.
    # Doing this globally (not division-by-division) is what keeps everyone under
    # max_load_hours_per_week even at large scale -- a division-local greedy pass runs out of
    # "safe" local candidates well before the pool's aggregate capacity is exhausted, silently
    # overloading people (a real bug this replaced: confirmed via `research/REPORT.md`'s
    # scalability check, medium/large scale were structurally INFEASIBLE for MIP/CP-SAT because
    # 7-25 of the faculty pool were pushed past their weekly cap by the old division-local pass).
    # Practical (lab) courses need TWO distinct instructors, one per batch, since both batches run
    # simultaneously in different rooms and a single person cannot supervise both at once. Open
    # Electives are cross-division synced to the same time slot (constraint 12), so no two
    # divisions may share the same OE instructor -- handled first, as the tightest constraint.
    faculty_load = {fid: 0 for fid in faculty_ids}
    division_ids = [f"D{i}" for i in range(1, cfg["divisions"] + 1)]
    faculty_by_course_by_division: dict[str, dict[str, str | tuple[str, str]]] = {d: {} for d in division_ids}

    def _least_loaded(pool: list[str]) -> str:
        least = min(faculty_load[fid] for fid in pool)
        return rng.choice([fid for fid in pool if faculty_load[fid] == least])

    def _eligible_pool(hours: float, exclude: frozenset[str]) -> list[str]:
        eligible = [fid for fid in faculty_ids if fid not in exclude and faculty_load[fid] + hours <= max_load]
        return eligible or [fid for fid in faculty_ids if fid not in exclude] or faculty_ids

    oe_code = next((c.code for c in courses if c.category == CourseCategory.OPEN_ELECTIVE), None)
    if oe_code:
        oe_course = courses_by_code[oe_code]
        oe_hours = oe_course.theory_sessions_per_week + oe_course.tutorial_sessions_per_week
        used: set[str] = set()
        for div_id in division_ids:
            chosen = _least_loaded(_eligible_pool(oe_hours, frozenset(used)))
            used.add(chosen)
            faculty_load[chosen] += oe_hours
            faculty_by_course_by_division[div_id][oe_code] = chosen

    tasks = []  # (hours, division_id, course_code) for every remaining course, largest first
    for code in course_codes:
        if code == oe_code:
            continue
        course = courses_by_code[code]
        lab_hours = course.practical_sessions_per_week * 2
        hours = course.theory_sessions_per_week + course.tutorial_sessions_per_week + lab_hours
        for div_id in division_ids:
            tasks.append((hours, div_id, code))
    tasks.sort(key=lambda t: -t[0])

    for hours, div_id, code in tasks:
        course = courses_by_code[code]
        if course.practical_sessions_per_week > 0:
            lab_hours = course.practical_sessions_per_week * 2
            lead = _least_loaded(_eligible_pool(hours, frozenset()))
            faculty_load[lead] += hours
            co_instructor = _least_loaded(_eligible_pool(lab_hours, frozenset({lead})))
            faculty_load[co_instructor] += lab_hours
            faculty_by_course_by_division[div_id][code] = (lead, co_instructor)
        else:
            chosen = _least_loaded(_eligible_pool(hours, frozenset()))
            faculty_load[chosen] += hours
            faculty_by_course_by_division[div_id][code] = chosen

    divisions = []
    for div_id in division_ids:
        divisions.append(Division(
            id=div_id, program=ProgramType.FYUP, semester=4, student_count=rng.randint(50, 70),
            course_codes=course_codes, faculty_by_course=faculty_by_course_by_division[div_id],
            batches=(f"{div_id}1", f"{div_id}2"),
        ))

    return ProblemInstance(
        time_slots=time_slots, rooms=rooms, faculty=faculty_pool, courses=courses,
        divisions=divisions, days_per_week=days,
        protected_notes=["Saturday reserved for project/research work (not modeled as time slots)."],
    )


def load_reference_instance() -> ProblemInstance:
    """Loads the real DJSCE CSE-DS SY Sem IV (D1/D2/D3) dataset transcribed from the team's own
    reference timetables. See data/reference/djsce_cse_ds_sy_sem4.json for provenance notes."""
    return load_problem(REFERENCE_DATASET_PATH)

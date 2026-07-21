"""Disruption engine (design.md §7) — the "rain day / holiday" re-plan flow.

When part of a day becomes unusable (sudden rain from 2 pm, a declared holiday), we do NOT
regenerate the whole week — that would move classes students and faculty have already planned
around. Instead we make a **minimal-change** adjustment:

  1. Every session on an UNDISRUPTED day is copied verbatim from the baseline — identical slot AND
     room. Those days are untouched, guaranteed.
  2. On the disrupted day, sessions whose baseline slot is NOT in the blocked window (the surviving
     morning classes, say) keep their exact placement.
  3. Sessions whose baseline slot IS blocked are displaced. We try to relocate each into a free,
     non-blocked slot on the same day (respecting no double-booking, batch-pair sync, subject-
     once-per-day). Any that don't fit are **dropped** for that occurrence — you cannot make up a
     rained-out afternoon by overloading another day past its 6-8h hard cap, so a genuinely lost
     session is reported as dropped rather than silently forced somewhere invalid.

This is built directly (not via the exact MIP/CP-SAT solvers) on purpose: the exact solvers force
*every* session to be placed (`AddExactlyOne`), which makes "some classes are simply cancelled by
the rain" an infeasible model. Direct construction is always feasible, deterministic, and truly
minimal-change. The master run is never mutated — the caller stores this as a dated overlay.

The engine-level `blocked_slot_ids` / `relaxed_days` / `pinned_slots` support added alongside this
module still lets an admin manually re-solve a blocked-window scenario with any solver if they want
a full re-optimization instead of a minimal-change patch.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from timetable.models import (
    Assignment, ProblemInstance, SessionRequirement, Solution, expand_requirements,
)
from timetable.scoring import score
from timetable.solvers.greedy import (
    NO_ROOM, _Trackers, _consecutive_slot_ids, _rooms_of_type, _slots_by_day,
)


@dataclass
class MovedSession:
    session_id: str
    from_slot_id: int | None
    to_slot_id: int | None
    from_room_id: str | None
    to_room_id: str | None


@dataclass
class AdjustmentResult:
    solution: Solution
    affected_slot_ids: frozenset[int]
    relaxed_days: frozenset[int]
    moved_sessions: list[MovedSession]
    dropped_session_ids: list[str]
    hard_violations: int          # includes the dropped sessions (each is an unmet weekly requirement)
    soft_cost: float
    notes: list[str] = field(default_factory=list)

    @property
    def conflict_violations(self) -> int:
        """Hard violations among the sessions that ARE placed — i.e. genuine scheduling clashes,
        excluding the intentional rained-out drops. A well-formed adjustment has this == 0."""
        return self.hard_violations - len(self.dropped_session_ids)

    @property
    def is_valid(self) -> bool:
        """True when every placed session is conflict-free (only dropped sessions are 'unmet')."""
        return self.conflict_violations == 0


def affected_slots_for_day(problem: ProblemInstance, day: int,
                           from_period: int | None = None) -> frozenset[int]:
    """Slot ids to block on a disrupted day. `from_period=None` blocks the whole day (holiday);
    `from_period=N` blocks periods >= N (e.g. 'rain from 2 pm')."""
    return frozenset(
        t.id for t in problem.time_slots
        if t.day == day and (from_period is None or t.period >= from_period)
    )


def _occupied_slot_ids(slots_by_id, slots_by_day_period, slot_id: int, duration_slots: int) -> list[int]:
    ts = slots_by_id.get(slot_id)
    if ts is None:
        return []
    ids = [ts.id]
    for k in range(1, duration_slots):
        nxt = slots_by_day_period.get((ts.day, ts.period + k))
        if nxt is not None:
            ids.append(nxt.id)
    return ids


def replan(problem: ProblemInstance, baseline: Solution, affected_slot_ids: frozenset[int],
           relaxed_days: frozenset[int] | None = None, time_limit_s: float = 30) -> AdjustmentResult:
    """Minimal-change re-plan. `problem` is the baseline snapshot; `baseline` is its solution.
    Returns an overlay adjustment; neither `problem` nor `baseline` is mutated. `time_limit_s` is
    accepted for API symmetry with the solvers but the direct construction is effectively instant."""
    affected = frozenset(affected_slot_ids)
    requirements = expand_requirements(problem)
    req_by_id = {r.id: r for r in requirements}
    baseline_by_session = baseline.assignment_by_session()

    slots_by_id = {t.id: t for t in problem.time_slots}
    slots_by_day_period = {(t.day, t.period): t for t in problem.time_slots}
    slots_by_day = _slots_by_day(problem)
    classrooms = _rooms_of_type(problem, "classroom")
    labs = _rooms_of_type(problem, "lab")

    disrupted_days = frozenset(relaxed_days) if relaxed_days else frozenset(
        slots_by_id[s].day for s in affected if s in slots_by_id
    )

    trackers = _Trackers(problem)
    result: list[Assignment] = []
    notes: list[str] = []

    def _occ(slot_id: int, dur: int) -> list[int]:
        return _occupied_slot_ids(slots_by_id, slots_by_day_period, slot_id, dur)

    def _commit(req: SessionRequirement, slot_id: int, room_id: str) -> None:
        occ = _occ(slot_id, req.duration_slots)
        ts = slots_by_id[slot_id]
        trackers.commit(req.division_id, req.batch_id, req.faculty_id, room_id, ts.day, occ, req.duration_slots)
        if not req.is_break:
            trackers.division_day_occurrence.add((req.division_id, req.course_code, ts.day))
        result.append(Assignment(session_id=req.id, time_slot_id=slot_id, room_id=room_id))

    # ---- 1. copy everything on undisrupted days verbatim (identical slot AND room) ----
    disrupted_reqs: list[SessionRequirement] = []
    for req in requirements:
        base = baseline_by_session.get(req.id)
        if base is None:
            continue
        ts = slots_by_id.get(base.time_slot_id)
        if ts is None:
            continue
        if ts.day in disrupted_days:
            disrupted_reqs.append(req)
        else:
            _commit(req, base.time_slot_id, base.room_id)

    # ---- 2. disrupted-day survivors (baseline slot not blocked) keep their exact placement ----
    displaced: list[SessionRequirement] = []
    displaced_groups: dict[str, list[SessionRequirement]] = {}
    for req in disrupted_reqs:
        base = baseline_by_session[req.id]
        occ = _occ(base.time_slot_id, req.duration_slots)
        if any(sid in affected for sid in occ):
            if req.batch_group_id:
                displaced_groups.setdefault(req.batch_group_id, []).append(req)
            else:
                displaced.append(req)
        else:
            _commit(req, base.time_slot_id, base.room_id)

    # ---- 3. try to relocate displaced sessions into free non-blocked slots on the same day ----
    dropped: list[str] = []

    def _free_room(room_pool: list[str], occ: list[int], used: set[str]) -> str | None:
        for r in room_pool:
            if r in used:
                continue
            if all((r, sid) not in trackers.room_busy for sid in occ):
                return r
        return None

    def _try_place_single(req: SessionRequirement) -> bool:
        base = baseline_by_session[req.id]
        day = slots_by_id[base.time_slot_id].day
        room_pool = labs if req.room_type == "lab" else classrooms
        for ts in slots_by_day.get(day, []):
            occ = _occ(ts.id, req.duration_slots)
            if len(occ) < req.duration_slots:
                continue
            if any(sid in affected for sid in occ):
                continue
            if (req.division_id, req.course_code, day) in trackers.division_day_occurrence:
                continue
            if not trackers.division_free(req.division_id, req.batch_id, occ):
                continue
            if not trackers.faculty_free(req.faculty_id, day, occ, req.duration_slots):
                continue
            if req.room_type == "none":
                _commit(req, ts.id, NO_ROOM)
                return True
            room_id = _free_room(room_pool, occ, set())
            if room_id is not None:
                _commit(req, ts.id, room_id)
                return True
        return False

    def _try_place_batch_group(reqs: list[SessionRequirement]) -> bool:
        base = baseline_by_session[reqs[0].id]
        day = slots_by_id[base.time_slot_id].day
        for ts in slots_by_day.get(day, []):
            occ = _occ(ts.id, reqs[0].duration_slots)
            if len(occ) < reqs[0].duration_slots:
                continue
            if any(sid in affected for sid in occ):
                continue
            if (reqs[0].division_id, reqs[0].course_code, day) in trackers.division_day_occurrence:
                continue
            if not all(trackers.division_free(r.division_id, r.batch_id, occ) for r in reqs):
                continue
            if not all(trackers.faculty_free(r.faculty_id, day, occ, r.duration_slots) for r in reqs):
                continue
            free_labs = [l for l in labs if all((l, sid) not in trackers.room_busy for sid in occ)]
            if len(free_labs) < len(reqs):
                continue
            for r, room_id in zip(reqs, free_labs):
                _commit(r, ts.id, room_id)
            return True
        return False

    for req in displaced:
        if not _try_place_single(req):
            dropped.append(req.id)
    for group_id, reqs in displaced_groups.items():
        if not _try_place_batch_group(reqs):
            dropped.extend(r.id for r in reqs)

    if dropped:
        notes.append(f"{len(dropped)} session(s) could not be re-placed within the disrupted day "
                     f"and were dropped for this occurrence (rained out).")

    # ---- 4. score the overlay (blocked+relaxed context) and diff vs baseline ----
    disrupted = replace(problem, blocked_slot_ids=affected, relaxed_days=disrupted_days)
    solution = Solution(assignments=result, solver_name="disruption-replan",
                        wall_clock_seconds=0.0, status="ADJUSTED")
    sc = score(solution, disrupted)

    new_by_session = solution.assignment_by_session()
    moved: list[MovedSession] = []
    for req in requirements:
        base = baseline_by_session.get(req.id)
        new = new_by_session.get(req.id)
        if new is None and base is not None:
            moved.append(MovedSession(req.id, base.time_slot_id, None, base.room_id, None))
        elif new is not None and base is None:
            moved.append(MovedSession(req.id, None, new.time_slot_id, None, new.room_id))
        elif new is not None and base is not None and (
                base.time_slot_id != new.time_slot_id or base.room_id != new.room_id):
            moved.append(MovedSession(req.id, base.time_slot_id, new.time_slot_id,
                                      base.room_id, new.room_id))

    return AdjustmentResult(
        solution=solution,
        affected_slot_ids=affected,
        relaxed_days=disrupted_days,
        moved_sessions=moved,
        dropped_session_ids=dropped,
        hard_violations=sc.hard_violations,
        soft_cost=sc.soft_cost,
        notes=notes,
    )

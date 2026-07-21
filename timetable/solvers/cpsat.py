"""CP-SAT solver -- Danish's approach.

Uses `ortools.sat.python.cp_model`. Shares the same candidate-pruning and hard-constraint set as
the MIP solver (via `timetable.solvers.candidates`) so the two are judged on equal footing, but
adds a genuinely richer objective CP-SAT can express natively that MIP's linear-sum formulation
cannot: per-(division, day) gap-minimization via reified "occupied at period p" booleans and
AddMaxEquality-based before/after indicators. This -- alongside AddNoOverlap-style reasoning being
unnecessary here since occupancy is already tracked per slot -- is the concrete mechanism behind
CP-SAT's quality edge in the team's own benchmark (best soft-cost, longest runtime).
"""
from __future__ import annotations

import time

from ortools.sat.python import cp_model

from timetable.models import (
    Assignment, CourseCategory, ProblemInstance, Solution, SessionType, expand_requirements,
)
from timetable.solvers.base import SolverBase
from timetable.solvers.candidates import (
    NO_ROOM, batch_group_members, build_candidates, slots_by_day, sync_group_members,
)


class CPSATSolver(SolverBase):
    name = "cpsat"

    def solve(self, problem: ProblemInstance, time_limit_s: float = 300,
              warm_start: Solution | None = None) -> Solution:
        start_time = time.time()
        model = cp_model.CpModel()

        requirements = expand_requirements(problem)
        candidates = build_candidates(problem, requirements)
        days = slots_by_day(problem)
        faculty_by_id = problem.faculty_by_id()
        divisions_by_id = problem.division_by_id()
        courses_by_code = problem.course_by_code()
        rooms_by_id = problem.room_by_id()
        batch_groups = batch_group_members(requirements)
        sync_groups = sync_group_members(requirements)

        x: dict[tuple[str, int, str], cp_model.IntVar] = {}
        for req in requirements:
            for (start_id, _occ, _day, room_id) in candidates[req.id]:
                x[(req.id, start_id, room_id)] = model.NewBoolVar(f"x_{req.id}_{start_id}_{room_id}")

        for req in requirements:
            cands = candidates[req.id]
            if not cands:
                continue
            model.AddExactlyOne(x[(req.id, s, r)] for (s, _, _, r) in cands)

        def _accumulate(bucket: dict, key, var):
            bucket.setdefault(key, []).append(var)

        room_terms: dict = {}
        faculty_terms: dict = {}
        whole_div_terms: dict = {}
        batch_terms: dict = {}
        for req in requirements:
            division = divisions_by_id.get(req.division_id)
            batches = division.batch_pair() if division else ()
            for (start_id, occ_ids, day, room_id) in candidates[req.id]:
                var = x[(req.id, start_id, room_id)]
                if room_id != NO_ROOM:
                    for sid in occ_ids:
                        _accumulate(room_terms, (room_id, sid), var)
                if req.faculty_id:
                    for sid in occ_ids:
                        _accumulate(faculty_terms, (req.faculty_id, sid), var)
                for sid in occ_ids:
                    if req.batch_id is None:
                        _accumulate(whole_div_terms, (req.division_id, sid), var)
                        for b in batches:
                            _accumulate(batch_terms, (req.division_id, b, sid), var)
                    else:
                        _accumulate(batch_terms, (req.division_id, req.batch_id, sid), var)

        for vars_ in room_terms.values():
            model.AddAtMostOne(vars_)
        for vars_ in faculty_terms.values():
            model.AddAtMostOne(vars_)
        for vars_ in whole_div_terms.values():
            model.AddAtMostOne(vars_)
        for vars_ in batch_terms.values():
            model.AddAtMostOne(vars_)

        # subject at most once per day per division (batch-pair counted once via representative)
        seen_groups: set[str] = set()
        day_subject_terms: dict = {}
        for req in requirements:
            if req.is_break:
                continue
            if req.batch_group_id:
                if req.batch_group_id in seen_groups:
                    continue
                seen_groups.add(req.batch_group_id)
            for (start_id, _occ, day, room_id) in candidates[req.id]:
                _accumulate(day_subject_terms, (req.division_id, req.course_code, day), x[(req.id, start_id, room_id)])
        for vars_ in day_subject_terms.values():
            model.AddAtMostOne(vars_)

        # batch-pair: same slot (different room already guaranteed by candidate pruning)
        for group_id, members in batch_groups.items():
            anchor, others = members[0], members[1:]
            for other in others:
                slot_ids = {s for (s, _, _, _) in candidates[anchor.id]} | {s for (s, _, _, _) in candidates[other.id]}
                for slot_id in slot_ids:
                    anchor_terms = [x[(anchor.id, s, r)] for (s, _, _, r) in candidates[anchor.id] if s == slot_id]
                    other_terms = [x[(other.id, s, r)] for (s, _, _, r) in candidates[other.id] if s == slot_id]
                    model.Add(sum(anchor_terms) == sum(other_terms))

        # cross-division sync (open elective): same slot across all linked divisions
        for group_id, members in sync_groups.items():
            anchor, others = members[0], members[1:]
            for other in others:
                slot_ids = {s for (s, _, _, _) in candidates[anchor.id]} | {s for (s, _, _, _) in candidates[other.id]}
                for slot_id in slot_ids:
                    anchor_terms = [x[(anchor.id, s, r)] for (s, _, _, r) in candidates[anchor.id] if s == slot_id]
                    other_terms = [x[(other.id, s, r)] for (s, _, _, r) in candidates[other.id] if s == slot_id]
                    model.Add(sum(anchor_terms) == sum(other_terms))

        # faculty daily (<=6h, matching the softened NEP institutional-norms reading) / weekly load cap
        faculty_day_terms: dict = {}
        faculty_week_terms: dict = {}
        for req in requirements:
            if not req.faculty_id:
                continue
            for (start_id, _occ, day, room_id) in candidates[req.id]:
                var = x[(req.id, start_id, room_id)]
                faculty_day_terms.setdefault((req.faculty_id, day), []).append((var, req.duration_slots))
                faculty_week_terms.setdefault(req.faculty_id, []).append((var, req.duration_slots))
        for terms in faculty_day_terms.values():
            model.Add(sum(v * d for v, d in terms) <= 6)
        for fid, terms in faculty_week_terms.items():
            fac = faculty_by_id.get(fid)
            cap = fac.max_load_hours_per_week if fac else 20
            model.Add(sum(v * d for v, d in terms) <= cap)

        # no more than max_consecutive_sessions in a row for any faculty
        for fid in {r.faculty_id for r in requirements if r.faculty_id}:
            fac = faculty_by_id.get(fid)
            cap = fac.max_consecutive_sessions if fac else 2
            for day, day_slots in days.items():
                for start in range(len(day_slots) - cap):
                    window = day_slots[start:start + cap + 1]
                    window_terms = []
                    for ts in window:
                        window_terms.extend(faculty_terms.get((fid, ts.id), []))
                    if window_terms:
                        model.Add(sum(window_terms) <= cap)

        # division daily load in [6, 8] hours (batch pairs counted once)
        seen_groups2: set[str] = set()
        div_day_terms: dict = {}
        for req in requirements:
            if req.is_break:
                continue
            if req.batch_group_id:
                if req.batch_group_id in seen_groups2:
                    continue
                seen_groups2.add(req.batch_group_id)
            for (start_id, _occ, day, room_id) in candidates[req.id]:
                var = x[(req.id, start_id, room_id)]
                div_day_terms.setdefault((req.division_id, day), []).append((var, req.duration_slots))
        # a relaxed (disrupted) day is exempt from the day-shaped hard rules -- see scoring.py
        relaxed = problem.relaxed_days
        for (division_id, day), terms in div_day_terms.items():
            if day in relaxed:
                continue
            expr = sum(v * d for v, d in terms)
            model.Add(expr >= 6)
            model.Add(expr <= 8)

        # each division's day must START at the first period (08:00) -- no empty leading slot
        first_slot_id_by_day = {day: ds[0].id for day, ds in days.items() if ds}
        first_slot_terms: dict = {}
        for req in requirements:
            if req.is_break:
                continue
            for (start_id, _occ, day, room_id) in candidates[req.id]:
                if start_id == first_slot_id_by_day.get(day):
                    first_slot_terms.setdefault((req.division_id, day), []).append(x[(req.id, start_id, room_id)])
        for (division_id, day), terms in first_slot_terms.items():
            if day in relaxed:
                continue
            model.Add(sum(terms) >= 1)

        # no all-theory day: each day must have >=1 practical/skill session, for divisions that offer one
        practical_or_skill_terms: dict = {}
        divisions_with_practical: set[str] = set()
        for req in requirements:
            if req.is_break:
                continue
            course = courses_by_code.get(req.course_code)
            if not course:
                continue
            if req.session_type == SessionType.PRACTICAL or course.category == CourseCategory.SKILL:
                divisions_with_practical.add(req.division_id)
                for (start_id, _occ, day, room_id) in candidates[req.id]:
                    practical_or_skill_terms.setdefault((req.division_id, day), []).append(
                        x[(req.id, start_id, room_id)])
        for division in problem.divisions:
            if division.id not in divisions_with_practical:
                continue
            for day in days:
                if day in relaxed:
                    continue
                terms = practical_or_skill_terms.get((division.id, day), [])
                if terms:
                    model.Add(sum(terms) >= 1)

        # ---- objective ----
        objective_terms = []
        for req in requirements:
            division = divisions_by_id.get(req.division_id)
            occupants = (division.student_count // 2) if (req.batch_id and division) else (division.student_count if division else 0)
            for (start_id, _occ, day, room_id) in candidates[req.id]:
                var = x[(req.id, start_id, room_id)]
                if room_id != NO_ROOM:
                    room = rooms_by_id[room_id]
                    waste = max(0, room.capacity - occupants)
                    if waste:
                        objective_terms.append(int(waste * 10) * var)  # scaled to stay integral
                if req.session_type == SessionType.PRACTICAL:
                    day_slots = days[day]
                    ts = next(t for t in day_slots if t.id == start_id)
                    if len(day_slots) >= 2 and ts.period in {day_slots[-1].period, day_slots[-2].period}:
                        objective_terms.append(200 * var)
                if req.is_break:
                    # nudge toward a natural mid-morning slot rather than merely "anywhere legal"
                    day_slots = days[day]
                    ts = next(t for t in day_slots if t.id == start_id)
                    target_period = day_slots[0].period + max(1, round(len(day_slots) * 0.35))
                    dist = abs(ts.period - target_period)
                    if dist:
                        objective_terms.append(15 * dist * var)

        # native gap-minimization: penalize periods that sit strictly between two occupied
        # periods on the same (division, day) but are themselves free. A break IS occupied for
        # this purpose (it's a real scheduled event) -- excluding it would incentivize the solver
        # to shove the break to the day's edge just to dodge the gap penalty, instead of a
        # natural mid-day placement.
        GAP_WEIGHT = 50
        for division in problem.divisions:
            for day, day_slots in days.items():
                occupied_by_period: dict[int, list] = {ts.period: [] for ts in day_slots}
                for req in requirements:
                    if req.division_id != division.id:
                        continue
                    for (start_id, occ_ids, d, room_id) in candidates[req.id]:
                        if d != day:
                            continue
                        var = x[(req.id, start_id, room_id)]
                        for sid in occ_ids:
                            ts = next((t for t in day_slots if t.id == sid), None)
                            if ts:
                                occupied_by_period[ts.period].append(var)

                occupied_bool = {}
                for period, terms in occupied_by_period.items():
                    b = model.NewBoolVar(f"occ_{division.id}_{day}_{period}")
                    if terms:
                        model.AddMaxEquality(b, terms)
                    else:
                        model.Add(b == 0)
                    occupied_bool[period] = b

                periods_sorted = sorted(occupied_bool.keys())
                for i, period in enumerate(periods_sorted):
                    if i == 0 or i == len(periods_sorted) - 1:
                        continue
                    before = model.NewBoolVar(f"before_{division.id}_{day}_{period}")
                    after = model.NewBoolVar(f"after_{division.id}_{day}_{period}")
                    model.AddMaxEquality(before, [occupied_bool[p] for p in periods_sorted[:i]])
                    model.AddMaxEquality(after, [occupied_bool[p] for p in periods_sorted[i + 1:]])
                    gap = model.NewBoolVar(f"gap_{division.id}_{day}_{period}")
                    model.Add(gap <= before)
                    model.Add(gap <= after)
                    model.Add(gap <= 1 - occupied_bool[period])
                    model.Add(gap >= before + after - occupied_bool[period] - 1)
                    objective_terms.append(GAP_WEIGHT * gap)

        if objective_terms:
            model.Minimize(sum(objective_terms))

        if warm_start is not None:
            assigned = warm_start.assignment_by_session()
            for req in requirements:
                a = assigned.get(req.id)
                if a is None:
                    continue
                key = (req.id, a.time_slot_id, a.room_id)
                if key in x:
                    model.AddHint(x[key], 1)  # this ortools build's AddHint takes one (var, value) at a time

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_search_workers = 8
        status = solver.Solve(model)
        elapsed = time.time() - start_time

        status_map = {
            cp_model.OPTIMAL: "OPTIMAL",
            cp_model.FEASIBLE: "FEASIBLE",
            cp_model.INFEASIBLE: "INFEASIBLE",
        }
        status_name = status_map.get(status, "TIMEOUT")

        assignments: list[Assignment] = []
        if status_name in ("OPTIMAL", "FEASIBLE"):
            for req in requirements:
                for (start_id, _occ, _day, room_id) in candidates[req.id]:
                    if solver.Value(x[(req.id, start_id, room_id)]) == 1:
                        assignments.append(Assignment(session_id=req.id, time_slot_id=start_id, room_id=room_id))
                        break

        return Solution(
            assignments=assignments, solver_name=self.name, wall_clock_seconds=elapsed,
            objective_value=solver.ObjectiveValue() if status_name in ("OPTIMAL", "FEASIBLE") else None,
            status=status_name,
        )

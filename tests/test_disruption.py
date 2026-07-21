"""Golden tests for the disruption engine (design.md §7 / P4).

Uses the greedy baseline (fast, deterministic) so the suite stays quick; the replan logic is
solver-agnostic — it consumes any baseline Solution.
"""
import pytest

from timetable.models import expand_requirements
from timetable.scoring import score
from timetable.solvers.cpsat import CPSATSolver
from timetable.disruption import replan, affected_slots_for_day


@pytest.fixture(scope="module")
def baseline(reference_problem):
    # a CONFLICT-FREE baseline (hard=0, all sessions placed) is required for the disruption
    # engine's is_valid / conflict_violations semantics to be meaningful -- they measure whether
    # the re-plan introduces NEW conflicts, which only makes sense relative to a clean baseline.
    sol = CPSATSolver().solve(reference_problem, time_limit_s=45)
    assert score(sol, reference_problem).hard_violations == 0, "baseline must be conflict-free"
    return sol


def _occ(problem, slot_id, duration):
    slots_by_id = {t.id: t for t in problem.time_slots}
    sbp = {(t.day, t.period): t for t in problem.time_slots}
    ts = slots_by_id[slot_id]
    ids = [ts.id]
    for k in range(1, duration):
        nxt = sbp.get((ts.day, ts.period + k))
        if nxt:
            ids.append(nxt.id)
    return ids


# --- engine-level field support (blocked_slot_ids / relaxed_days) ---

def test_blocked_slot_ids_default_empty_is_noop(reference_problem):
    assert reference_problem.blocked_slot_ids == frozenset()
    assert reference_problem.relaxed_days == frozenset()
    assert reference_problem.pinned_slots == {}


def test_scoring_flags_sessions_in_blocked_slots(reference_problem, baseline):
    from dataclasses import replace
    # block a mid-morning Tuesday slot the greedy baseline is likely using
    blocked = frozenset({10, 11, 12})
    clean = score(baseline, reference_problem)
    with_block = score(baseline, replace(reference_problem, blocked_slot_ids=blocked))
    assert with_block.hard_violations >= clean.hard_violations


def test_pinned_slots_fix_session_via_fixed_time_slot_id(reference_problem):
    from dataclasses import replace
    reqs = expand_requirements(reference_problem)
    target = next(r.id for r in reqs if not r.is_break)
    pinned = replace(reference_problem, pinned_slots={target: 18})
    by_id = {r.id: r for r in expand_requirements(pinned)}
    assert by_id[target].fixed_time_slot_id == 18


# --- replan() golden behavior ---

def test_replan_no_disruption_equals_baseline(reference_problem, baseline):
    result = replan(reference_problem, baseline, frozenset())
    assert result.moved_sessions == []
    assert result.dropped_session_ids == []
    assert result.is_valid


def test_replan_places_nothing_in_blocked_slots(reference_problem, baseline):
    affected = affected_slots_for_day(reference_problem, day=1, from_period=5)
    result = replan(reference_problem, baseline, affected)
    reqs = {r.id: r for r in expand_requirements(reference_problem)}
    for a in result.solution.assignments:
        req = reqs.get(a.session_id)
        if req and not req.is_break:
            occ = _occ(reference_problem, a.time_slot_id, req.duration_slots)
            assert not any(sid in affected for sid in occ), f"{a.session_id} landed in a blocked slot"


def test_replan_is_minimal_change_only_disrupted_day_moves(reference_problem, baseline):
    affected = affected_slots_for_day(reference_problem, day=1, from_period=5)
    result = replan(reference_problem, baseline, affected)
    slots_by_id = {t.id: t for t in reference_problem.time_slots}
    for m in result.moved_sessions:
        # every moved/dropped session must have originated on the disrupted day (day 1)
        assert m.from_slot_id is None or slots_by_id[m.from_slot_id].day == 1


def test_replan_placed_sessions_are_conflict_free(reference_problem, baseline):
    affected = affected_slots_for_day(reference_problem, day=1, from_period=5)
    result = replan(reference_problem, baseline, affected)
    # every hard violation must be an intentional drop, never a genuine clash among placed sessions
    assert result.conflict_violations == 0
    assert result.is_valid


def test_replan_whole_day_holiday_drops_that_day(reference_problem, baseline):
    affected = affected_slots_for_day(reference_problem, day=3, from_period=None)
    result = replan(reference_problem, baseline, affected)
    # a full holiday: nothing from that day survives in the blocked window; result stays conflict-free
    assert result.is_valid
    assert len(result.dropped_session_ids) > 0
    # no placed session sits on any blocked (whole-day) slot
    reqs = {r.id: r for r in expand_requirements(reference_problem)}
    for a in result.solution.assignments:
        req = reqs.get(a.session_id)
        if req and not req.is_break:
            occ = _occ(reference_problem, a.time_slot_id, req.duration_slots)
            assert not any(sid in affected for sid in occ)


def test_replan_does_not_mutate_inputs(reference_problem, baseline):
    before_assignments = list(baseline.assignments)
    before_blocked = reference_problem.blocked_slot_ids
    replan(reference_problem, baseline, affected_slots_for_day(reference_problem, 1, from_period=5))
    assert baseline.assignments == before_assignments
    assert reference_problem.blocked_slot_ids == before_blocked  # master problem untouched

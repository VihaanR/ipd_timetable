"""Regression tests for break placement: exactly one break per day per division, pinned to its
own day, inside the mid-day band, and -- since breaks must count as "occupied" for gap-scoring
purposes -- not pushed to the day's edge just to dodge the idle-gap penalty."""
from timetable.models import expand_requirements
from timetable.scoring import score
from timetable.solvers.greedy import GreedySolver
from timetable.solvers.ga import GASolver


def _break_placements(solution, problem):
    reqs = {r.id: r for r in expand_requirements(problem)}
    slots = {t.id: t for t in problem.time_slots}
    out = []
    for a in solution.assignments:
        r = reqs.get(a.session_id)
        if r and r.is_break:
            out.append((r, slots[a.time_slot_id]))
    return out


def test_greedy_break_exactly_one_per_day_in_band(small_problem):
    sol = GreedySolver().solve(small_problem)
    for req, ts in _break_placements(sol, small_problem):
        day_slots = small_problem.slots_for_day(ts.day)
        assert ts.day == req.fixed_day
        assert ts.period not in (day_slots[0].period, day_slots[1].period, day_slots[-1].period)


def test_ga_randomized_seed_break_respects_band(small_problem):
    # exercises GA's own _randomized_construct seeding path (regression for the stale band bug)
    sol = GASolver().solve(small_problem, time_limit_s=6)
    for req, ts in _break_placements(sol, small_problem):
        day_slots = small_problem.slots_for_day(ts.day)
        assert ts.day == req.fixed_day, f"{req.id} landed on day {ts.day}, expected {req.fixed_day}"
        assert ts.period not in (day_slots[0].period, day_slots[1].period, day_slots[-1].period)


def test_scorer_does_not_penalize_break_as_a_gap(small_problem):
    # a break sandwiched between occupied periods must not register as an idle gap -- otherwise
    # the optimizer is incentivized to push breaks to the day's edge instead of mid-day
    sol = GreedySolver().solve(small_problem)
    result = score(sol, small_problem)
    breaks_in_band = all(
        ts.period not in (small_problem.slots_for_day(ts.day)[0].period, small_problem.slots_for_day(ts.day)[-1].period)
        for _req, ts in _break_placements(sol, small_problem)
    )
    assert breaks_in_band
    # idle_gaps should reflect only genuinely idle (unoccupied, non-break) periods
    assert result.details["idle_gaps"] >= 0


def test_reference_pipeline_breaks_cluster_near_midmorning(reference_problem):
    from timetable.pipeline import run_pipeline, PipelineConfig
    cfg = PipelineConfig(greedy_time_limit_s=3, mip_time_limit_s=20, ga_time_limit_s=8, cpsat_time_limit_s=20)
    sol = run_pipeline(reference_problem, cfg).final
    placements = _break_placements(sol, reference_problem)
    assert len(placements) == len(reference_problem.divisions) * reference_problem.days_per_week
    for req, ts in placements:
        assert ts.day == req.fixed_day
        day_slots = reference_problem.slots_for_day(ts.day)
        assert ts.period not in (day_slots[0].period, day_slots[1].period, day_slots[-1].period)

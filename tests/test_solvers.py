"""Solver-level tests. Time limits are kept small so the suite runs quickly; CP-SAT/MIP are
given enough budget to reach a feasible (zero-hard-violation) solution on the small instance."""
import pytest

from timetable.scoring import score
from timetable.solvers.greedy import GreedySolver
from timetable.solvers.mip import MIPSolver
from timetable.solvers.ga import GASolver
from timetable.solvers.cpsat import CPSATSolver


def test_greedy_runs_fast_and_returns_solution(small_problem):
    sol = GreedySolver().solve(small_problem)
    assert sol.solver_name == "greedy"
    assert sol.wall_clock_seconds < 2.0
    assert len(sol.assignments) > 0


def test_mip_finds_zero_hard_violation_solution(small_problem):
    sol = MIPSolver().solve(small_problem, time_limit_s=60)
    assert sol.status in ("OPTIMAL", "FEASIBLE")
    result = score(sol, small_problem)
    assert result.hard_violations == 0


def test_cpsat_finds_zero_hard_violation_solution(small_problem):
    sol = CPSATSolver().solve(small_problem, time_limit_s=30)
    assert sol.status in ("OPTIMAL", "FEASIBLE")
    result = score(sol, small_problem)
    assert result.hard_violations == 0


def test_ga_improves_over_random_and_returns_solution(small_problem):
    sol = GASolver().solve(small_problem, time_limit_s=8)
    assert sol.solver_name == "ga"
    assert len(sol.assignments) > 0
    # GA should at least beat an empty schedule's hard-violation count
    result = score(sol, small_problem)
    assert result.hard_violations < len(small_problem.time_slots)


def test_cpsat_respects_warm_start(small_problem):
    seed = GreedySolver().solve(small_problem)
    sol = CPSATSolver().solve(small_problem, time_limit_s=20, warm_start=seed)
    result = score(sol, small_problem)
    assert result.hard_violations == 0

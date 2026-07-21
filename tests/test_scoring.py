from timetable.models import Assignment, Solution, expand_requirements
from timetable.scoring import score, better


def _empty_solution(name="empty"):
    return Solution(assignments=[], solver_name=name, wall_clock_seconds=0.0)


def test_empty_solution_has_all_sessions_unassigned(small_problem):
    reqs = expand_requirements(small_problem)
    result = score(_empty_solution(), small_problem)
    # every session unassigned is a hard violation, so hard >= number of requirements
    assert result.hard_violations >= len(reqs)
    assert result.soft_cost == 0.0


def test_score_key_is_lexicographic():
    a = Solution([], "a", 0.0)
    from timetable.scoring import ScoreResult
    r_low_hard = ScoreResult(hard_violations=1, soft_cost=1000.0)
    r_high_hard = ScoreResult(hard_violations=2, soft_cost=0.0)
    assert r_low_hard.key() < r_high_hard.key()


def test_better_prefers_fewer_hard_violations(small_problem):
    from timetable.solvers.greedy import GreedySolver
    good = GreedySolver().solve(small_problem)
    empty = _empty_solution()
    chosen = better(good, empty, small_problem)
    assert chosen is good


def test_better_handles_none(small_problem):
    s = _empty_solution()
    assert better(s, None, small_problem) is s


def test_double_booking_detected(small_problem):
    # place two different sessions of the same division in the same room+slot
    reqs = expand_requirements(small_problem)
    theory = [r for r in reqs if not r.is_break and r.duration_slots == 1][:2]
    assert len(theory) == 2
    slot_id = small_problem.time_slots[1].id  # not first/last
    room_id = small_problem.rooms[0].id
    sol = Solution(
        assignments=[
            Assignment(theory[0].id, slot_id, room_id),
            Assignment(theory[1].id, slot_id, room_id),
        ],
        solver_name="conflict", wall_clock_seconds=0.0,
    )
    result = score(sol, small_problem)
    assert result.hard_violations > 0

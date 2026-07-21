from timetable.models import expand_requirements, SessionType


def test_reference_and_synthetic_validate_clean(reference_problem, small_problem, medium_problem):
    assert reference_problem.validate() == []
    assert small_problem.validate() == []
    assert medium_problem.validate() == []


def test_expand_requirements_is_deterministic(small_problem):
    a = [r.id for r in expand_requirements(small_problem)]
    b = [r.id for r in expand_requirements(small_problem)]
    assert a == b
    assert len(a) == len(set(a)), "session requirement ids must be unique"


def test_break_requirements_exist_per_division_per_day(small_problem):
    reqs = expand_requirements(small_problem)
    breaks = [r for r in reqs if r.is_break]
    expected = len(small_problem.divisions) * small_problem.days_per_week
    assert len(breaks) == expected


def test_practicals_are_batch_split_pairs(small_problem):
    reqs = expand_requirements(small_problem)
    groups: dict[str, list] = {}
    for r in reqs:
        if r.session_type == SessionType.PRACTICAL and r.batch_group_id:
            groups.setdefault(r.batch_group_id, []).append(r)
    assert groups, "expected some practical batch groups"
    for members in groups.values():
        assert len(members) == 2, "each lab must split into exactly 2 simultaneous batches"
        assert members[0].batch_id != members[1].batch_id
        assert all(m.duration_slots == 2 for m in members)


def test_per_batch_faculty_resolves(reference_problem):
    # reference dataset assigns two distinct instructors to each practical's two batches
    div = reference_problem.division_by_id()["D1"]
    b1, b2 = div.batch_pair()
    f1 = div.faculty_for("WE", b1)
    f2 = div.faculty_for("WE", b2)
    assert f1 is not None and f2 is not None
    assert f1 != f2

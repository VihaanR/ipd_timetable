from timetable.models import CourseCategory, expand_requirements
from timetable.sample_data import load_reference_instance


def test_reference_instance_loads_and_validates():
    problem = load_reference_instance()
    assert problem.validate() == []


def test_reference_has_three_divisions_with_batches():
    problem = load_reference_instance()
    assert {d.id for d in problem.divisions} == {"D1", "D2", "D3"}
    for d in problem.divisions:
        assert d.batches is not None and len(d.batches) == 2


def test_reference_covers_nep_categories():
    problem = load_reference_instance()
    categories = {c.category for c in problem.courses}
    for required in (CourseCategory.MAJOR, CourseCategory.MINOR, CourseCategory.SKILL,
                     CourseCategory.AEC, CourseCategory.VAC, CourseCategory.OPEN_ELECTIVE):
        assert required in categories, f"reference dataset missing {required}"


def test_reference_open_elective_is_sync_grouped():
    problem = load_reference_instance()
    reqs = expand_requirements(problem)
    oe = [r for r in reqs if r.course_code == "OE"]
    assert oe, "expected OE sessions"
    assert all(r.sync_group_id for r in oe), "OE sessions must be sync-grouped across divisions"


def test_reference_faculty_all_referenced_exist():
    problem = load_reference_instance()
    known = {f.id for f in problem.faculty}
    for d in problem.divisions:
        for value in d.faculty_by_course.values():
            fids = value if isinstance(value, tuple) else (value,)
            for fid in fids:
                assert fid in known, f"unknown faculty {fid} in {d.id}"

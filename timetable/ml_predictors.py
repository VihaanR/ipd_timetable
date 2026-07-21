"""Predictive quality models feeding GA's fitness function (Abhish's GA+ML architecture).

Per the team's architecture docs, three models predict normalized [0,1] quality indicators that
do NOT construct the timetable themselves -- they only refine fitness scoring:
  difficulty_score  = difficultyModel.predict(subjectFeatures)
  fatigue_index     = fatigueModel.predict(dayFeatures)
  infra_stress_score = infraModel.predict(weekFeatures)

These are heuristic, rule-based stand-ins: no real historical academic-performance dataset
(average marks, failure rates, assessment weightage) exists yet to train actual regressors. Each
model's `predict(features: dict) -> float` interface is deliberately narrow so a trained sklearn
model can be swapped in later without touching the GA loop -- only these classes change.
"""
from __future__ import annotations

from timetable.models import ProblemInstance, SessionType, Solution, expand_requirements


class DifficultyModel:
    def predict(self, features: dict) -> float:
        is_heavy = 1.0 if features.get("is_heavy") else 0.0
        credits = features.get("credits", 0)
        score = 0.5 * is_heavy + 0.5 * min(1.0, credits / 6.0)
        return max(0.0, min(1.0, score))


class FatigueModel:
    def predict(self, features: dict) -> float:
        heavy_count = features.get("heavy_count", 0)
        max_heavy_run = features.get("max_heavy_run", 0)
        total_hours = features.get("total_hours", 0)
        score = 0.15 * heavy_count + 0.25 * max_heavy_run + 0.05 * total_hours
        return max(0.0, min(1.0, score))


class InfraStressModel:
    def predict(self, features: dict) -> float:
        labs_today = features.get("labs_today", 0)
        peak_day_labs = features.get("peak_day_labs", 0)
        score = 0.3 * labs_today + 0.2 * peak_day_labs
        return max(0.0, min(1.0, score))


def difficulty_penalty(solution: Solution, problem: ProblemInstance) -> float:
    """Average across divisions of the day-to-day variance in scheduled subject difficulty --
    rewards spreading heavy/high-credit courses evenly across the week."""
    model = DifficultyModel()
    course_difficulty = {c.code: model.predict({"is_heavy": c.is_heavy, "credits": c.credits})
                          for c in problem.courses}
    requirements = {r.id: r for r in expand_requirements(problem)}
    assignments = solution.assignment_by_session()
    slots_by_id = {t.id: t for t in problem.time_slots}

    per_div_day: dict[tuple[str, int], float] = {}
    for req_id, a in assignments.items():
        req = requirements.get(req_id)
        if not req or req.is_break:
            continue
        ts = slots_by_id.get(a.time_slot_id)
        if not ts:
            continue
        key = (req.division_id, ts.day)
        per_div_day[key] = per_div_day.get(key, 0.0) + course_difficulty.get(req.course_code, 0.0)

    by_div: dict[str, list[float]] = {}
    for (div, _day), val in per_div_day.items():
        by_div.setdefault(div, []).append(val)

    variances = []
    for vals in by_div.values():
        if not vals:
            continue
        mean = sum(vals) / len(vals)
        variances.append(sum((v - mean) ** 2 for v in vals) / len(vals))
    return sum(variances) / len(variances) if variances else 0.0


def fatigue_penalty(solution: Solution, problem: ProblemInstance) -> float:
    """Average FatigueModel score across every (division, day) that has any class."""
    model = FatigueModel()
    courses_by_code = problem.course_by_code()
    requirements = {r.id: r for r in expand_requirements(problem)}
    assignments = solution.assignment_by_session()
    slots_by_id = {t.id: t for t in problem.time_slots}

    per_div_day_heavy_periods: dict[tuple[str, int], list[int]] = {}
    per_div_day_hours: dict[tuple[str, int], float] = {}
    for req_id, a in assignments.items():
        req = requirements.get(req_id)
        if not req or req.is_break:
            continue
        ts = slots_by_id.get(a.time_slot_id)
        if not ts:
            continue
        key = (req.division_id, ts.day)
        per_div_day_hours[key] = per_div_day_hours.get(key, 0.0) + req.duration_slots
        course = courses_by_code.get(req.course_code)
        if course and course.is_heavy:
            per_div_day_heavy_periods.setdefault(key, []).append(ts.period)

    scores = []
    for key, hours in per_div_day_hours.items():
        periods = sorted(per_div_day_heavy_periods.get(key, []))
        max_run, run = (1, 1) if periods else (0, 0)
        for i in range(1, len(periods)):
            if periods[i] == periods[i - 1] + 1:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 1
        scores.append(model.predict({
            "heavy_count": len(periods), "max_heavy_run": max_run, "total_hours": hours,
        }))
    return sum(scores) / len(scores) if scores else 0.0


def infra_stress_penalty(solution: Solution, problem: ProblemInstance) -> float:
    """Average InfraStressModel score across the week's lab-session distribution."""
    model = InfraStressModel()
    requirements = {r.id: r for r in expand_requirements(problem)}
    assignments = solution.assignment_by_session()
    slots_by_id = {t.id: t for t in problem.time_slots}

    labs_per_day: dict[int, set] = {}
    for req_id, a in assignments.items():
        req = requirements.get(req_id)
        if not req or req.session_type != SessionType.PRACTICAL or not req.batch_group_id:
            continue
        ts = slots_by_id.get(a.time_slot_id)
        if not ts:
            continue
        labs_per_day.setdefault(ts.day, set()).add(req.batch_group_id)

    if not labs_per_day:
        return 0.0
    counts = [len(v) for v in labs_per_day.values()]
    peak = max(counts)
    scores = [model.predict({"labs_today": c, "peak_day_labs": peak}) for c in counts]
    return sum(scores) / len(scores)

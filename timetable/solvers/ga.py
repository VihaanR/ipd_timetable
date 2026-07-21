"""Genetic Algorithm solver -- Abhish's GA+ML architecture (hand-rolled, no DEAP).

Design follows the team's own architecture docs (references/GA_ML_Timetable_Optimization_*.pdf):
  - Chromosome: one fixed-position gene per SessionRequirement (from expand_requirements), so
    crossover positions have stable meaning across the whole population.
  - Initialization: staged construction (assign_break_slots -> allocate_lab_blocks ->
    assign_theory_sessions -> repair_schedule), matching the doc's Block 3. Implemented as a
    randomized variant of the greedy constructor for population diversity.
  - Selection: tournament (k=3). Crossover: division-wise (primary) + uniform gene crossover
    (secondary/fallback). Mutation: reassign-gene, swap-slots, shift-lab-block.
  - Fitness: 10000*hard + 100*normalized_soft + 40*difficulty + 60*fatigue + 30*infra, per the
    doc's weights (hard/soft terms come from the shared scoring.py so GA stays comparable to
    MIP/CP-SAT; soft_cost is normalized by requirement count so the ML terms -- on a clean [0,1]
    scale -- stay meaningfully influential rather than drowned out by soft_cost's larger raw
    magnitude).

DEAP was deliberately not used: its generic `creator`/`toolbox` machinery is built around
position/order-based crossover operators that don't understand scheduling semantics (room-type
validity, batch pairing, faculty eligibility) -- using it would still require hand-writing custom
repair/crossover/mutation operators, so it mainly adds an extra dependency and global mutable
state without saving real code, at the cost of transparency in a codebase meant to explain how
each of the three solvers works.
"""
from __future__ import annotations

import random
import time

from timetable.models import Assignment, ProblemInstance, Solution, expand_requirements
from timetable.ml_predictors import difficulty_penalty, fatigue_penalty, infra_stress_penalty
from timetable.scoring import score
from timetable.solvers.base import SolverBase
from timetable.solvers.candidates import build_candidates, batch_group_members, sync_group_members
from timetable.solvers.greedy import _Trackers, _slots_by_day, _consecutive_slot_ids, NO_ROOM

POP_SIZE = 80
GENERATIONS = 150
CROSSOVER_RATE = 0.8
MUTATION_RATE = 0.15
ELITISM = 2
TOURNAMENT_SIZE = 3

Gene = tuple[int, str]          # (time_slot_id, room_id)
Chromosome = list[Gene]         # one gene per requirement, fixed position


def _randomized_construct(problem: ProblemInstance, requirements, rng: random.Random) -> Solution:
    """Randomized variant of greedy.GreedySolver's staged construction, used to seed a diverse
    initial GA population (and to fill in genes a repair pass could not otherwise place)."""
    slots_by_day = _slots_by_day(problem)
    classrooms = [r.id for r in problem.rooms if r.room_type == "classroom"]
    labs = [r.id for r in problem.rooms if r.room_type == "lab"]
    trackers = _Trackers(problem)
    assignments: list[Assignment] = []

    blocked = problem.blocked_slot_ids

    def _blocked(slot_ids: list[int]) -> bool:
        return bool(blocked) and any(sid in blocked for sid in slot_ids)

    breaks = [r for r in requirements if r.is_break]
    lab_groups = batch_group_members(requirements)
    sync_groups = sync_group_members(requirements)
    singles = [r for r in requirements if not r.is_break and not r.batch_group_id and not r.sync_group_id]

    for req in breaks:
        day = req.fixed_day if req.fixed_day is not None else int(req.id.rsplit("D", 1)[-1])
        day_slots = slots_by_day.get(day, [])
        if len(day_slots) < 4:
            continue
        # mid-day band: exclude the first two periods and the last period, matching
        # candidates.py / greedy.py / mip.py so no solver can drift out of the legal band
        pick = rng.choice(day_slots[2:-1])
        slot_ids = [pick.id]
        trackers.commit(req.division_id, None, None, None, day, slot_ids, req.duration_slots)
        assignments.append(Assignment(session_id=req.id, time_slot_id=pick.id, room_id=NO_ROOM))

    group_ids = list(lab_groups.keys())
    rng.shuffle(group_ids)
    for group_id in group_ids:
        reqs = lab_groups[group_id]
        day_order = list(slots_by_day.keys())
        rng.shuffle(day_order)
        placed = False
        for day in day_order:
            if placed:
                break
            day_slots = slots_by_day[day]
            start_indices = list(range(len(day_slots)))
            rng.shuffle(start_indices)
            for start_idx in start_indices:
                slot_ids = _consecutive_slot_ids(day_slots, start_idx, reqs[0].duration_slots)
                if slot_ids is None:
                    continue
                if _blocked(slot_ids):
                    continue
                if not all(trackers.division_free(r.division_id, r.batch_id, slot_ids) for r in reqs):
                    continue
                if not all(trackers.faculty_free(r.faculty_id, day, slot_ids, r.duration_slots) for r in reqs):
                    continue
                key = (reqs[0].division_id, reqs[0].course_code, day)
                if key in trackers.division_day_occurrence:
                    continue
                available_labs = [l for l in labs if all((l, sid) not in trackers.room_busy for sid in slot_ids)]
                rng.shuffle(available_labs)
                if len(available_labs) < len(reqs):
                    continue
                for req, room_id in zip(reqs, available_labs):
                    trackers.commit(req.division_id, req.batch_id, req.faculty_id, room_id, day, slot_ids, req.duration_slots)
                    assignments.append(Assignment(session_id=req.id, time_slot_id=slot_ids[0], room_id=room_id))
                trackers.division_day_occurrence.add(key)
                placed = True
                break

    group_ids = list(sync_groups.keys())
    rng.shuffle(group_ids)
    for group_id in group_ids:
        reqs = sync_groups[group_id]
        day_order = list(slots_by_day.keys())
        rng.shuffle(day_order)
        placed = False
        for day in day_order:
            if placed:
                break
            day_slots = list(slots_by_day[day])
            rng.shuffle(day_slots)
            for ts in day_slots:
                slot_ids = [ts.id]
                if _blocked(slot_ids):
                    continue
                if not all(trackers.division_free(r.division_id, None, slot_ids) for r in reqs):
                    continue
                if not all(trackers.faculty_free(r.faculty_id, day, slot_ids, r.duration_slots) for r in reqs):
                    continue
                keys = [(r.division_id, r.course_code, day) for r in reqs]
                if any(k in trackers.division_day_occurrence for k in keys):
                    continue
                pool = list(classrooms)
                rng.shuffle(pool)
                chosen_rooms, used, ok = [], set(), True
                for _ in reqs:
                    found = next((c for c in pool if c not in used and (c, ts.id) not in trackers.room_busy), None)
                    if found is None:
                        ok = False
                        break
                    used.add(found)
                    chosen_rooms.append(found)
                if not ok:
                    continue
                for req, room_id in zip(reqs, chosen_rooms):
                    trackers.commit(req.division_id, None, req.faculty_id, room_id, day, slot_ids, req.duration_slots)
                    assignments.append(Assignment(session_id=req.id, time_slot_id=ts.id, room_id=room_id))
                for k in keys:
                    trackers.division_day_occurrence.add(k)
                placed = True
                break

    order = list(singles)
    rng.shuffle(order)
    for req in order:
        placed = False
        day_order = list(slots_by_day.keys())
        rng.shuffle(day_order)
        for day in day_order:
            if placed:
                break
            key = (req.division_id, req.course_code, day)
            if key in trackers.division_day_occurrence:
                continue
            day_slots = list(slots_by_day[day])
            rng.shuffle(day_slots)
            for ts in day_slots:
                slot_ids = [ts.id]
                if _blocked(slot_ids):
                    continue
                if not trackers.division_free(req.division_id, None, slot_ids):
                    continue
                if not trackers.faculty_free(req.faculty_id, day, slot_ids, req.duration_slots):
                    continue
                pool = list(classrooms)
                rng.shuffle(pool)
                room_id = next((r for r in pool if (r, ts.id) not in trackers.room_busy), None)
                if room_id is None:
                    continue
                trackers.commit(req.division_id, None, req.faculty_id, room_id, day, slot_ids, req.duration_slots)
                assignments.append(Assignment(session_id=req.id, time_slot_id=ts.id, room_id=room_id))
                trackers.division_day_occurrence.add(key)
                placed = True
                break

    return Solution(assignments=assignments, solver_name="ga-init", wall_clock_seconds=0.0, status="PARTIAL")


def _solution_to_chromosome(solution: Solution, requirements) -> Chromosome:
    assigned = solution.assignment_by_session()
    chromosome: Chromosome = []
    for req in requirements:
        a = assigned.get(req.id)
        chromosome.append((a.time_slot_id, a.room_id) if a else (-1, NO_ROOM))
    return chromosome


def _chromosome_to_solution(chromosome: Chromosome, requirements, name: str, elapsed: float) -> Solution:
    assignments = [
        Assignment(session_id=req.id, time_slot_id=slot_id, room_id=room_id)
        for req, (slot_id, room_id) in zip(requirements, chromosome) if slot_id >= 0
    ]
    return Solution(assignments=assignments, solver_name=name, wall_clock_seconds=elapsed, status="FEASIBLE")


def _fitness(chromosome: Chromosome, requirements, problem: ProblemInstance) -> float:
    solution = _chromosome_to_solution(chromosome, requirements, "ga", 0.0)
    result = score(solution, problem)
    normalized_soft = result.soft_cost / max(1, len(requirements))
    diff = difficulty_penalty(solution, problem)
    fat = fatigue_penalty(solution, problem)
    infra = infra_stress_penalty(solution, problem)
    return 10000 * result.hard_violations + 100 * normalized_soft + 40 * diff + 60 * fat + 30 * infra


def _tournament_select(population: list[Chromosome], fitnesses: list[float], rng: random.Random) -> Chromosome:
    idxs = rng.sample(range(len(population)), min(TOURNAMENT_SIZE, len(population)))
    best = min(idxs, key=lambda i: fitnesses[i])
    return population[best]


def _division_wise_crossover(a: Chromosome, b: Chromosome, requirements, division_ids, rng: random.Random) -> Chromosome:
    swap_from_b = {d for d in division_ids if rng.random() < 0.5}
    return [b[i] if requirements[i].division_id in swap_from_b else a[i] for i in range(len(requirements))]


def _uniform_crossover(a: Chromosome, b: Chromosome, rng: random.Random) -> Chromosome:
    return [rng.choice((a[i], b[i])) for i in range(len(a))]


def _mutate(chromosome: Chromosome, requirements, req_index, candidates, batch_groups, rng: random.Random) -> Chromosome:
    child = list(chromosome)
    op = rng.choice(("reassign", "swap", "shift_lab_block"))

    if op == "reassign":
        i = rng.randrange(len(requirements))
        cands = candidates.get(requirements[i].id, [])
        if cands:
            start_id, _occ, _day, room_id = rng.choice(cands)
            child[i] = (start_id, room_id)

    elif op == "swap":
        i, j = rng.sample(range(len(requirements)), 2)
        ri, rj = requirements[i], requirements[j]
        if ri.room_type == rj.room_type and ri.duration_slots == rj.duration_slots:
            child[i], child[j] = child[j], child[i]

    else:  # shift_lab_block: move both halves of a random batch pair together
        if batch_groups:
            group_id = rng.choice(list(batch_groups.keys()))
            members = batch_groups[group_id]
            anchor = members[0]
            cands = candidates.get(anchor.id, [])
            if cands:
                start_id, _occ, _day, room_id = rng.choice(cands)
                for member in members:
                    idx = req_index[member.id]
                    member_cands = candidates.get(member.id, [])
                    same_slot = [c for c in member_cands if c[0] == start_id]
                    if same_slot:
                        _s, _o, _d, r = rng.choice(same_slot)
                        child[idx] = (start_id, r)
    return child


def _repair(chromosome: Chromosome, requirements, req_index, problem: ProblemInstance, candidates,
            batch_groups, sync_groups, slots_by_day_cache, rng: random.Random) -> Chromosome:
    """Greedily resolves double-bookings and batch/sync desynchronization introduced by crossover
    or mutation, leaving genes that are already conflict-free untouched."""
    child = list(chromosome)

    # re-sync batch pairs to the anchor's slot where possible
    for group_id, members in batch_groups.items():
        anchor = members[0]
        anchor_idx = req_index[anchor.id]
        anchor_slot = child[anchor_idx][0]
        for member in members[1:]:
            idx = req_index[member.id]
            if child[idx][0] != anchor_slot:
                member_cands = candidates.get(member.id, [])
                same_slot = [c for c in member_cands if c[0] == anchor_slot]
                if same_slot:
                    _s, _o, _d, r = rng.choice(same_slot)
                    child[idx] = (anchor_slot, r)

    for group_id, members in sync_groups.items():
        anchor = members[0]
        anchor_idx = req_index[anchor.id]
        anchor_slot = child[anchor_idx][0]
        for member in members[1:]:
            idx = req_index[member.id]
            if child[idx][0] != anchor_slot:
                member_cands = candidates.get(member.id, [])
                same_slot = [c for c in member_cands if c[0] == anchor_slot]
                if same_slot:
                    _s, _o, _d, r = rng.choice(same_slot)
                    child[idx] = (anchor_slot, r)

    trackers = _Trackers(problem)
    slots_by_id = {t.id: t for t in problem.time_slots}
    for i, req in enumerate(requirements):
        slot_id, room_id = child[i]
        if slot_id < 0:
            continue
        ts = slots_by_id.get(slot_id)
        if ts is None:
            child[i] = (-1, NO_ROOM)
            continue
        occ = _consecutive_slot_ids(slots_by_day_cache[ts.day], ts.period, req.duration_slots)
        # a gene must be one of req's OWN legal (slot, room) candidates -- catches e.g. the "swap"
        # mutation moving a fixed_day break (or any other pinned/banded session) to an illegal
        # slot even when it happens not to collide with anything else
        own_valid = (slot_id, room_id) in {(s, r) for (s, _o, _d, r) in candidates.get(req.id, [])}
        conflict = (
            occ is None
            or not own_valid
            or not trackers.division_free(req.division_id, req.batch_id, occ)
            or not trackers.faculty_free(req.faculty_id, ts.day, occ, req.duration_slots)
            or (room_id != NO_ROOM and any((room_id, sid) in trackers.room_busy for sid in occ))
        )
        if conflict:
            cands = list(candidates.get(req.id, []))
            rng.shuffle(cands)
            replaced = False
            for (s, o, d, r) in cands:
                if (trackers.division_free(req.division_id, req.batch_id, o)
                        and trackers.faculty_free(req.faculty_id, d, o, req.duration_slots)
                        and (r == NO_ROOM or all((r, sid) not in trackers.room_busy for sid in o))):
                    trackers.commit(req.division_id, req.batch_id, req.faculty_id, r, d, o, req.duration_slots)
                    child[i] = (s, r)
                    replaced = True
                    break
            if not replaced:
                child[i] = (-1, NO_ROOM)
        else:
            trackers.commit(req.division_id, req.batch_id, req.faculty_id, room_id, ts.day, occ, req.duration_slots)
    return child


class GASolver(SolverBase):
    name = "ga"

    def solve(self, problem: ProblemInstance, time_limit_s: float = 30,
              warm_start: Solution | None = None,
              warm_starts: list[Solution] | None = None) -> Solution:
        """`warm_start` seeds the initial population from one prior solution; `warm_starts` seeds
        it from several (e.g. the greedy seed AND the MIP solution in the hybrid pipeline), so the
        GA genuinely builds on the strengths of the earlier stages rather than starting cold."""
        start = time.time()
        rng = random.Random(42)
        requirements = expand_requirements(problem)
        req_index = {req.id: i for i, req in enumerate(requirements)}
        slots_by_day_cache = _slots_by_day(problem)
        candidates = build_candidates(problem, requirements)
        batch_groups = batch_group_members(requirements)
        sync_groups = sync_group_members(requirements)
        division_ids = [d.id for d in problem.divisions]

        def mutate(c):
            return _mutate(c, requirements, req_index, candidates, batch_groups, rng)

        def repair(c):
            return _repair(c, requirements, req_index, problem, candidates, batch_groups, sync_groups,
                            slots_by_day_cache, rng)

        seeds: list[Solution] = list(warm_starts or [])
        if warm_start is not None:
            seeds.append(warm_start)

        population: list[Chromosome] = []
        if seeds:
            # inject each seed plus a spread of mutated copies, so the population starts from the
            # best of every upstream solver and explores around them
            copies_per_seed = max(1, min(POP_SIZE // 4, 10) // len(seeds))
            for seed in seeds:
                seed_chromosome = _solution_to_chromosome(seed, requirements)
                population.append(seed_chromosome)
                for _ in range(copies_per_seed):
                    population.append(repair(mutate(seed_chromosome)))

        while len(population) < POP_SIZE:
            solution = _randomized_construct(problem, requirements, rng)
            population.append(_solution_to_chromosome(solution, requirements))

        best_chromosome = population[0]
        best_fitness = float("inf")

        generation = 0
        while generation < GENERATIONS and (time.time() - start) < time_limit_s:
            fitnesses = [_fitness(c, requirements, problem) for c in population]
            gen_best_idx = min(range(len(population)), key=lambda i: fitnesses[i])
            if fitnesses[gen_best_idx] < best_fitness:
                best_fitness = fitnesses[gen_best_idx]
                best_chromosome = population[gen_best_idx]

            ranked = sorted(range(len(population)), key=lambda i: fitnesses[i])
            next_population = [population[i] for i in ranked[:ELITISM]]

            while len(next_population) < POP_SIZE:
                parent_a = _tournament_select(population, fitnesses, rng)
                parent_b = _tournament_select(population, fitnesses, rng)
                if rng.random() < CROSSOVER_RATE:
                    child = (_division_wise_crossover(parent_a, parent_b, requirements, division_ids, rng)
                             if rng.random() < 0.7 else _uniform_crossover(parent_a, parent_b, rng))
                else:
                    child = list(parent_a)
                if rng.random() < MUTATION_RATE:
                    child = mutate(child)
                child = repair(child)
                next_population.append(child)

            population = next_population
            generation += 1

        elapsed = time.time() - start
        solution = _chromosome_to_solution(best_chromosome, requirements, self.name, elapsed)
        return solution

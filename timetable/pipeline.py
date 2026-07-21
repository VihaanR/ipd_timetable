"""Orchestration: the genuine hybrid pipeline plus the cold ensemble baseline.

`run_pipeline` -- COOPERATIVE 4-stage hybrid (the recommended production path). All four
algorithms take part and each one hands its work to the next; they are not run independently and
compared. The chain:

    1. Greedy      builds a fast feasible seed (sub-second).
    2. MIP         warm-started from the greedy seed (SetHint); solves the exact 0/1 model,
                   nailing the hard constraints + the linearizable soft terms.
    3. GA          initial population seeded from BOTH the greedy seed AND the MIP solution;
                   explores the soft-constraint / ML-fitness space MIP cannot express, producing
                   a diverse improved candidate.
    4. CP-SAT      warm-started (AddHint) from the best candidate so far; does the final polish
                   using its richer objective (native gap-minimization etc.).

Every stage passes its solution forward as a warm start, so stage N literally continues from
stage N-1's work. `better()` (lexicographic on (hard_violations, soft_cost) from scoring.py) is a
safety net that keeps the running best monotonic -- if any stage's solver returns something worse
(e.g. a timeout), the pipeline carries the earlier, better solution forward as the next stage's
warm start rather than losing it. The result records each stage so you can see the quality climb.

`run_ensemble` -- the CONTRAST baseline: runs Greedy, MIP, GA independently from a cold start (no
cross-seeding), then picks the best. CP-SAT is deliberately excluded from this cold comparison
(owner decision, 2026-07-21) -- it is the platform's polish/default solver, not one of the cold
baselines here. Useful to show how much the hybrid handoff actually buys over "just run the fast
baselines and compare".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from timetable.models import ProblemInstance, Solution
from timetable.scoring import better, score
from timetable.solvers.greedy import GreedySolver
from timetable.solvers.mip import MIPSolver
from timetable.solvers.ga import GASolver
from timetable.solvers.cpsat import CPSATSolver


@dataclass
class PipelineConfig:
    greedy_time_limit_s: float = 5
    mip_time_limit_s: float = 60
    ga_time_limit_s: float = 30
    cpsat_time_limit_s: float = 300
    run_mip: bool = True
    run_ga: bool = True
    run_cpsat: bool = True


@dataclass
class StageReport:
    """One stage of the hybrid chain: the solver's own output, and the running-best after it."""
    name: str
    solver_status: str
    wall_clock_s: float
    hard_violations: int
    soft_cost: float
    running_best_hard: int
    running_best_soft: float
    improved: bool


@dataclass
class PipelineResult:
    stages: list[Solution]
    final: Solution
    total_wall_clock_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    reports: list[StageReport] = field(default_factory=list)


def run_pipeline(problem: ProblemInstance, config: PipelineConfig | None = None) -> PipelineResult:
    """Cooperative 4-stage hybrid: Greedy -> MIP -> GA(multi-seed) -> CP-SAT, each warm-started
    from the previous stage(s). See module docstring."""
    config = config or PipelineConfig()
    start = time.time()
    stages: list[Solution] = []
    reports: list[StageReport] = []
    notes: list[str] = []

    best: Solution | None = None

    def register(stage_name: str, solution: Solution) -> None:
        nonlocal best
        sc = score(solution, problem)
        prev = best
        best = better(solution, best, problem)
        best_sc = score(best, problem)
        reports.append(StageReport(
            name=stage_name, solver_status=solution.status, wall_clock_s=solution.wall_clock_seconds,
            hard_violations=sc.hard_violations, soft_cost=sc.soft_cost,
            running_best_hard=best_sc.hard_violations, running_best_soft=best_sc.soft_cost,
            improved=(prev is None or best is solution),
        ))
        stages.append(solution)

    # Stage 1: Greedy fast feasible seed
    greedy_sol = GreedySolver().solve(problem, time_limit_s=config.greedy_time_limit_s)
    register("Greedy (seed)", greedy_sol)

    # Stage 2: MIP, warm-started from the greedy seed
    mip_sol = None
    if config.run_mip:
        try:
            mip_sol = MIPSolver().solve(problem, time_limit_s=config.mip_time_limit_s, warm_start=best)
            register("MIP (exact core)", mip_sol)
        except Exception as exc:
            notes.append(f"MIP stage failed ({exc!r}); continuing from greedy seed.")

    # Stage 3: GA, population seeded from BOTH the greedy seed and the MIP solution
    if config.run_ga:
        ga_seeds = [s for s in (greedy_sol, mip_sol) if s is not None]
        ga_sol = GASolver().solve(problem, time_limit_s=config.ga_time_limit_s, warm_starts=ga_seeds)
        register("GA (explore, ML fitness)", ga_sol)

    # Stage 4: CP-SAT final polish, warm-started (AddHint) from the best candidate so far
    if config.run_cpsat:
        try:
            cpsat_sol = CPSATSolver().solve(problem, time_limit_s=config.cpsat_time_limit_s, warm_start=best)
            register("CP-SAT (polish)", cpsat_sol)
        except Exception as exc:
            notes.append(f"CP-SAT stage failed ({exc!r}); kept best of earlier stages.")

    total = time.time() - start
    return PipelineResult(stages=stages, final=best, total_wall_clock_s=total, notes=notes, reports=reports)


def run_ensemble(problem: ProblemInstance, config: PipelineConfig | None = None) -> PipelineResult:
    """Cold contrast baseline: Greedy, MIP, GA run independently (no cross-seeding); best is
    picked. CP-SAT is excluded here on purpose (see module docstring)."""
    config = config or PipelineConfig()
    start = time.time()
    stages: list[Solution] = []

    greedy_solution = GreedySolver().solve(problem, time_limit_s=config.greedy_time_limit_s)
    stages.append(greedy_solution)

    mip_solution = MIPSolver().solve(problem, time_limit_s=config.mip_time_limit_s)
    stages.append(mip_solution)

    ga_solution = GASolver().solve(problem, time_limit_s=config.ga_time_limit_s)
    stages.append(ga_solution)

    best = stages[0]
    for s in stages[1:]:
        best = better(s, best, problem)

    total = time.time() - start
    return PipelineResult(stages=stages, final=best, total_wall_clock_s=total)

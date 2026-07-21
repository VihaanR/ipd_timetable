"""Same equal-compute-budget ablation as ablation_pilot.py, run on the real DJSCE reference
dataset instead of synthetic small-scale instances. There is only one reference instance (it's
real institutional data, not seeded/generated), so "trials" here are repeated runs of the SAME
problem rather than different problem instances -- this isolates CP-SAT's own run-to-run search
variance (num_search_workers=8, no fixed seed) rather than generalization across instances. Still
the single most relevant number for this project, since it's the actual target use case.

Run:  python research/ablation_pilot_reference.py
"""
from __future__ import annotations

import csv
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timetable.sample_data import load_reference_instance
from timetable.scoring import score
from timetable.solvers.cpsat import CPSATSolver
from timetable.pipeline import run_pipeline, PipelineConfig

REPEATS = 3
TOTAL_BUDGET_S = 60
STAGE_SPLIT = dict(greedy_time_limit_s=3, mip_time_limit_s=20, ga_time_limit_s=12, cpsat_time_limit_s=25)
assert sum(STAGE_SPLIT.values()) == TOTAL_BUDGET_S


@dataclass
class Trial:
    run: int
    hard_a: int
    soft_a: float
    wall_a: float
    hard_b: int
    soft_b: float
    wall_b: float


def main():
    problem = load_reference_instance()
    trials = []
    print(f"Pilot ablation on the REAL DJSCE reference dataset (3 divisions, budget={TOTAL_BUDGET_S}s "
          f"each condition, {REPEATS} repeats)\n")
    print(f"{'run':<5}{'hard_A':<8}{'soft_A':<10}{'wall_A':<9}{'hard_B':<8}{'soft_B':<10}{'wall_B':<9}{'winner':<8}")

    for i in range(1, REPEATS + 1):
        t0 = time.time()
        sol_a = CPSATSolver().solve(problem, time_limit_s=TOTAL_BUDGET_S)
        wall_a = time.time() - t0
        sc_a = score(sol_a, problem)

        t0 = time.time()
        result_b = run_pipeline(problem, PipelineConfig(**STAGE_SPLIT))
        wall_b = time.time() - t0
        sc_b = score(result_b.final, problem)

        t = Trial(run=i, hard_a=sc_a.hard_violations, soft_a=sc_a.soft_cost, wall_a=wall_a,
                  hard_b=sc_b.hard_violations, soft_b=sc_b.soft_cost, wall_b=wall_b)
        trials.append(t)
        key_a, key_b = (t.hard_a, t.soft_a), (t.hard_b, t.soft_b)
        winner = "B (hybrid)" if key_b < key_a else ("A (cpsat)" if key_a < key_b else "tie")
        print(f"{t.run:<5}{t.hard_a:<8}{t.soft_a:<10.1f}{t.wall_a:<9.1f}"
              f"{t.hard_b:<8}{t.soft_b:<10.1f}{t.wall_b:<9.1f}{winner:<8}")

    soft_a = [t.soft_a for t in trials]
    soft_b = [t.soft_b for t in trials]
    wins_b = sum(1 for t in trials if (t.hard_b, t.soft_b) < (t.hard_a, t.soft_a))
    wins_a = sum(1 for t in trials if (t.hard_a, t.soft_a) < (t.hard_b, t.soft_b))
    ties = len(trials) - wins_a - wins_b

    print("\n--- summary ---")
    print(f"soft_cost  A (CP-SAT alone): mean={statistics.mean(soft_a):.1f}  stdev={statistics.pstdev(soft_a):.1f}")
    print(f"soft_cost  B (hybrid):       mean={statistics.mean(soft_b):.1f}  stdev={statistics.pstdev(soft_b):.1f}")
    print(f"wins: hybrid={wins_b}, cpsat_alone={wins_a}, ties={ties}  (repeats={len(trials)})")

    out_path = Path(__file__).resolve().parent / "ablation_pilot_reference_results.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["run", "hard_a_cpsat_alone", "soft_a_cpsat_alone", "wall_a_s",
                    "hard_b_hybrid", "soft_b_hybrid", "wall_b_s"])
        for t in trials:
            w.writerow([t.run, t.hard_a, f"{t.soft_a:.2f}", f"{t.wall_a:.2f}",
                        t.hard_b, f"{t.soft_b:.2f}", f"{t.wall_b:.2f}"])
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()

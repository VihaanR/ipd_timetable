"""Pilot ablation study: does the cooperative 4-stage hybrid pipeline actually beat CP-SAT alone
under an EQUAL total compute budget, or does it just look better because nobody controlled for
time?

This is a small pilot (N seeds x 1 scale) proving the methodology described in design.md SS15.2.
The full study there (N=10-20 seeds x small/medium/large/reference, Wilcoxon signed-rank
significance testing, warm-start on/off isolation) is future work -- this pilot exists so the
project has a REAL, honestly-scoped experimental result to present now, not just a plan.

Conditions, both given the same TOTAL_BUDGET_S of wall-clock time:
  A) CP-SAT alone, for the whole budget, cold (no warm start).
  B) The cooperative hybrid pipeline (Greedy -> MIP -> GA -> CP-SAT), stage budgets summing to
     the same total.

Run:  python research/ablation_pilot.py
"""
from __future__ import annotations

import csv
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timetable.sample_data import generate_sample_instance
from timetable.scoring import score
from timetable.solvers.cpsat import CPSATSolver
from timetable.pipeline import run_pipeline, PipelineConfig

SEEDS = [1, 2, 3, 4, 5]
SCALE = "small"
TOTAL_BUDGET_S = 40
# stage split for condition B, summing to TOTAL_BUDGET_S
STAGE_SPLIT = dict(greedy_time_limit_s=2, mip_time_limit_s=12, ga_time_limit_s=8, cpsat_time_limit_s=18)
assert sum(STAGE_SPLIT.values()) == TOTAL_BUDGET_S


@dataclass
class Trial:
    seed: int
    hard_a: int
    soft_a: float
    wall_a: float
    hard_b: int
    soft_b: float
    wall_b: float


def run_trial(seed: int) -> Trial:
    problem = generate_sample_instance(SCALE, seed=seed)

    t0 = time.time()
    sol_a = CPSATSolver().solve(problem, time_limit_s=TOTAL_BUDGET_S)
    wall_a = time.time() - t0
    sc_a = score(sol_a, problem)

    t0 = time.time()
    result_b = run_pipeline(problem, PipelineConfig(**STAGE_SPLIT))
    wall_b = time.time() - t0
    sc_b = score(result_b.final, problem)

    return Trial(seed=seed, hard_a=sc_a.hard_violations, soft_a=sc_a.soft_cost, wall_a=wall_a,
                 hard_b=sc_b.hard_violations, soft_b=sc_b.soft_cost, wall_b=wall_b)


def main():
    trials = []
    print(f"Pilot ablation: CP-SAT-alone (A) vs hybrid pipeline (B), {TOTAL_BUDGET_S}s budget each, "
          f"scale={SCALE}, N={len(SEEDS)} seeds\n")
    print(f"{'seed':<6}{'hard_A':<8}{'soft_A':<10}{'wall_A':<9}{'hard_B':<8}{'soft_B':<10}{'wall_B':<9}{'winner':<8}")
    for seed in SEEDS:
        t = run_trial(seed)
        trials.append(t)
        key_a, key_b = (t.hard_a, t.soft_a), (t.hard_b, t.soft_b)
        winner = "B (hybrid)" if key_b < key_a else ("A (cpsat)" if key_a < key_b else "tie")
        print(f"{t.seed:<6}{t.hard_a:<8}{t.soft_a:<10.1f}{t.wall_a:<9.1f}"
              f"{t.hard_b:<8}{t.soft_b:<10.1f}{t.wall_b:<9.1f}{winner:<8}")

    soft_a = [t.soft_a for t in trials]
    soft_b = [t.soft_b for t in trials]
    hard_a = [t.hard_a for t in trials]
    hard_b = [t.hard_b for t in trials]
    wins_b = sum(1 for t in trials if (t.hard_b, t.soft_b) < (t.hard_a, t.soft_a))
    wins_a = sum(1 for t in trials if (t.hard_a, t.soft_a) < (t.hard_b, t.soft_b))
    ties = len(trials) - wins_a - wins_b

    print("\n--- summary ---")
    print(f"soft_cost  A: mean={statistics.mean(soft_a):.1f}  stdev={statistics.pstdev(soft_a):.1f}")
    print(f"soft_cost  B: mean={statistics.mean(soft_b):.1f}  stdev={statistics.pstdev(soft_b):.1f}")
    print(f"hard_viol  A: mean={statistics.mean(hard_a):.2f}")
    print(f"hard_viol  B: mean={statistics.mean(hard_b):.2f}")
    print(f"paired wins: hybrid={wins_b}, cpsat_alone={wins_a}, ties={ties}  (N={len(trials)})")
    print("\nNote: N=5 is a pilot, not powered for a formal significance test (Wilcoxon signed-rank"
          " is reserved for the full N=10-20 x 4-scale sweep in design.md SS15.2). Report the win"
          " count and effect size honestly, not a p-value from an underpowered sample.")

    out_path = Path(__file__).resolve().parent / "ablation_pilot_results.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seed", "hard_a_cpsat_alone", "soft_a_cpsat_alone", "wall_a_s",
                    "hard_b_hybrid", "soft_b_hybrid", "wall_b_s"])
        for t in trials:
            w.writerow([t.seed, t.hard_a, f"{t.soft_a:.2f}", f"{t.wall_a:.2f}",
                        t.hard_b, f"{t.soft_b:.2f}", f"{t.wall_b:.2f}"])
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()

"""Head-to-head benchmark: runs each solver cold (Greedy, MIP, GA, CP-SAT) plus the full
sequential pipeline on the same instance, records wall-clock + score, prints a table, and writes
a CSV. This numerically reproduces the team's own observation (Zane=MIP, Abhish=GA, Danish=CP-SAT;
CP-SAT best quality but slowest).

    python benchmarks/compare_solvers.py --scale medium --cpsat-time-limit 300
    python benchmarks/compare_solvers.py --reference --cpsat-time-limit 300 --output benchmarks/results/ref.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from timetable.models import ProblemInstance, expand_requirements
from timetable.sample_data import generate_sample_instance, load_reference_instance
from timetable.scoring import score
from timetable.solvers.greedy import GreedySolver
from timetable.solvers.mip import MIPSolver
from timetable.solvers.ga import GASolver
from timetable.solvers.cpsat import CPSATSolver
from timetable.pipeline import run_pipeline, PipelineConfig


@dataclass
class BenchmarkRow:
    name: str
    wall_clock_s: float
    hard_violations: int
    soft_cost: float
    status: str
    sessions_scheduled: int
    sessions_total: int


def run_benchmark(problem: ProblemInstance, mip_tl: float, ga_tl: float, cpsat_tl: float) -> list[BenchmarkRow]:
    total = len(expand_requirements(problem))
    rows: list[BenchmarkRow] = []

    def record(name, solution):
        result = score(solution, problem)
        rows.append(BenchmarkRow(
            name=name, wall_clock_s=solution.wall_clock_seconds,
            hard_violations=result.hard_violations, soft_cost=result.soft_cost,
            status=solution.status, sessions_scheduled=len(solution.assignments), sessions_total=total,
        ))

    record("Greedy (seed)", GreedySolver().solve(problem, time_limit_s=5))
    record("MIP (Zane)", MIPSolver().solve(problem, time_limit_s=mip_tl))
    record("GA (Abhish)", GASolver().solve(problem, time_limit_s=ga_tl))
    record("CP-SAT (Danish)", CPSATSolver().solve(problem, time_limit_s=cpsat_tl))

    config = PipelineConfig(ga_time_limit_s=ga_tl, mip_time_limit_s=mip_tl, cpsat_time_limit_s=cpsat_tl)
    pipeline_result = run_pipeline(problem, config)
    final = pipeline_result.final
    final.wall_clock_seconds = pipeline_result.total_wall_clock_s
    record("Pipeline (final)", final)

    return rows


def print_table(rows: list[BenchmarkRow]) -> None:
    header = f"{'Solver':<18}{'wall(s)':>9}{'hard':>6}{'soft':>10}{'status':>12}{'sched/total':>14}"
    print(header)
    print("-" * len(header))
    for r in rows:
        print(f"{r.name:<18}{r.wall_clock_s:>9.1f}{r.hard_violations:>6}{r.soft_cost:>10.1f}"
              f"{r.status:>12}{f'{r.sessions_scheduled}/{r.sessions_total}':>14}")


def export_csv(rows: list[BenchmarkRow], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["solver", "wall_clock_s", "hard_violations", "soft_cost", "status",
                         "sessions_scheduled", "sessions_total", "timestamp"])
        ts = dt.datetime.now().isoformat(timespec="seconds")
        for r in rows:
            writer.writerow([r.name, f"{r.wall_clock_s:.2f}", r.hard_violations, f"{r.soft_cost:.2f}",
                             r.status, r.sessions_scheduled, r.sessions_total, ts])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark the three solvers + pipeline.")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--scale", choices=["small", "medium", "large"], default="medium")
    src.add_argument("--reference", action="store_true", help="Use the real DJSCE D1/D2/D3 dataset.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mip-time-limit", type=float, default=60)
    parser.add_argument("--ga-time-limit", type=float, default=30)
    parser.add_argument("--cpsat-time-limit", type=float, default=300)
    parser.add_argument("--output", help="CSV output path (default: benchmarks/results/run_<ts>.csv)")
    args = parser.parse_args(argv)

    if args.reference:
        problem = load_reference_instance()
        label = "DJSCE reference (D1/D2/D3)"
    else:
        problem = generate_sample_instance(args.scale, seed=args.seed)
        label = f"synthetic {args.scale}"

    print(f"Benchmarking on: {label}  ({len(problem.divisions)} divisions, "
          f"{len(expand_requirements(problem))} session requirements)\n")

    rows = run_benchmark(problem, args.mip_time_limit, args.ga_time_limit, args.cpsat_time_limit)
    print_table(rows)

    output = args.output or f"benchmarks/results/run_{dt.datetime.now():%Y%m%d_%H%M%S}.csv"
    export_csv(rows, output)
    print(f"\nWrote CSV: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

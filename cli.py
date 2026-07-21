"""Command-line entry point for the NEP timetable generator.

Examples:
    python cli.py generate --solver cpsat --scale small --out result.xlsx
    python cli.py generate --solver pipeline --reference --out timetable.xlsx --pdf timetable.pdf
    python cli.py generate --solver mip --scale medium --time-limit 120
"""
from __future__ import annotations

import argparse
import sys

from timetable.sample_data import generate_sample_instance, load_reference_instance
from timetable.scoring import score
from timetable.export import export_xlsx, export_pdf
from timetable.solvers import SOLVERS  # single-source registry (design.md §9)
from timetable.pipeline import run_pipeline, run_ensemble, PipelineConfig


def _load_problem(args):
    if args.reference:
        return load_reference_instance()
    return generate_sample_instance(args.scale, seed=args.seed)


def cmd_generate(args) -> int:
    problem = _load_problem(args)
    issues = problem.validate()
    if issues:
        print("WARNING: problem instance has structural issues:")
        for issue in issues:
            print(f"  - {issue}")

    if args.solver in ("pipeline", "ensemble"):
        config = PipelineConfig(
            cpsat_time_limit_s=args.time_limit,
            ga_time_limit_s=min(args.time_limit, 30),
            mip_time_limit_s=min(args.time_limit, 60),
        )
        runner = run_pipeline if args.solver == "pipeline" else run_ensemble
        result = runner(problem, config)
        if result.reports:
            print(f"\n=== {args.solver}: cooperative hybrid stages (each warm-starts the next) ===")
            print(f"  {'stage':<26}{'status':>10}{'hard':>6}{'soft':>9}{'wall(s)':>9}   running-best")
            for rep in result.reports:
                marker = "  <- new best" if rep.improved else ""
                print(f"  {rep.name:<26}{rep.solver_status:>10}{rep.hard_violations:>6}"
                      f"{rep.soft_cost:>9.1f}{rep.wall_clock_s:>9.1f}   "
                      f"hard={rep.running_best_hard} soft={rep.running_best_soft:.1f}{marker}")
        else:
            print(f"\n=== {args.solver} stages ===")
            for s in result.stages:
                r = score(s, problem)
                print(f"  {s.solver_name:12} status={s.status:10} hard={r.hard_violations:3} "
                      f"soft={r.soft_cost:8.1f} wall={s.wall_clock_seconds:6.1f}s")
        for note in result.notes:
            print(f"  note: {note}")
        solution = result.final
    else:
        solver = SOLVERS[args.solver]()
        solution = solver.solve(problem, time_limit_s=args.time_limit)

    result = score(solution, problem)
    print(f"\n=== final solution ({solution.solver_name}) ===")
    print(f"  status:          {solution.status}")
    print(f"  assigned:        {len(solution.assignments)}")
    print(f"  hard violations: {result.hard_violations}")
    print(f"  soft cost:       {result.soft_cost:.1f}")
    print(f"  wall clock:      {solution.wall_clock_seconds:.1f}s")
    if result.hard_violations:
        print("  hard breakdown:")
        for k, v in result.details.items():
            if k.startswith("hard:"):
                print(f"    {k[5:]}: {v}")

    if args.out:
        export_xlsx(solution, problem, args.out)
        print(f"\n  wrote Excel: {args.out}")
    if args.pdf:
        export_pdf(solution, problem, args.pdf)
        print(f"  wrote PDF:   {args.pdf}")

    return 0 if result.hard_violations == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="NEP 2020 timetable generator (MIP / GA / CP-SAT).")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate a timetable with a chosen solver or pipeline.")
    gen.add_argument("--solver", choices=list(SOLVERS) + ["pipeline", "ensemble"], default="pipeline")
    src = gen.add_mutually_exclusive_group()
    src.add_argument("--scale", choices=["small", "medium", "large"], default="small",
                     help="Synthetic instance size (ignored if --reference).")
    src.add_argument("--reference", action="store_true",
                     help="Use the real DJSCE D1/D2/D3 reference dataset.")
    gen.add_argument("--seed", type=int, default=42, help="RNG seed for synthetic data.")
    gen.add_argument("--time-limit", type=float, default=60,
                     help="Per-solver / CP-SAT stage time budget in seconds.")
    gen.add_argument("--out", help="Path to write the timetable as .xlsx")
    gen.add_argument("--pdf", help="Path to write the timetable as .pdf")
    gen.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

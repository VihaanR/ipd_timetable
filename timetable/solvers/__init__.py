"""Solver registry — the single source of truth mapping a solver name to its class.

Both `cli.py` and the web platform import `SOLVERS` from here instead of each maintaining
their own dict (design.md §9 de-duplication). `pipeline`/`ensemble` are orchestration
*functions* in `timetable.pipeline` (they combine several of these solvers), so they are not
entries in this registry — callers dispatch them separately.
"""
from __future__ import annotations

from timetable.solvers.base import SolverBase
from timetable.solvers.greedy import GreedySolver
from timetable.solvers.mip import MIPSolver
from timetable.solvers.ga import GASolver
from timetable.solvers.cpsat import CPSATSolver

# name -> solver class. Order is the canonical presentation order (fast -> exact).
SOLVERS: dict[str, type[SolverBase]] = {
    "greedy": GreedySolver,
    "mip": MIPSolver,
    "ga": GASolver,
    "cpsat": CPSATSolver,
}

__all__ = [
    "SOLVERS",
    "SolverBase",
    "GreedySolver",
    "MIPSolver",
    "GASolver",
    "CPSATSolver",
]

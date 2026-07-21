from __future__ import annotations

from abc import ABC, abstractmethod

from timetable.models import ProblemInstance, Solution


class SolverBase(ABC):
    name: str = "base"

    @abstractmethod
    def solve(self, problem: ProblemInstance, time_limit_s: float = 60,
              warm_start: Solution | None = None) -> Solution:
        ...

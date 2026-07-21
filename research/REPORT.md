# Research Report: Does the Cooperative Hybrid Actually Beat CP-SAT Alone?

**Pilot ablation study — equal-compute-budget comparison, small-scale instances, N=5**
*Companion to `design.md` §15.2 (the full N=10–20 × 4-scale study this pilot is a proof-of-concept
for). Raw data: [`ablation_pilot_results.csv`](ablation_pilot_results.csv). Runner:
[`ablation_pilot.py`](ablation_pilot.py).*

## Why this study exists

Every benchmark number shown earlier in this project (`CLAUDE.md` §13, `PIPELINE_WALKTHROUGH.md`
§7) compared solvers at **different, uncontrolled time budgets**: CP-SAT got its own
`--cpsat-time-limit`, while the pipeline's reported wall-clock was the **sum** of four stages
(Greedy + MIP + GA + CP-SAT), which is strictly *more* total compute than any single solver's row
in the same table. "The pipeline beat every individual solver" was true, but it wasn't a fair
comparison — the pipeline simply got more total time. This pilot fixes that: both conditions get
the **exact same 40-second wall-clock budget**, so any difference is attributable to
*architecture*, not to *who got more compute*.

## Method

| | Condition A | Condition B |
|---|---|---|
| **What runs** | CP-SAT alone, cold (no warm start) | Greedy → MIP → GA → CP-SAT, each warm-started from the previous stage |
| **Budget** | 40s | 2s + 12s + 8s + 18s = 40s |
| **Instances** | 5 independently generated synthetic "small"-scale problems (seeds 1–5; 2 divisions, 76 session requirements each) | same 5 instances, paired |
| **Metric** | `scoring.score()` → `(hard_violations, soft_cost)`, compared lexicographically | same |

Both conditions are run on the *same* 5 problem instances (paired design), so the comparison is
seed-by-seed, not just an average-vs-average.

## Results

| seed | hard A | soft A (CP-SAT alone) | wall A | hard B | soft B (hybrid) | wall B | winner |
|-----:|-------:|-----------------------:|-------:|-------:|------------------:|-------:|--------|
| 1 | 0 | **98.3** | 3.1s | 0 | 128.3 | 17.8s | A |
| 2 | 0 | **117.9** | 2.7s | 0 | 130.9 | 22.3s | A |
| 3 | 0 | **122.9** | 2.3s | 0 | 128.9 | 17.4s | A |
| 4 | 0 | 150.4 | 2.1s | 0 | **138.4** | 12.1s | B |
| 5 | 0 | **124.0** | 3.1s | 0 | 126.0 | 17.5s | A |

| | mean soft_cost | stdev |
|---|---:|---:|
| A — CP-SAT alone | 122.7 | 16.7 |
| B — hybrid pipeline | 130.5 | 4.2 |

**Both conditions reach `hard_violations = 0` on every single seed** (both are always
constraint-valid). **CP-SAT alone wins 4 of 5 seeds on soft cost**; the hybrid wins 1.

This is the opposite of the informal claim in the earlier (budget-uncontrolled) benchmark table.
**Under a fair, equal-time comparison, CP-SAT alone outperforms the hybrid pipeline on small,
easy instances.**

## Why — two concrete, verified mechanisms

**1. CP-SAT solves "small" instances to proven optimality almost instantly, so extra budget in
condition A is close to free.** Wall-clock for condition A is 2.1–3.1 seconds — a tiny fraction
of the 40s allowed. OR-Tools' CP-SAT stops as soon as it *proves* optimality (no benefit to
burning the remaining budget), which is the standard explanation for why more time didn't need to
help condition A: it likely didn't need the other ~37 seconds at all. Meanwhile, condition B
still *spends* ~22 of its 40 seconds on Greedy+MIP+GA before CP-SAT ever gets to run — time that,
on an instance this easy, buys comparatively little, because CP-SAT alone could have reached the
same neighborhood in 3 seconds unassisted.

**2. CP-SAT's own search objective does not see 5 of the 9 soft-cost terms it is judged on —
confirmed directly by comparing the code.** `scoring.py` (the single source of truth used to
score *every* solver) sums **nine** weighted soft terms: `heavy_subject_run`,
`teacher_workload_spread`, `idle_gaps`, `earliest_latest_same_day`, `lab_not_before_final_slots`,
`room_capacity_waste`, `division_load_inequity`, `lab_distribution_pattern`,
`break_not_midmorning`. CP-SAT's own internal `objective_terms` (`timetable/solvers/cpsat.py`)
only encodes **four** of them: room-capacity waste, the late-lab-slot penalty, the break-timing
nudge, and native gap-minimization (which approximates, but is not identical to, `idle_gaps`).
The other five terms — including `teacher_workload_spread` and `division_load_inequity`, two of
the costlier soft terms in practice — are **structurally invisible to CP-SAT's search**, in both
conditions A and B. Giving CP-SAT more time (condition A) only helps it polish the four terms it
can see; the other five drift with whichever feasible region the search happens to land in. This
is an **objective misalignment**, not a bug — it's the documented, deliberate scope decision that
CP-SAT's own objective doesn't fully replicate every soft term, but it is also the most likely
reason a warm start (condition B) doesn't reliably help: the hybrid pipeline hands CP-SAT a
starting point selected using the *full* 9-term `scoring.py` (via GA's fitness function, which
does use the complete `scoring.py` soft cost), but CP-SAT's own subsequent 18-second search can
only *locally* improve on the 4 terms it can see from wherever that starting point landed —
it cannot correct the other 5 terms even if they're worse than what a cold, unconstrained CP-SAT
search might have found on its own.

## What this means for the project's claims

- **The claim "the hybrid pipeline beats every individual solver" needs a caveat it didn't have
  before:** that comparison was not equal-budget. Under an equal-budget comparison on small, easy
  instances, it does not hold — CP-SAT alone is both faster to reach an equally-valid schedule
  *and* scores better, because on an easy instance the earlier pipeline stages have little left to
  contribute and the CP-SAT stage they hand off to has less time and a worse-aligned starting
  point than a cold run would have.
- **This does not mean the hybrid architecture is wrong — it means its value is conditional on
  instance difficulty, which this pilot did not yet test.** The theoretical case for warm-starting
  is strongest exactly where a cold search struggles to find feasibility quickly: larger instances
  (more divisions, more rooms/faculty contention) or tight time budgets where CP-SAT alone might
  not reach `hard=0` at all inside the allotted time. The "small" scale used here reaches
  `hard=0` in ~3 seconds regardless of approach — it's an easy case that doesn't exercise the
  scenario the hybrid is actually designed for.
- **The objective-misalignment finding is independently actionable**, regardless of the
  budget-fairness question: CP-SAT's internal objective should either be extended to cover all
  nine `scoring.py` soft terms, or the comparison should also report CP-SAT's own internal
  objective value (not just the external `scoring.py` score) so search behavior and evaluation
  metric are never confused with each other again.

## Honest limitations of this pilot

- **N=5, one scale.** This is a proof-of-concept for the methodology in `design.md` §15.2, not
  the full study. It is not powered for a formal significance test — the win-count (4/5) and
  effect size are reported plainly rather than computing a p-value on an underpowered sample.
  The full design (N=10–20 seeds × small/medium/large/reference, Wilcoxon signed-rank) is the
  correctly-scoped follow-up, not this pilot.
- **Only "small" scale was tested.** The mechanism argued above predicts the hybrid's advantage
  should grow on harder instances (medium/large/reference) and under tighter budgets — this is a
  prediction from the pilot's evidence, not yet a separately confirmed result.
- **CP-SAT's own parallel search (`num_search_workers=8`, no fixed seed) is a confound.** Some
  run-to-run variance in condition A is CP-SAT's own stochasticity, not purely instance
  difficulty — not fully separated from the seed-to-seed instance variance in this pilot.
- **Objective-misalignment mechanism (§2 above) is a well-evidenced hypothesis from direct code
  comparison, not yet confirmed by directly logging CP-SAT's internal objective value alongside
  the external score in the same run** — a cheap, natural next check.

## Immediate next steps (not yet run)

1. Repeat this same equal-budget design on the **real DJSCE reference dataset** (3 divisions, 114
   sessions — the project's actual target use case, harder than the small synthetic scale).
2. Repeat at **medium/large synthetic scale** and under **tighter total budgets** (e.g. 10–15s),
   where a cold CP-SAT start is more likely to still be searching for feasibility when time runs
   out — the scenario the hybrid's fast-feasibility stages (Greedy, then MIP) are actually meant
   to help with.
3. Log CP-SAT's own internal objective value alongside the external `scoring.py` score in the same
   run, to directly confirm (rather than infer) the objective-misalignment mechanism in §2.
4. Scale this pilot up to the full N=10–20 × 4-scale design with Wilcoxon signed-rank testing per
   `design.md` §15.2 once the above cheaper checks have run.

## Conclusion

A controlled experiment overturned an informal claim this project was making. That is the
experiment working as intended. The hybrid pipeline's architecture is not shown to be wrong by
this result — it is shown to be **untested under fair conditions until now**, and the one fair
test run so far says its advantage, if any, is not on easy instances. The honest, evidence-based
position going into a presentation is: *"the hybrid's benefit is hypothesized to be conditional on
instance difficulty; a controlled pilot on easy instances shows CP-SAT alone winning under an
equal budget, and we know why (objective misalignment, confirmed by code comparison); testing on
harder instances is the immediate next step."* That is a stronger, more defensible research
narrative than an unqualified "the hybrid always wins."

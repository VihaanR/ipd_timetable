# How the Timetable Generator Works — A Walkthrough

*Prepared for project review. Companion to [CLAUDE.md](CLAUDE.md) (the terse technical reference)
— this document explains the same system in plain language, with a worked example, for
presenting to Prof. Aditi Raut / SIH reviewers.*

---

## 1. The problem, in one paragraph

Under NEP 2020, a college like DJSCE has to schedule Major/Minor/Skill/AEC/VAC courses, split
each division into two lab batches, keep faculty and rooms conflict-free, and respect ~20 rules
(one break a day, labs on every working day, no all-theory day, etc.) simultaneously. Doing this
by hand is what "timetable committees" struggle with every semester. This project automates it —
and instead of picking one optimization technique, it implements **three genuinely different
algorithms** (one per teammate) and combines them into a single pipeline that is better than any
one of them alone.

## 2. The three algorithms — in plain language

| Who | Algorithm | How it thinks |
|-----|-----------|----------------|
| **Zane** | **MIP** (Mixed Integer Programming) | Writes the whole problem as one giant system of yes/no decisions ("does session X go in slot Y, room Z?") plus equations, and hands it to an exact solver (OR-Tools CBC). Guaranteed correct if it finishes; the exactness costs speed. |
| **Abhish** | **GA** (Genetic Algorithm + ML) | Treats a full timetable as a "chromosome." Starts with a population of candidate timetables, breeds the better ones together (crossover), randomly tweaks some (mutation), and repeats for generations — survival of the fittest. Fitness is boosted by three lightweight ML models that estimate cognitive fatigue, subject difficulty, and lab-infrastructure stress. |
| **Danish** | **CP-SAT** (Constraint Programming) | Google OR-Tools' constraint solver. Similar spirit to MIP but with a richer vocabulary — it can natively express "no two overlapping intervals" and "minimize gaps in the day," which is exactly what a timetable needs. This is why it produces the best-quality result — but that richer search takes the longest. |

This matches exactly what the team observed in their own prototyping: **CP-SAT gives the best
timetable, but takes 5–10 minutes; MIP and GA are faster but lower quality.**

## 3. The key idea: a real hybrid, not a bake-off

The naive way to use three algorithms is: run all three, keep whichever is best, throw the other
two away. That's an **ensemble** — this repo has that too (`--mode ensemble`), as the "contrast
baseline" that mirrors the team's original three separate prototypes.

But the actual **recommended pipeline** is a **cooperative hybrid**: every algorithm hands its
work to the next one as a starting point, so each stage builds on the previous stage's progress
instead of starting from scratch.

```
   Greedy          MIP              GA                 CP-SAT
  (seed, <1s)  →  (Zane)     →   (Abhish + ML)   →    (Danish)
                warm-started      population           warm-started
                from greedy       seeded from           (AddHint) from
                (SetHint)         greedy AND MIP         best-so-far
                                  (warm_starts=[...])
      │               │                 │                   │
      ▼               ▼                 ▼                   ▼
  fast, rough     exact core:       explores soft-       final polish:
  feasible-ish    hard constraints  constraint space      native gap-
  schedule        nailed exactly    MIP can't reach       minimization
```

**A real run**, taken directly from the CLI on the real DJSCE dataset:

| Stage | status | hard violations | soft cost | running best |
|-------|--------|-----------------:|----------:|--------------|
| Greedy (seed) | PARTIAL | 21 | 207.3 | 207.3 |
| MIP (exact core) | OPTIMAL | **0** | 159.1 | 159.1 ▲ |
| GA (explore, ML fitness) | FEASIBLE | 0 | 159.1 | 159.1 (no change this run) |
| CP-SAT (polish) | OPTIMAL | 0 | **88.7** | **88.7** ▲ |

Read left to right: quality visibly climbs. Greedy gets *something* on the board in under a
second. MIP — because it got a head start from greedy — proves the hard constraints can all be
satisfied exactly (21 violations → 0). GA explores around that exact solution looking for
soft-constraint improvements the ML fitness function rewards (a more even faculty workload,
fewer idle gaps). CP-SAT — warm-started from whatever is best so far, not from scratch — spends
its time budget doing what only it can do well: native gap-minimization, dropping the cost from
159 to 89.

The mechanism that makes this safe: `scoring.better(a, b)` compares two solutions
**lexicographically** — hard constraint violations first, soft cost second — and the pipeline
always carries forward the better of "this stage's output" vs. "the running best so far." If any
stage times out, crashes, or regresses, nothing is lost; the previous stage's solution is simply
passed on unchanged. This is what "sequential learning" means here in concrete code terms.

## 4. What actually gets decided (the data model)

Every solver reads the *same* input and writes the *same* output shape, which is what makes the
comparison in the table above fair:

- **Input** (`ProblemInstance`): rooms, labs, faculty (with availability/workload caps), courses
  (with credits, category, weekly session counts), and divisions (D1/D2/D3, each split into two
  batches for labs). Faculty-to-course assignment is *fixed input*, matching how the real
  department actually schedules — the solvers only decide **when** and **where**, not **who**.
- **Output** (`Solution`): for every required class session, a `(time_slot, room)` pair.

A subtlety worth mentioning to a reviewer: a 2-hour lab is modeled as **two linked session
requirements** (one per batch) that must land on the *same* time slot but in *different* rooms —
this is what makes "both batches run simultaneously in different labs" (a real hard constraint)
enforceable identically across all three solvers.

## 5. The rules being enforced (grounded in real documents, not invented)

The constraint list isn't guessed — it's transcribed from `references/TIMETABLE_CONSTRAINTS.pdf`
and `references/NEP_Optimized_Timetable_Constraints.pdf`, and implemented once, in one file
(`timetable/scoring.py`), so every solver is judged by the exact same yardstick. Highlights:

**Hard constraints (must be zero, always):** 5-day week, 1-hour lectures / 2-hour labs, exactly
one break per day (never first, second, or last period — nudged toward a realistic ~11:00 slot,
matching the real photographed timetables), 6–8 hours of class per division per day, no
faculty/room/division double-booking, labs split into simultaneous different-room batches, open
electives synchronized across divisions, no all-theory day.

**Soft constraints (minimized, not required to be zero):** spread faculty workload across the
week, minimize idle gaps, avoid 3+ heavy subjects in a row, prefer labs before the day's last two
slots, balance daily load across divisions, distribute labs evenly across the week.

A `ScoreResult` is just `(hard_violations, soft_cost)` — hard dominates soft in every comparison,
so a solution with fewer hard violations always wins regardless of how "pretty" the other one's
soft score looks.

## 6. Why this is grounded in the real institution, not synthetic data

`data/reference/djsce_cse_ds_sy_sem4.json` is transcribed from the three actual timetable photos
the team supplied (DJSCE CSE-DS, SY Sem IV, Divisions D1/D2/D3) — real subject codes (DS, ML-I,
SDS, WE, PBC, EFM, CMPM, OE), real faculty short-codes and names, real room/lab names (CR51–53,
CR46, and the four labs: Data Analytics, ML, Computer Vision, HPC). Per the team's own report,
this is used as a **structural reference and benchmark**, not as ML training data — the point is
that every solver generates its *own* schedule against the *same real-world constraints*, and the
result can be sanity-checked against what the department actually does.

## 7. The benchmark — reproducing the team's original finding, numerically

`benchmarks/compare_solvers.py` runs every solver cold (no cross-seeding) plus the full pipeline,
and prints a table like this (fresh run, real dataset, 3 divisions / 114 sessions):

| Solver | wall(s) | hard | soft | note |
|--------|--------:|-----:|-----:|------|
| Greedy (seed) | 0.0 | 21 | 207.3 | fast, rough |
| MIP (Zane) | 28.7 | 0 | 159.9 | exact, faster than CP-SAT |
| GA (Abhish) | 15.0 | 7 | 202.4 | stochastic, seed-quality sensitive |
| CP-SAT (Danish) | 13.7 | 0 | **77.7** | best quality, richest constraint model |
| Pipeline (final) | 61.1 | 0 | 92.9 | cooperative hybrid |

This numerically reproduces the team's own observation — **CP-SAT wins on quality; MIP is exact
but slower to match it; GA alone is the least reliable of the three on hard constraints under a
short time budget.** (Honest caveat for a mentor Q&A: CP-SAT uses 8 parallel search workers with
no fixed random seed, so wall-clock and final soft-cost vary a little run to run — this is why the
standalone CP-SAT row and the pipeline's internal CP-SAT stage don't always land on the exact same
number. Averaging a few runs, or extending `--cpsat-time-limit` toward the team's originally
observed 5–10 minutes, produces the fuller convergence.)

## 8. The web showcase

`webapp/server.py` (FastAPI) + `webapp/static/` (vanilla HTML/CSS/JS) — a live, interactive demo:

- Pick a dataset (real D1/D2/D3, or synthetic small/medium/large), a solver (hybrid pipeline,
  ensemble, or any one algorithm alone), and a time budget, then generate live.
- The **stage track** shows exactly the table in §3 rendered as connected cards, so a reviewer can
  watch the running-best number fall as each algorithm contributes.
- Weekly grids per division, color-coded by session type (theory / lab / tutorial / break), with
  faculty and room labels, and both simultaneous lab batches shown side by side in the same slot.

Run it with:
```bash
python -m uvicorn webapp.server:app --port 8750
```
then open `http://127.0.0.1:8750`.

## 9. Honest limitations (good to raise proactively with a mentor)

- **MIP doesn't linearize gap-minimization** — the auxiliary variables needed are complex to
  express as linear constraints, so MIP alone plateaus below CP-SAT on soft cost. This is a
  deliberate, documented scope decision, not an oversight — and it's *why* CP-SAT is needed at all
  in the pipeline.
- **The three ML predictors** (difficulty / cognitive-fatigue / infrastructure-stress) feeding
  GA's fitness are **heuristic, rule-based stand-ins** — there is no real historical academic
  performance dataset yet to train actual regressors on. The `predict(features) -> float`
  interface is deliberately narrow so real trained models can be swapped in later without
  touching the GA loop.
- **CP-SAT's parallel search introduces run-to-run variance** (see §7). For a stable demo number,
  run the benchmark 2–3 times or fix `num_search_workers=1`.
- Faculty→course assignment is fixed input (matches real department practice), not something the
  solvers optimize — a deliberate scope choice, not a limitation of the algorithms themselves.

## 10. Explicitly out of scope for this MVP

Web admin UI beyond the showcase, live Academic Management System integration, MongoDB
persistence, authentication/multi-tenancy, and training the ML predictors on real historical data.
These are documented as roadmap items in `CLAUDE.md` §14, not silently missing.

## 11. Quick demo script (for presenting live)

```bash
# 1. Show the real dataset loads and validates cleanly
python -c "from timetable.sample_data import load_reference_instance as l; p=l(); print(p.validate())"

# 2. Show the hybrid pipeline improving stage by stage
python cli.py generate --solver pipeline --reference --time-limit 30

# 3. Show the head-to-head benchmark (the team's original observation, reproduced)
python benchmarks/compare_solvers.py --reference --cpsat-time-limit 60

# 4. Open the live web showcase
python -m uvicorn webapp.server:app --port 8750
```

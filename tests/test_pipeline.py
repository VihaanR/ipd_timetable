from timetable.pipeline import run_pipeline, run_ensemble, PipelineConfig
from timetable.scoring import score


def test_pipeline_never_regresses_quality(small_problem):
    cfg = PipelineConfig(greedy_time_limit_s=3, ga_time_limit_s=6, cpsat_time_limit_s=15)
    result = run_pipeline(small_problem, cfg)
    final_key = score(result.final, small_problem).key()
    # final must be at least as good (lexicographically) as every individual stage
    for stage in result.stages:
        assert final_key <= score(stage, small_problem).key()


def test_pipeline_reaches_zero_hard_violations(small_problem):
    cfg = PipelineConfig(greedy_time_limit_s=3, ga_time_limit_s=6, cpsat_time_limit_s=20)
    result = run_pipeline(small_problem, cfg)
    assert score(result.final, small_problem).hard_violations == 0


def test_pipeline_runs_all_four_stages(small_problem):
    cfg = PipelineConfig(greedy_time_limit_s=3, mip_time_limit_s=20, ga_time_limit_s=6, cpsat_time_limit_s=15)
    result = run_pipeline(small_problem, cfg)
    # cooperative hybrid: Greedy -> MIP -> GA -> CP-SAT
    assert len(result.stages) == 4
    assert [r.name for r in result.reports][0].startswith("Greedy")
    assert any("MIP" in r.name for r in result.reports)
    assert any("GA" in r.name for r in result.reports)
    assert any("CP-SAT" in r.name for r in result.reports)


def test_pipeline_running_best_is_monotonic(small_problem):
    cfg = PipelineConfig(greedy_time_limit_s=3, mip_time_limit_s=20, ga_time_limit_s=6, cpsat_time_limit_s=15)
    result = run_pipeline(small_problem, cfg)
    keys = [(r.running_best_hard, r.running_best_soft) for r in result.reports]
    for earlier, later in zip(keys, keys[1:]):
        assert later <= earlier  # running best never regresses across stages


def test_pipeline_without_cpsat_still_returns_final(small_problem):
    cfg = PipelineConfig(greedy_time_limit_s=3, mip_time_limit_s=20, ga_time_limit_s=6, run_cpsat=False)
    result = run_pipeline(small_problem, cfg)
    assert result.final is not None
    assert len(result.stages) == 3  # greedy + MIP + GA (no CP-SAT)


def test_ensemble_returns_best_of_three(small_problem):
    cfg = PipelineConfig(mip_time_limit_s=30, ga_time_limit_s=6, cpsat_time_limit_s=20)
    result = run_ensemble(small_problem, cfg)
    assert len(result.stages) == 3
    final_key = score(result.final, small_problem).key()
    for stage in result.stages:
        assert final_key <= score(stage, small_problem).key()

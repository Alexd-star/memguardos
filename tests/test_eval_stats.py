from memguard.eval.stats import (
    mcnemar_exact,
    paired_bootstrap_mean_difference,
    wilcoxon_signed_rank,
    wilson_interval,
)


def test_wilson_interval_is_bounded_and_reports_sample_size():
    result = wilson_interval(8, 10)
    assert result["point"] == 0.8
    assert 0.0 < result["ci_low"] < result["point"]
    assert result["point"] < result["ci_high"] < 1.0
    assert result["n"] == 10


def test_mcnemar_exact_uses_only_discordant_pairs():
    result = mcnemar_exact(
        [False, False, True, True, True],
        [True, True, True, True, False],
    )
    assert result["improved"] == 2
    assert result["regressed"] == 1
    assert result["discordant"] == 3
    assert 0.0 <= result["p_value"] <= 1.0


def test_paired_bootstrap_is_deterministic_and_preserves_direction():
    first = paired_bootstrap_mean_difference([4, 5, 6], [2, 3, 4], iterations=500)
    second = paired_bootstrap_mean_difference([4, 5, 6], [2, 3, 4], iterations=500)
    assert first == second
    assert first["mean_difference"] == -2.0
    assert first["ci_high"] < 0


def test_wilcoxon_handles_ties_and_zero_differences():
    result = wilcoxon_signed_rank([1, 2, 3, 4], [1, 1, 2, 3])
    assert result["n"] == 3
    assert result["zero_differences"] == 1
    assert 0.0 <= result["p_value"] <= 1.0

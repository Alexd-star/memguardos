from __future__ import annotations

from collections import defaultdict
from math import comb, sqrt
from random import Random
from statistics import mean
from typing import Dict, Iterable, Sequence


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> Dict[str, object]:
    successes = max(0, int(successes))
    total = max(0, int(total))
    if total == 0:
        return {"point": None, "ci_low": None, "ci_high": None, "n": 0, "method": "wilson_95"}
    point = successes / total
    denominator = 1.0 + z * z / total
    centre = (point + z * z / (2.0 * total)) / denominator
    margin = z * sqrt((point * (1.0 - point) + z * z / (4.0 * total)) / total) / denominator
    return {
        "point": round(point, 6),
        "ci_low": round(max(0.0, centre - margin), 6),
        "ci_high": round(min(1.0, centre + margin), 6),
        "n": total,
        "method": "wilson_95",
    }


def mcnemar_exact(baseline: Sequence[bool], treatment: Sequence[bool]) -> Dict[str, object]:
    _same_length(baseline, treatment)
    improved = sum(1 for left, right in zip(baseline, treatment) if not left and right)
    regressed = sum(1 for left, right in zip(baseline, treatment) if left and not right)
    discordant = improved + regressed
    if discordant == 0:
        p_value = 1.0
    else:
        tail = sum(comb(discordant, index) for index in range(min(improved, regressed) + 1))
        p_value = min(1.0, 2.0 * tail / (2**discordant))
    return {
        "improved": improved,
        "regressed": regressed,
        "discordant": discordant,
        "p_value": round(p_value, 8),
        "method": "mcnemar_exact_two_sided",
    }


def paired_bootstrap_mean_difference(
    baseline: Sequence[float],
    treatment: Sequence[float],
    iterations: int = 5000,
    seed: int = 20260727,
) -> Dict[str, object]:
    _same_length(baseline, treatment)
    differences = [float(right) - float(left) for left, right in zip(baseline, treatment)]
    if not differences:
        return {"mean_difference": None, "ci_low": None, "ci_high": None, "n": 0, "method": "paired_bootstrap_95"}
    rng = Random(seed)
    sample_count = max(200, int(iterations))
    samples = sorted(
        mean(differences[rng.randrange(len(differences))] for _ in differences)
        for _ in range(sample_count)
    )
    low = samples[int(0.025 * (sample_count - 1))]
    high = samples[int(0.975 * (sample_count - 1))]
    return {
        "mean_difference": round(mean(differences), 6),
        "ci_low": round(low, 6),
        "ci_high": round(high, 6),
        "n": len(differences),
        "method": "paired_bootstrap_95",
        "seed": seed,
        "iterations": sample_count,
    }


def wilcoxon_signed_rank(baseline: Sequence[float], treatment: Sequence[float]) -> Dict[str, object]:
    _same_length(baseline, treatment)
    differences = [float(right) - float(left) for left, right in zip(baseline, treatment)]
    nonzero = [value for value in differences if value != 0.0]
    if not nonzero:
        return {"statistic": 0.0, "p_value": 1.0, "n": 0, "method": "wilcoxon_exact_two_sided"}

    ordered = sorted(enumerate(nonzero), key=lambda item: abs(item[1]))
    rank_twice = [0] * len(nonzero)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and abs(ordered[end][1]) == abs(ordered[cursor][1]):
            end += 1
        average_rank_twice = (cursor + 1) + end
        for index in range(cursor, end):
            rank_twice[ordered[index][0]] = average_rank_twice
        cursor = end

    positive = sum(rank for rank, delta in zip(rank_twice, nonzero) if delta > 0)
    total = sum(rank_twice)
    statistic_twice = min(positive, total - positive)
    distribution = {0: 1}
    for rank in rank_twice:
        updated = defaultdict(int)
        for score, count in distribution.items():
            updated[score] += count
            updated[score + rank] += count
        distribution = dict(updated)
    extreme = sum(
        count
        for score, count in distribution.items()
        if score <= statistic_twice or score >= total - statistic_twice
    )
    p_value = min(1.0, extreme / float(2 ** len(rank_twice)))
    return {
        "statistic": round(statistic_twice / 2.0, 6),
        "p_value": round(p_value, 8),
        "n": len(nonzero),
        "zero_differences": len(differences) - len(nonzero),
        "method": "wilcoxon_exact_two_sided",
    }


def _same_length(left: Iterable[object], right: Iterable[object]) -> None:
    if len(left) != len(right):  # type: ignore[arg-type]
        raise ValueError("paired samples must have the same length")

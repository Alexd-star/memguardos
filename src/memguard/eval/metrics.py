from typing import Iterable, List, Set


def recall_at_k(expected: Iterable[str], actual: Iterable[str], k: int) -> float:
    expected_set: Set[str] = set(expected)
    if not expected_set:
        return 1.0
    actual_set = set(list(actual)[:k])
    return len(expected_set & actual_set) / float(len(expected_set))


def precision(expected: Iterable[str], actual: Iterable[str]) -> float:
    expected_set = set(expected)
    actual_set = set(actual)
    if not actual_set:
        return 1.0 if not expected_set else 0.0
    return len(expected_set & actual_set) / float(len(actual_set))


def f1(expected: Iterable[str], actual: Iterable[str]) -> float:
    actual_list = list(actual)
    p = precision(expected, actual)
    r = recall_at_k(expected, actual_list, k=len(actual_list))
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def mean(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return sum(values_list) / float(len(values_list))


def percentile(values: Iterable[float], pct: float) -> float:
    values_list: List[float] = sorted(values)
    if not values_list:
        return 0.0
    if len(values_list) == 1:
        return values_list[0]
    rank = (len(values_list) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(values_list) - 1)
    weight = rank - lower
    return values_list[lower] * (1 - weight) + values_list[upper] * weight

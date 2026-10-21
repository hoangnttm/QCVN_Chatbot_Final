"""Statistics for the evaluation (plan §3.8), stdlib only: bootstrap and Wilson intervals,
cluster bootstrap, exact McNemar, paired bootstrap / sign-flip permutation, Holm–Bonferroni,
Cohen's κ, PABAK, quadratic-weighted κ, Krippendorff's α (ordinal, two raters)."""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from typing import Any


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(
    values: list[float], *, b: int = 1000, seed: int = 20260924, clusters: list[Any] | None = None
) -> tuple[float, float]:
    """Percentile 95 % CI of the mean; with `clusters` (same length) resamples whole clusters."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    if clusters is None:
        n = len(values)
        means = []
        for _ in range(b):
            s = 0.0
            for _ in range(n):
                s += values[rng.randrange(n)]
            means.append(s / n)
    else:
        groups: dict[Any, list[float]] = defaultdict(list)
        for v, c in zip(values, clusters, strict=False):
            groups[c].append(v)
        keys = list(groups)
        means = []
        for _ in range(b):
            picked = [groups[keys[rng.randrange(len(keys))]] for _ in keys]
            flat = [v for g in picked for v in g]
            means.append(sum(flat) / len(flat))
    means.sort()
    lo = means[int(0.025 * (b - 1))]
    hi = means[int(0.975 * (b - 1))]
    return (lo, hi)


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value on discordant pairs (b: A right/B wrong, c: the reverse)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / (2**n)
    return min(1.0, 2 * p)


def paired_bootstrap(
    diffs: list[float], *, b: int = 1000, seed: int = 20260924
) -> tuple[float, float]:
    return bootstrap_ci(diffs, b=b, seed=seed)


def permutation_p(diffs: list[float], *, n_perm: int = 10000, seed: int = 20260924) -> float:
    if not diffs:
        return 1.0
    rng = random.Random(seed)
    obs = abs(sum(diffs))
    hits = 0
    for _ in range(n_perm):
        s = sum(d if rng.random() < 0.5 else -d for d in diffs)
        if abs(s) >= obs - 1e-12:
            hits += 1
    return (hits + 1) / (n_perm + 1)


def cohens_dz(diffs: list[float]) -> float | None:
    if len(diffs) < 2:
        return None
    m = sum(diffs) / len(diffs)
    var = sum((d - m) ** 2 for d in diffs) / (len(diffs) - 1)
    sd = math.sqrt(var)
    return m / sd if sd > 0 else None


def cohens_h(p1: float, p2: float) -> float:
    return 2 * math.asin(math.sqrt(p1)) - 2 * math.asin(math.sqrt(p2))


def holm(pvals: dict[str, float], alpha: float = 0.05) -> dict[str, dict[str, Any]]:
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    m = len(items)
    out = {}
    reject_so_far = True
    for i, (name, p) in enumerate(items):
        adj = min(1.0, (m - i) * p)
        reject = reject_so_far and adj <= alpha
        if not reject:
            reject_so_far = False
        out[name] = {"p": p, "p_holm": adj, "reject": reject}
    return out


def cohen_kappa(pairs: list[tuple[Any, Any]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for a, b in pairs if a == b) / n
    ca = Counter(a for a, _ in pairs)
    cb = Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def pabak(pairs: list[tuple[Any, Any]]) -> float | None:
    if not pairs:
        return None
    po = sum(1 for a, b in pairs if a == b) / len(pairs)
    return 2 * po - 1


def weighted_kappa(pairs: list[tuple[int, int]], levels: int = 4) -> float | None:
    """Quadratic-weighted κ on an ordinal 0..levels-1 scale."""
    if not pairs:
        return None
    n = len(pairs)
    obs = [[0.0] * levels for _ in range(levels)]
    for a, b in pairs:
        obs[a][b] += 1
    ra = [sum(obs[i]) for i in range(levels)]
    rb = [sum(obs[i][j] for i in range(levels)) for j in range(levels)]
    num = 0.0
    den = 0.0
    for i in range(levels):
        for j in range(levels):
            w = ((i - j) ** 2) / ((levels - 1) ** 2)
            num += w * obs[i][j] / n
            den += w * (ra[i] / n) * (rb[j] / n)
    return 1.0 if den == 0 else 1 - num / den


def krippendorff_alpha_ordinal(pairs: list[tuple[int, int]], levels: int = 4) -> float | None:
    """Krippendorff's α for two raters with ordinal metric (Krippendorff 2011)."""
    if len(pairs) < 2:
        return None
    values = [v for p in pairs for v in p]
    n = len(values)
    counts = Counter(values)
    cats = sorted(counts)

    def delta(c: int, k: int) -> float:
        if c == k:
            return 0.0
        lo, hi = min(c, k), max(c, k)
        inner = sum(counts[g] for g in cats if lo < g < hi)
        return (inner + (counts[lo] + counts[hi]) / 2) ** 2

    do = 0.0
    for a, b in pairs:
        do += 2 * delta(a, b)  # each unit has 2 values → m_u=2, pairs (a,b),(b,a)
    do = do / n
    de = 0.0
    for c in cats:
        for k in cats:
            de += counts[c] * counts[k] * delta(c, k)
    de = de / (n * (n - 1))
    if de == 0:
        return 1.0
    return 1 - do / de


def summarize_metric(
    values: list[float | None], *, clusters: list[Any] | None = None, seed: int = 20260924
) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return {"n": 0, "mean": None}
    out: dict[str, Any] = {"n": len(xs), "mean": sum(xs) / len(xs)}
    cl = None
    if clusters is not None:
        cl = [c for v, c in zip(values, clusters, strict=False) if v is not None]
    lo, hi = bootstrap_ci(xs, seed=seed, clusters=cl)
    out["ci95"] = [lo, hi]
    if all(x in (0.0, 1.0) for x in xs):
        k = int(sum(xs))
        out["wilson95"] = list(wilson_ci(k, len(xs)))
        if k == len(xs):
            out["rule_of_three_upper"] = 3 / len(xs)
    return out

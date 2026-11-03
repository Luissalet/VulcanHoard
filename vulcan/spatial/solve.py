"""Facility selection on a coverage relation: exact (MILP with HiGHS via scipy) for small and medium instances, a greedy
heuristic with a proven bound otherwise. ``cover[j]`` is the set of demand indices that site ``j`` serves in time.

* max_coverage (maximal covering location): choose at most k sites to maximise the served demand weight.
* min_sites (set covering): the cheapest set of sites serving at least ``target`` of the total demand weight.

Never silent about quality: every result says ``method``, ``optimal`` and the ``bound`` it is judged against."""

from __future__ import annotations

import itertools
from math import comb

from .errors import SpatialError

EXACT_LIMIT_CANDIDATES = 3000
EXACT_LIMIT_NNZ = 600_000
ENUMERATION_LIMIT = 20_000


def _weight(cover_sets, w) -> float:
    covered = set().union(*cover_sets) if cover_sets else set()
    return float(sum(w[i] for i in covered))


def _milp(cover, w, costs, k, target_weight, time_limit_s, relax=False):
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_matrix

    m, n = len(cover), len(w)
    rows, cols, vals = [], [], []
    by_demand: dict[int, list[int]] = {}
    for j, s in enumerate(cover):
        for i in s:
            by_demand.setdefault(i, []).append(j)
    demands = sorted(by_demand)
    pos = {i: p for p, i in enumerate(demands)}
    nv = m + len(demands)
    for i in demands:  # y_i - sum_j x_j <= 0
        r = pos[i]
        rows.append(r), cols.append(m + r), vals.append(1.0)
        for j in by_demand[i]:
            rows.append(r), cols.append(j), vals.append(-1.0)
    A = [coo_matrix((vals, (rows, cols)), shape=(len(demands), nv)).tocsr()]
    lo, hi = [np.full(len(demands), -np.inf)], [np.zeros(len(demands))]
    wy = np.zeros(nv)
    for i in demands:
        wy[m + pos[i]] = w[i]
    if k is not None:  # max coverage: sum x <= k, maximise weight
        row = np.zeros(nv)
        row[:m] = 1
        A.append(coo_matrix(row))
        lo.append([-np.inf]), hi.append([k])
        c = -wy
        if not relax:
            c[:m] = 1e-6  # prefer fewer sites among equally good sets
    else:  # set cover: weight >= target, minimise cost
        A.append(coo_matrix(wy))
        lo.append([target_weight]), hi.append([np.inf])
        c = np.zeros(nv)
        c[:m] = costs
    from scipy.sparse import vstack

    cons = LinearConstraint(vstack(A).tocsr(), np.concatenate(lo), np.concatenate(hi))
    integrality = np.zeros(nv)
    if not relax:
        integrality[:m] = 1
    res = milp(c, constraints=cons, integrality=integrality, bounds=Bounds(0, 1),
               options={"time_limit": float(time_limit_s), "mip_rel_gap": 0.0, "presolve": True})
    return res, m


def _enumerate(cover, w, k):
    """Exhaustive optimum of max coverage for tiny instances (used to cross-check the solver)."""
    best_val = -1.0
    for r in range(min(k, len(cover)) + 1):
        for combo in itertools.combinations(range(len(cover)), r):
            best_val = max(best_val, _weight([cover[j] for j in combo], w))
    return best_val


def _greedy(cover, w, k, target_weight, costs):
    covered: set[int] = set()
    chosen: list[int] = []
    remaining = set(range(len(cover)))
    while remaining:
        if k is not None and len(chosen) >= k:
            break
        if k is None and sum(w[i] for i in covered) + 1e-9 >= target_weight:
            break
        gains = {j: (sum(w[i] for i in cover[j] - covered) / (1.0 if k is not None else max(costs[j], 1e-9))) for j in remaining}
        j = max(gains, key=lambda q: (gains[q], -q))
        if gains[j] <= 0:
            break
        chosen.append(j)
        covered |= cover[j]
        remaining.discard(j)
    return chosen


def solve(cover: list[set], w: list[float], *, k: int | None = None, target: float | None = None, costs: list[float] | None = None,
          method: str = "auto", time_limit_s: float = 30.0) -> dict:
    """Exactly one of ``k`` (max coverage) or ``target`` (fraction 0..1 of total weight, set cover)."""
    if (k is None) == (target is None):
        raise SpatialError("invalid", "Give either k (maximise coverage) or target (minimise sites), not both.")
    if method not in ("auto", "exact", "greedy"):
        raise SpatialError("invalid", "method must be auto, exact or greedy.")
    m, total = len(cover), float(sum(w))
    costs = list(costs) if costs is not None else [1.0] * m
    coverable = float(sum(w[i] for i in set().union(*cover))) if cover else 0.0
    target_weight = None if target is None else target * total
    if target is not None and target_weight > coverable + 1e-9:
        raise SpatialError("infeasible", f"Even with every candidate site only {coverable / total * 100:.1f} % of the demand can be served "
                           f"in the allowed time; the target is {target * 100:.1f} %.", hint="Raise max_time, add candidates or lower the target.",
                           details={"max_achievable_pct": round(coverable / total * 100, 3) if total else 0.0})
    nnz = sum(len(s) for s in cover)
    too_big = m > EXACT_LIMIT_CANDIDATES or nnz > EXACT_LIMIT_NNZ
    if method == "exact" and too_big:
        raise SpatialError("too_large", f"Exact solving is limited to {EXACT_LIMIT_CANDIDATES} candidates / {EXACT_LIMIT_NNZ} coverage pairs "
                           f"(this has {m} / {nnz}).", hint="Use method auto or greedy: greedy reports an LP-relaxation bound.")
    use_exact = method == "exact" or (method == "auto" and not too_big)
    out = {"objective": "max_coverage" if k is not None else "min_sites", "k": k, "target": target}
    # LP-relaxation bound (valid for the integer optimum): upper bound on weight, or lower bound on cost.
    bound = None
    if cover and (use_exact or True):
        try:
            res, _ = _milp(cover, w, costs, k, target_weight, time_limit_s, relax=True)
            if res.status == 0:
                bound = float(-res.fun) if k is not None else float(res.fun)
        except Exception:  # bound is informative only
            bound = None
    if use_exact and cover:
        res, mm = _milp(cover, w, costs, k, target_weight, time_limit_s)
        if res.x is None:
            raise SpatialError("failed", f"Solver stopped without a solution ({res.message}).", hint="Raise time_limit_s or use greedy.")
        chosen = [j for j in range(m) if res.x[j] > 0.5]
        optimal = res.status == 0
        out.update(method="exact (MILP, HiGHS)", optimal=optimal, solver_status=res.message)
        if not optimal:
            gap = getattr(res, "mip_gap", None)
            out["mip_gap"] = None if gap is None else float(gap)
    else:
        chosen = _greedy(cover, w, k, target_weight, costs)
        out.update(method="greedy heuristic", optimal=False,
                   solver_status="Heuristic: no optimality guarantee; compare with the LP-relaxation bound.")
    got = _weight([cover[j] for j in chosen], w)
    if k is not None:
        out.update(bound=bound if bound is not None else coverable,
                   bound_type="lp_relaxation (upper bound on served weight)" if bound is not None else "total coverable weight")
        if not use_exact:
            out["guarantee"] = "greedy serves at least (1 - 1/e) = 63.2 % of the optimum for maximal covering"
        if cover and len(cover) <= 25 and sum(comb(len(cover), r) for r in range(min(k, len(cover)) + 1)) <= ENUMERATION_LIMIT:
            best = _enumerate(cover, w, k)
            out["enumeration_optimum"] = best
            out["verified_by_enumeration"] = abs(best - got) <= 1e-9
    else:
        out.update(bound=bound, bound_type="lp_relaxation (lower bound on total cost)")
    chosen = sorted(chosen)
    out.update(chosen=chosen, served_weight=got, total_weight=total, coverable_weight=coverable,
               served_pct=round(100 * got / total, 4) if total else 0.0, cost=float(sum(costs[j] for j in chosen)))
    return out

"""Hand-off JSON for the decision optimizer (Dantzig) and the data workbench (Nightingale). No integration is assumed: the files
are self-describing and carry their provenance."""

from __future__ import annotations

import time

SCHEMA = "vulcan.spatial.decision/1"


def export_run(run: dict, mode: str | None = None) -> dict:
    head = {"schema": SCHEMA, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": "Vulcan's Hoard spatial analysis", "run_id": run.get("run_id"),
            "scenario": run.get("scenario"), "crs": run.get("crs"), "data": run.get("data"), "parameters": run.get("parameters")}
    if run.get("kind") == "coverage":
        modes = list((run.get("instance") or {}))
        mode = mode or ("network" if "network" in modes else (modes[0] if modes else None))
        if mode not in (run.get("instance") or {}):
            raise ValueError(f"The run has no instance for mode '{mode}' (available: {modes}).")
        inst, sol = run["instance"][mode], run["solutions"][mode]
        c = run["parameters"]["coverage"]
        return {**head, "kind": "set_covering_instance", "mode": mode,
                "basis": "shortest travel time over the road network plus walking access" if mode == "network" else "straight-line distance (optimistic; not travel time)",
                "problem": {"type": c["objective"], "k": c["k"] if c["objective"] == "max_coverage" else None,
                            "target_pct": c["target_pct"] if c["objective"] == "min_sites" else None, "max_time_min": c["max_time_min"]},
                "demand": inst["demand"], "candidates": inst["candidates"],
                "solution": {"chosen": sol["chosen"], "served_pct": sol["served_pct"], "method": sol["method"], "optimal": sol.get("optimal"), "bound": sol.get("bound")},
                "notes": "Candidate 'covers' lists the demand ids served within the maximum time; weights are demand units; cost defaults to 1 per site."}
    if run.get("kind") == "screening":
        rows = [{"id": a["id"], "name": a["name"], "decision": "accepted", **a["measures"], "reasons": []} for a in run["accepted"]] + \
               [{"id": r["id"], "name": r["name"], "decision": "rejected", **r["measures"], "reasons": r["reasons"]} for r in run["rejected"]]
        return {**head, "kind": "screening_table", "rows": rows, "summary": run["summary"]}
    raise ValueError("Unknown run kind.")

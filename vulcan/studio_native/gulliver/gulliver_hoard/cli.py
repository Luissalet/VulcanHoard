"""Command line: build one model straight to files, without the app.

    python -m gulliver_hoard.cli --place "Móstoles, España" --radius-km 1.2 --size 180 --out mostoles
    python -m gulliver_hoard.cli --bbox=-3.88,40.31,-3.85,40.33 --preset landscape --tiles 2x2 --out sierra

Writes the same files as the app (STL, 3MF, tiles, preview, report.json) into the --out folder."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .config import REPO_ROOT
from .geo import Area
from .mesh.build import build
from .outputs import write_outputs
from .params import PRESETS, ModelParams
from .sources import Sources
from .sources.http import Fetcher


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m gulliver_hoard.cli", description="Printable 3D map model from open data")
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument("--place", help="place name (OpenStreetMap search)")
    where.add_argument("--bbox", help="lon_min,lat_min,lon_max,lat_max (write --bbox=... when it starts with a minus)")
    where.add_argument("--center", help="lon,lat with --width-m / --height-m (write --center=... when it starts with a minus)")
    ap.add_argument("--radius-km", type=float, default=1.0, help="half the side around --place")
    ap.add_argument("--width-m", type=float, default=2000.0)
    ap.add_argument("--height-m", type=float, default=2000.0)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="city")
    ap.add_argument("--size", type=float, default=None, help="longest side in mm")
    ap.add_argument("--zexag", type=float, default=None, help="vertical exaggeration")
    ap.add_argument("--shape", choices=["rect", "rounded", "circle", "hexagon"], default="rect")
    ap.add_argument("--tiles", default="1x1", help="columns x rows, e.g. 2x2")
    ap.add_argument("--mono", action="store_true", help="one piece instead of colour parts")
    ap.add_argument("--name", default="")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--cache", default=str(REPO_ROOT / "data" / "cache"), help="download cache folder")
    args = ap.parse_args(argv)

    fetcher = Fetcher(Path(args.cache))
    sources = Sources(fetcher)
    name = args.name
    if args.place:
        hits = sources.search(args.place, 1)
        if not hits:
            print(f"Place not found: {args.place}", file=sys.stderr)
            return 1
        lon, lat = hits[0]["lon"], hits[0]["lat"]
        w = h = args.radius_km * 2000.0
        name = name or hits[0]["name"].split(",")[0]
    elif args.bbox:
        area = Area.from_bbox(*map(float, args.bbox.split(",")))
        lon, lat, w, h = area.lon, area.lat, area.width_m, area.height_m
    else:
        lon, lat = map(float, args.center.split(","))
        w, h = args.width_m, args.height_m
    tx, ty = (int(v) for v in args.tiles.lower().split("x"))
    values = {**PRESETS[args.preset], "lon": lon, "lat": lat, "width_m": w, "height_m": h, "shape": args.shape, "tiles_x": tx, "tiles_y": ty}
    if args.size:
        values["size_mm"] = args.size
    if args.zexag:
        values["z_exag"] = args.zexag
    if args.mono:
        values["color"] = "mono"
    params = ModelParams(**values)
    name = name or f"{lat:.4f}, {lon:.4f}"

    def progress(fraction: float, message: str) -> None:
        print(f"[{fraction * 100:3.0f}%] {message}" if fraction >= 0 else f"       {message}", flush=True)

    result = build(params, sources, progress=progress, name=name)
    report = write_outputs(result, Path(args.out), name=name, progress=progress)
    print(json.dumps({k: report[k] for k in ("scale", "size_mm", "weight_g_pla", "files", "check")}, ensure_ascii=False, indent=1))
    fetcher.close()
    return 0 if report["check"]["watertight"] and not math.isnan(report["volume_cm3"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())

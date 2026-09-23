"""Self-test: scan a folder of models into a temporary data dir and print stats and timings.

    python scripts/selftest.py <folder> [--no-thumbs] [--query "..."]...

Never touches the real data directory.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from vulcan.config import Config  # noqa: E402
from vulcan.search import Filters  # noqa: E402
from vulcan.services import Services  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", help="Folder with STL/3MF/OBJ files")
    parser.add_argument("--no-thumbs", action="store_true", help="Skip thumbnail rendering")
    parser.add_argument("--workers", type=int, default=None, help="Worker processes (default: VULCAN_SCAN_WORKERS or cpu_count - 2)")
    parser.add_argument("--query", action="append", help="Search query to run afterwards (repeatable)")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        return 2
    with tempfile.TemporaryDirectory(prefix="vulcan-selftest-") as tmp:
        config = Config(data_dir=Path(tmp) / "data", watch=False, autostart=False, thumbnails=not args.no_thumbs, data_dir_configured=True)
        if args.workers:
            config.scan_workers = max(1, args.workers)
        print(f"workers: {config.scan_workers}")
        services = Services(config)
        services.worker.start()
        try:
            started = time.time()
            root = services.add_root("selftest", str(folder), None, None, False)
            last = -1
            while not services.worker.wait_idle(1.0):
                progress = services.worker.progress(root.id) or {}
                if progress.get("files_done") != last:
                    last = progress.get("files_done")
                    print(f"  {progress.get('phase')}: {last}/{progress.get('files_total')} {progress.get('current_file', '')[:60]}", flush=True)
            progress = services.worker.progress(root.id)
            elapsed = time.time() - started
            counts = services.stats.counts()
            per_file = elapsed / max(1, progress["files_total"])
            rate = progress["rate"] or 0.0
            print(f"scanned {counts['models']} models in {elapsed:.1f}s ({per_file * 1000:.0f} ms/file, {rate:.2f} parsed files/s): "
                  f"{progress['files_changed']} parsed, {progress['thumbs_rendered']} thumbnails, {progress['files_skipped']} skipped, {progress['error_count']} errors")
            print(f"  {counts['triangles']:,} triangles · {counts['bytes'] / 1e6:.1f} MB · watertight {counts['watertight']} / open {counts['not_watertight']} · "
                  f"duplicates {counts['duplicates']} · odd units {counts['odd_units']}")
            for entry in counts["by_format"]:
                print(f"  {entry['format']}: {entry['models']} files, {entry['triangles']:,} triangles")
            for error in progress["errors"][:10]:
                print(f"  ! {error['path']}: {error['error']}")
            near = services.dupes.near(20)
            print(f"  near-duplicate groups: {len(near)}")
            for group in near[:5]:
                print("    " + " | ".join(m["rel_path"] for m in group["models"]))
            for query in args.query or []:
                started = time.time()
                result = services.search.query(Filters(q=query, sort="relevance", limit=args.limit))
                print(f"\n«{query}» — {result['total']} hits — {(time.time() - started) * 1000:.0f} ms")
                for model in result["models"]:
                    bbox = " × ".join(f"{v:.1f}" for v in model["bbox"]) if model["bbox"] else "—"
                    print(f"  #{model['id']} {model['name']} [{model['format']}] {bbox} mm · {model['triangles']} tri · {model['rel_path']}")
        finally:
            services.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""A build runs in its own process: the geometry work is long and CPU-bound, and in the server's process it would starve the
web server (pages, the health check, the agent tools) for minutes. The child builds and writes the files, and sends its
progress, then the report, back through a queue; cancelling a job ends the child."""

from __future__ import annotations

import multiprocessing as mp
import queue
import traceback
from pathlib import Path
from typing import Any, Optional

from .errors import Cancelled, GulliverError


def child_main(q: Any, args: dict[str, Any]) -> None:
    """Entry point of the build process (top level, so the spawn start method can import it)."""
    try:
        from .mesh.build import build
        from .outputs import write_outputs
        from .params import ModelParams
        from .sources import Sources
        from .sources.fake import FakeSources
        from .sources.http import Fetcher

        fetcher = Fetcher(Path(args["cache_dir"]))
        sources = FakeSources(fetcher) if args.get("fake") else Sources(fetcher, overpass_servers=args.get("servers") or None,
                                                                        keys=args.get("keys") or None)

        def progress(fraction: float, message: str) -> None:
            q.put(("progress", fraction, message))

        params = ModelParams(**args["params"])
        result = build(params, sources, progress=progress, name=args["name"])
        report = write_outputs(result, Path(args["folder"]), name=args["name"], bed=tuple(args["bed"]), progress=progress,
                               lang=args["lang"])
        q.put(("done", report))
    except GulliverError as exc:
        q.put(("error", exc.code, exc.message, exc.hint))
    except BaseException:  # noqa: BLE001 - everything is reported to the parent
        q.put(("crash", traceback.format_exc()[-4000:]))


def run_isolated(job: Any, args: dict[str, Any], *, poll_s: float = 0.4) -> dict[str, Any]:
    """Run :func:`child_main` in a new process, relaying its progress to ``job`` until it answers."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    proc = ctx.Process(target=child_main, args=(q, args), name="gulliver-build", daemon=True)
    proc.start()
    try:
        while True:
            if job.cancelled():
                raise Cancelled()
            try:
                msg = q.get(timeout=poll_s)
            except queue.Empty:
                if not proc.is_alive():
                    try:
                        msg = q.get(timeout=1.0)          # it may have answered just before ending
                    except queue.Empty:
                        raise GulliverError("crashed", f"The build process ended unexpectedly (exit code {proc.exitcode}).",
                                            "It may have run out of memory: try a coarser resolution or a smaller area.")
                else:
                    continue
            kind = msg[0]
            if kind == "progress":
                job.report(msg[1], msg[2])
            elif kind == "done":
                return msg[1]
            elif kind == "error":
                raise GulliverError(msg[1], msg[2], msg[3])
            else:
                raise RuntimeError("The build failed:\n" + msg[1])
    finally:
        if proc.is_alive():
            proc.terminate()
        proc.join(timeout=10)
        q.close()


def isolate_default(fake: bool) -> bool:
    import os
    flag: Optional[str] = os.environ.get("GULLIVER_ISOLATE")
    if flag is not None:
        return flag.strip().lower() not in ("0", "false", "no", "")
    return not fake

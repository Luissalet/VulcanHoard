"""Persistent single-consumer figure queue with phase checkpoints and restart recovery."""

import hashlib, json, sqlite3, threading, time, uuid
from contextlib import contextmanager
from pathlib import Path


class Cancelled(Exception):
    pass


def pipeline_signature():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    paths = [*sorted((root / "backend").glob("*.py")), root / "blender/evaluation_worker.py"]
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class Queue:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(exist_ok=True)
        self.stop = threading.Event()
        self.thread = None
        with self.db() as db:
            db.executescript("""PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,source TEXT,model TEXT,recipe TEXT,state TEXT,message TEXT,project TEXT,attempt INTEGER,created REAL,started REAL,finished REAL,cancel INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            INSERT OR IGNORE INTO settings VALUES('paused','1');""")

    def recover(self):
        with self.db() as db:
            changed = db.execute(
                "UPDATE queue SET state='interrupted',message='El servidor se reinició. Puedes reintentar desde la última fase completa.' WHERE state='running'"
            ).rowcount
            if changed:
                db.execute("UPDATE settings SET value='1' WHERE key='paused'")

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.root / "queue.sqlite", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def state(self):
        with self.db() as db:
            return {
                "paused": db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[0]
                == "1",
                "items": [
                    dict(r)
                    for r in db.execute("SELECT * FROM queue ORDER BY created DESC LIMIT 200")
                ],
            }

    def active(self):
        with self.db() as db:
            return bool(db.execute("SELECT 1 FROM queue WHERE state='running' LIMIT 1").fetchone())

    def add(self, sources, model, recipe):
        prepared = []
        for source in sources:
            path = Path(source).resolve()
            if not path.is_file() or path.suffix.lower() != ".blend":
                raise ValueError(f"No es una escena Blender: {source}")
            if path.stat().st_size > 160 * 1024 * 1024:
                raise ValueError("Máximo 160 MB por escena.")
            prepared.append(str(path))
        with self.db() as db:
            for source in dict.fromkeys(prepared):
                if db.execute(
                    "SELECT 1 FROM queue WHERE source=? AND model=? AND recipe=? AND state IN ('queued','running')",
                    (source, model, recipe),
                ).fetchone():
                    continue
                db.execute(
                    "INSERT INTO queue VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex[:12],
                        source,
                        model,
                        recipe,
                        "queued",
                        "En espera.",
                        None,
                        0,
                        time.time(),
                        None,
                        None,
                        0,
                    ),
                )
        return self.state()

    def control(self, action, jid=None):
        with self.db() as db:
            if action in {"pause", "resume"}:
                db.execute(
                    "UPDATE settings SET value=? WHERE key='paused'",
                    ("1" if action == "pause" else "0",),
                )
            elif action == "cancel":
                db.execute(
                    "UPDATE queue SET cancel=1,state=CASE WHEN state='queued' THEN 'cancelled' ELSE state END,message=CASE WHEN state='running' THEN 'Se detendrá al terminar la operación actual.' ELSE message END WHERE id=? AND state IN ('queued','running')",
                    (jid,),
                )
            elif action == "retry":
                db.execute(
                    "UPDATE queue SET state='queued',cancel=0,finished=NULL,message='Preparado para reintentar.' WHERE id=? AND state IN ('error','interrupted','cancelled')",
                    (jid,),
                )
            else:
                raise ValueError("Acción desconocida.")
        return self.state()

    def launch(self, server):
        if self.thread and self.thread.is_alive():
            return
        self.recover()
        self.stop.clear()

        def loop():
            from . import runtime

            while not self.stop.wait(1):
                with server.lock:
                    if runtime.busy() or any(
                        j["status"] in {"queued", "running"} for j in server.jobs.values()
                    ):
                        continue
                    with self.db() as db:
                        if (
                            db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()[
                                0
                            ]
                            == "1"
                        ):
                            continue
                        row = db.execute(
                            "SELECT * FROM queue WHERE state='queued' ORDER BY created LIMIT 1"
                        ).fetchone()
                        if not row:
                            continue
                        item = dict(row)
                        db.execute(
                            "UPDATE queue SET state='running',started=?,attempt=attempt+1 WHERE id=?",
                            (time.time(), item["id"]),
                        )
                self.process(item, server)

        self.thread = threading.Thread(target=loop, daemon=True, name="figure-queue")
        self.thread.start()

    def process(self, item, server):
        jid = item["id"]
        out = self.root / jid
        out.mkdir(exist_ok=True)

        def progress(**values):
            with self.db() as db:
                row = db.execute("SELECT cancel FROM queue WHERE id=?", (jid,)).fetchone()
                if self.stop.is_set() or row[0]:
                    raise Cancelled("Procesamiento detenido; se conservan las fases terminadas.")
                if values.get("message"):
                    db.execute("UPDATE queue SET message=? WHERE id=?", (values["message"], jid))

        try:
            from .closed_loop import run, write, validate_sources
            from .assembly import assemble
            from scripts.import_evaluation import import_run

            source = Path(item["source"])
            stamp = {
                "size": source.stat().st_size,
                "modified": source.stat().st_mtime_ns,
                "model": item["model"],
                "recipe": item["recipe"],
                "pipeline": pipeline_signature(),
            }
            checkpoint = out / "source.json"
            if checkpoint.exists() and json.loads(checkpoint.read_text("utf-8")) != stamp:
                raise ValueError(
                    "El origen o el motor de preparación ha cambiado desde la primera ejecución. Añádelo como una entrada nueva para no reutilizar fases antiguas."
                )
            write(checkpoint, stamp)

            def phase(name, fn):
                marker = out / (name + "-complete.json")
                if marker.exists():
                    path = Path(json.loads(marker.read_text("utf-8"))["path"])
                    if (path / "result.json").exists():
                        validate_sources(path)
                        progress(message=f"Reutilizando la fase terminada: {name}.")
                        return path
                attempt = out / (name + "-attempt-" + str(item["attempt"] + 1))
                resume = {}
                if name in {"body", "clothing"}:
                    previous = sorted(
                        out.glob(name + "-attempt-*"),
                        key=lambda p: int(p.name.rsplit("-", 1)[-1]),
                        reverse=True,
                    )
                    for candidate in previous:
                        if candidate == attempt:
                            continue
                        plans = sorted(
                            (candidate / "iterations").glob("*/plans.json"),
                            key=lambda p: int(p.parent.name),
                            reverse=True,
                        )
                        if plans and (candidate / "scene/prepared.blend").exists():
                            inventory = json.loads(
                                (candidate / "scene/materials.json").read_text("utf-8")
                            )
                            if all(
                                (candidate / "surfaces" / r["material"] / "masks.npz").exists()
                                for r in inventory
                            ):
                                resume = {
                                    "reuse": candidate,
                                    "overrides": json.loads(plans[0].read_text("utf-8")),
                                }
                                break
                if resume:
                    progress(
                        message="Reanudando con las regiones y mapas calculados antes del fallo."
                    )
                fn(attempt, resume)
                write(marker, {"path": str(attempt.resolve())})
                return attempt

            recipe = item["recipe"]
            model = item["model"]
            if recipe == "clothing":
                final = phase(
                    "clothing",
                    lambda p, resume: run(
                        source, p, model, progress, corrections=1, target=["mTops"], **resume
                    ),
                )
            elif recipe == "complete":
                body = phase(
                    "body",
                    lambda p, resume: run(
                        source,
                        p,
                        model,
                        progress,
                        corrections=0,
                        defer_review=True,
                        separate=["mTops"],
                        **resume,
                    ),
                )
                garment = phase(
                    "clothing",
                    lambda p, resume: run(
                        source,
                        p,
                        model,
                        progress,
                        corrections=0,
                        defer_review=True,
                        target=["mTops"],
                        scene_contract=body / "observation/contract.json"
                        if (body / "observation/contract.json").exists()
                        else None,
                        **resume,
                    ),
                )
                final = phase(
                    "complete",
                    lambda p, resume: assemble(body, garment, p, progress, automatic=True),
                )
            else:
                final = phase(
                    "body",
                    lambda p, resume: run(
                        source, p, model, progress, corrections=1, separate=["mTops"], **resume
                    ),
                )
            progress(message="Guardando el proyecto y sus evidencias.")
            with server.lock:
                if not item["project"]:
                    project = server.create(server.NewProject(name=source.stem + " · lote"))
                    item["project"] = project["id"]
                    with self.db() as db:
                        db.execute("UPDATE queue SET project=? WHERE id=?", (project["id"], jid))
                project = import_run(
                    final,
                    source,
                    name=source.stem
                    + " · "
                    + {"complete": "figura completa", "body": "cuerpo", "clothing": "ropa"}[recipe],
                    project_id=item["project"],
                )
            with self.db() as db:
                db.execute(
                    "UPDATE queue SET state='done',finished=?,message='Mapas calculados. Pendiente de revisión visual.' WHERE id=?",
                    (time.time(), jid),
                )
        except Exception as e:
            state = (
                "interrupted"
                if self.stop.is_set()
                else "cancelled"
                if isinstance(e, Cancelled)
                else "error"
            )
            with self.db() as db:
                db.execute(
                    "UPDATE queue SET state=?,finished=?,message=? WHERE id=?",
                    (state, time.time(), str(e)[:600], jid),
                )

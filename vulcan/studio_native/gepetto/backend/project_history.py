"""Durable whole-project undo/redo, including masks and evaluation references."""

import copy, hashlib, json, shutil, sqlite3, time, uuid
from pathlib import Path

from .hoard_link.atomic import replace_with_retry, write_bytes_atomic, write_json_atomic


def connect(folder):
    root = Path(folder) / "revisions"
    root.mkdir(exist_ok=True)
    db = sqlite3.connect(root / "history.sqlite")
    db.row_factory = sqlite3.Row
    db.executescript("""PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS states(id TEXT PRIMARY KEY,doc TEXT,masks TEXT,fingerprint TEXT,created REAL);
    CREATE TABLE IF NOT EXISTS timeline(position INTEGER PRIMARY KEY,id TEXT);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS bookmarks(id TEXT PRIMARY KEY,name TEXT,created REAL);""")
    return db


def record(folder, project):
    with connect(folder) as db:
        masks = {}
        root = Path(folder) / "revisions"
        for asset in project["assets"]:
            path = Path(folder) / f"{asset['id']}.npz"
            if path.exists():
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                target = root / (digest + ".npz")
                if not target.exists():
                    write_bytes_atomic(target, raw)
                masks[asset["id"]] = digest
        # This committed document is the recovery source if publishing project.json
        # or restoring mask files is interrupted after/before a database commit.
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES('current_doc',?)",
            (json.dumps(project, ensure_ascii=False),),
        )
        db.execute("INSERT OR REPLACE INTO meta VALUES('current_masks',?)", (json.dumps(masks),))
        stable = copy.deepcopy(project)
        if stable.get("evaluation"):
            stable["evaluation"]["_current"] = (
                stable["evaluation"]["revision"] == stable["revision"]
            )
            stable["evaluation"].pop("revision", None)
        for key in ["revision", "history", "updated"]:
            stable.pop(key, None)
        for asset in stable["assets"]:
            asset.pop("version", None)
        fingerprint = hashlib.sha256(
            json.dumps([stable, masks], sort_keys=True).encode()
        ).hexdigest()
        current = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        cursor = int(current[0]) if current else -1
        previous = db.execute(
            "SELECT s.fingerprint FROM states s JOIN timeline t ON t.id=s.id WHERE t.position=?",
            (cursor,),
        ).fetchone()
        if previous and previous[0] == fingerprint:
            return
        sid = uuid.uuid4().hex
        db.execute(
            "INSERT INTO states VALUES(?,?,?,?,?)",
            (
                sid,
                json.dumps(project, ensure_ascii=False),
                json.dumps(masks),
                fingerprint,
                time.time(),
            ),
        )
        db.execute("DELETE FROM timeline WHERE position>?", (cursor,))
        db.execute("INSERT INTO timeline VALUES(?,?)", (cursor + 1, sid))
        db.execute("INSERT OR REPLACE INTO meta VALUES('cursor',?)", (str(cursor + 1),))


def status(folder):
    with connect(folder) as db:
        row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        cursor = int(row[0]) if row else -1
        end = db.execute("SELECT MAX(position) FROM timeline").fetchone()[0]
        return {
            "canUndo": cursor > 0,
            "canRedo": end is not None and cursor < end,
            "checkpoints": [
                dict(r) for r in db.execute("SELECT * FROM bookmarks ORDER BY created DESC")
            ],
        }


def bookmark(folder, name):
    with connect(folder) as db:
        row = db.execute(
            "SELECT t.id FROM timeline t JOIN meta m ON m.key='cursor' AND t.position=CAST(m.value AS INTEGER)"
        ).fetchone()
        if not row:
            raise ValueError("Guarda el proyecto antes de crear una versión.")
        db.execute("INSERT OR REPLACE INTO bookmarks VALUES(?,?,?)", (row[0], name, time.time()))
    return status(folder)


def restore(folder, current, direction=None, checkpoint=None):
    with connect(folder) as db:
        row = db.execute("SELECT value FROM meta WHERE key='cursor'").fetchone()
        cursor = int(row[0]) if row else -1
        if checkpoint:
            row = db.execute(
                "SELECT s.* FROM states s JOIN bookmarks b ON b.id=s.id WHERE s.id=?", (checkpoint,)
            ).fetchone()
        else:
            target = cursor + (-1 if direction == "undo" else 1)
            row = db.execute(
                "SELECT s.* FROM states s JOIN timeline t ON t.id=s.id WHERE t.position=?",
                (target,),
            ).fetchone()
        if not row:
            raise ValueError("No hay una versión disponible en esa dirección.")
        p = json.loads(row["doc"])
        versions = {a["id"]: a["version"] for a in current["assets"]}
        evaluated = p.get("evaluation") and p["evaluation"]["revision"] == p["revision"]
        p["revision"] = current["revision"] + 1
        p["updated"] = time.time()
        if evaluated:
            p["evaluation"]["revision"] = p["revision"]
        for asset in p["assets"]:
            asset["version"] = max(asset["version"], versions.get(asset["id"], 0)) + 1
        for aid, digest in json.loads(row["masks"]).items():
            dest = Path(folder) / (aid + ".npz")
            temp = dest.with_suffix(".restoring")
            shutil.copyfile(Path(folder) / "revisions" / (digest + ".npz"), temp)
            replace_with_retry(temp, dest)
        write_json_atomic(Path(folder) / "project.json", p)
        db.execute(
            "INSERT OR REPLACE INTO meta VALUES('current_doc',?)",
            (json.dumps(p, ensure_ascii=False),),
        )
        db.execute("INSERT OR REPLACE INTO meta VALUES('current_masks',?)", (row["masks"],))
        if not checkpoint:
            db.execute("UPDATE meta SET value=? WHERE key='cursor'", (str(target),))
    if checkpoint:
        record(folder, p)
    return p


def recover_published_files(folder):
    """Called at server startup, before accepting edits or launching the queue."""
    folder = Path(folder)
    if not (folder / "revisions/history.sqlite").exists():
        return False
    with connect(folder) as db:
        metadata = dict(
            db.execute(
                "SELECT key,value FROM meta WHERE key IN ('current_doc','current_masks')"
            ).fetchall()
        )
    if "current_doc" not in metadata:
        return False  # Legacy histories migrate on save.
    p = json.loads(metadata["current_doc"])
    repaired = False
    for aid, digest in json.loads(metadata["current_masks"]).items():
        destination = folder / (aid + ".npz")
        if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
            continue
        source = folder / "revisions" / (digest + ".npz")
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError(f"La copia de recuperación de {aid} está dañada.")
        temp = destination.with_suffix(".recovering")
        shutil.copyfile(source, temp)
        replace_with_retry(temp, destination)
        repaired = True
    destination = folder / "project.json"
    try:
        matches = json.loads(destination.read_text("utf-8")) == p
    except (OSError, ValueError):
        matches = False
    if not matches:
        write_json_atomic(destination, p)
        repaired = True
    return repaired

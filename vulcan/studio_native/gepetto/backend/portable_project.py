"""Import CPU-prepared editable project folders without inference or queue actions."""
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
import hashlib
import os
import stat
import uuid
from pathlib import PurePosixPath

import numpy as np
from PIL import Image
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


class PortablePath(BaseModel):
    path: str = Field(min_length=1, max_length=2000)


def inspect(directory):
    root = Path(directory)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or root.is_junction():
        raise ValueError("Selecciona una carpeta de proyecto local válida.")
    root = root.resolve()
    files = []
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError("El proyecto contiene enlaces a otras carpetas.")
        if path.is_file():
            total += path.stat().st_size
            files.append(path)
            if total > 2 * 1024**3 or len(files) > 10000:
                raise ValueError("El proyecto supera el límite de importación (2 GB o 10.000 archivos).")
    manifest = root / "project.json"
    if manifest.stat().st_size > 20 * 1024**2:
        raise ValueError("El documento del proyecto es demasiado grande.")
    p = json.loads(manifest.read_text("utf-8"))
    if not isinstance(p, dict):
        raise ValueError("El documento del proyecto debe ser un objeto.")
    if not re.fullmatch(r"[a-f0-9]{12}", p.get("id", "")):
        raise ValueError("El identificador del proyecto no es válido.")
    if not isinstance(p.get("name"), str) or not isinstance(p.get("history"), list):
        raise ValueError("Faltan el nombre o el historial del proyecto.")
    if not isinstance(p.get("revision"), int) or p["revision"] < 0:
        raise ValueError("La revisión del proyecto no es válida.")
    if p.get("evaluation") or p.get("blenderSource"):
        raise ValueError("Esta importación admite proyectos portables de preparación, sin evaluaciones Blender.")
    seen = set()
    required = {manifest}
    for collection in ("assets", "models"):
        if not isinstance(p.get(collection), list):
            raise ValueError("El proyecto no contiene un inventario válido.")
        for a in p[collection]:
            aid = a.get("id", "")
            if not re.fullmatch(r"[a-f0-9]{10}", aid) or aid in seen:
                raise ValueError("Identificador de textura/modelo inválido o repetido.")
            seen.add(aid)
            if not isinstance(a.get("name"), str):
                raise ValueError("Falta el nombre de un archivo.")
            filename = a.get("file", "")
            extensions = r"png|jpg|jpeg|webp|tga|bmp" if collection == "assets" else r"dae|glb"
            if not re.fullmatch(rf"{aid}\.({extensions})", filename):
                raise ValueError("Una ruta de origen no es portable.")
            source = root / "sources" / filename
            required.add(source)
            if collection == "models":
                continue
            preview = root / f"{aid}_preview.png"
            required.add(preview)
            if any(type(a.get(key)) is not int or a[key] < 0 for key in
                   ("width", "height", "workWidth", "workHeight", "version")):
                raise ValueError("Dimensiones o versión de textura inválidas.")
            if a["width"] * a["height"] > 85_000_000:
                raise ValueError("La textura supera 85 megapíxeles.")
            with Image.open(source) as im:
                if im.size != (a["width"], a["height"]):
                    raise ValueError("Las dimensiones de una textura no coinciden.")
                im.verify()
            with Image.open(preview) as im:
                im.verify()
            regions = a["regions"]
            ids = [r["id"] for r in regions]
            if len(set(ids)) != len(ids) or any(type(i) is not int or i < 0 for i in ids):
                raise ValueError("Regiones repetidas o inválidas.")
            if any(type(r["height"]) is not int or not 0 <= r["height"] <= 255 for r in regions):
                raise ValueError("Las alturas deben estar entre 0 y 255.")
            for region in regions:
                if region.get("displacementColor") is not None:
                    from .processing import displacement_color
                    displacement_color(region)
                if not isinstance(region.get("name"), str) or not isinstance(region.get("reason"), str):
                    raise ValueError("Falta la descripción de una región.")
                if region.get("geometry") not in {"painted", "modeled", "unknown"}:
                    raise ValueError("El tipo de geometría no es válido.")
                for key, length in (("color", 3), ("center", 2), ("bbox", 4)):
                    if not isinstance(region.get(key), list) or len(region[key]) != length:
                        raise ValueError("La descripción espacial de una región no es válida.")
            if regions:
                mask = root / f"{aid}.npz"
                required.add(mask)
                with zipfile.ZipFile(mask) as archive:
                    if any(info.file_size > 64 * 1024**2 for info in archive.infolist()):
                        raise ValueError("Una máscara excede el tamaño permitido.")
                with np.load(mask, allow_pickle=False) as data:
                    labels, centers = data["labels"], data["centers"]
                    if labels.shape != (a["workHeight"], a["workWidth"]) or labels.dtype.kind not in "iu":
                        raise ValueError("Las máscaras no coinciden con la textura.")
                    if set(np.unique(labels)) - {-1} != set(ids):
                        raise ValueError("Las máscaras no coinciden con las regiones.")
                    if centers.ndim != 2 or centers.shape[1] != 3 or not np.isfinite(centers).all():
                        raise ValueError("Los datos de reconstrucción no son válidos.")
                    if any(type(r.get("cluster")) is not int or not 0 <= r["cluster"] < len(centers)
                           for r in regions):
                        raise ValueError("Los grupos de color no coinciden con la reconstrucción.")
    if any(not path.is_file() for path in required):
        raise ValueError("Faltan archivos del proyecto portable.")
    return p


def export_file(source, destination):
    """Versioned .gepettos package; no Blender, queue, or external SQLite read."""
    p = inspect(str(source))
    target = Path(destination)
    if not target.is_absolute() or target.suffix.lower() != ".gepettos":
        raise ValueError("Usa una ruta absoluta nueva con extensión .gepettos.")
    target = target.resolve()
    if target.exists():
        raise FileExistsError("El archivo ya existe; elige otro nombre.")
    source = Path(source).resolve()
    if source == target or source in target.parents:
        raise ValueError("Guarda el archivo fuera de la carpeta interna del proyecto.")
    p["approved"] = False
    for asset in p["assets"]:
        asset["approved"] = False
    files = {}
    for a in p["assets"] + p["models"]:
        files["sources/" + a["file"]] = source / "sources" / a["file"]
    for a in p["assets"]:
        for name in [f"{a['id']}_preview.png", *([f"{a['id']}.npz"] if a["regions"] else [])]:
            files[name] = source / name
    documents = {"project.json": json.dumps(p, ensure_ascii=False, indent=2).encode("utf-8"),
                 "history.json": json.dumps(p["history"], ensure_ascii=False, indent=2).encode("utf-8")}
    index = {}
    for name, path in files.items():
        with path.open("rb") as stream:
            index[name] = {"bytes": path.stat().st_size, "sha256": hashlib.file_digest(stream, "sha256").hexdigest()}
    for name, raw in documents.items():
        index[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    manifest = {"format": "gepettos-project", "version": 1, "project_id": p["id"],
                "revision": p["revision"], "files": index,
                "history": "Decision history in history.json; undo database is not imported.",
                "review": "unapproved"}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / (".gepettos-" + uuid.uuid4().hex + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, raw in documents.items():
                archive.writestr(name, raw)
            for name, path in files.items():
                archive.write(path, name)
        # Windows rename is atomic and refuses an existing destination, including
        # on external filesystems without hard links. POSIX needs link for no-clobber.
        if os.name == "nt":
            temporary.rename(target)
        else:
            os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"path": str(target), "project_id": p["id"], "revision": p["revision"],
            "format": "gepettos-project", "version": 1, "approved": False}


def unpack_file(path, directory):
    """Validate archive metadata before extracting a single byte."""
    path, root = Path(path), Path(directory).resolve()
    if not path.is_absolute() or path.suffix.lower() != ".gepettos" or path.stat().st_size > 2 * 1024**3:
        raise ValueError("Archivo .gepettos inválido o demasiado grande.")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(infos) > 10000 or len(set(n.casefold() for n in names)) != len(names):
            raise ValueError("Inventario de archivo inválido o repetido.")
        total = 0
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            mode = info.external_attr >> 16
            if not re.fullmatch(r"manifest\.json|project\.json|history\.json|[a-f0-9]{10}(?:_preview\.png|\.npz)|sources/[a-f0-9]{10}\.(?:png|jpg|jpeg|webp|tga|bmp|dae|glb)", name):
                raise ValueError("Nombre o ruta no permitido en el paquete.")
            if (not parts or name.startswith("/") or "\\" in name or ":" in name
                or any(p in {".", ".."} or p.endswith((".", " ")) for p in parts)
                or info.is_dir() or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                or info.flag_bits & 1):
                raise ValueError("Ruta o tipo de archivo no permitido en el paquete.")
            if root not in (root / name).resolve().parents:
                raise ValueError("La ruta sale del paquete.")
            total += info.file_size
            if total > 2 * 1024**3 or info.file_size > 512 * 1024**2:
                raise ValueError("El paquete supera el límite descomprimido.")
        if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > 4 * 1024**2:
            raise ValueError("Falta el manifiesto de formato.")
        manifest = json.loads(archive.read("manifest.json"))
        if not isinstance(manifest, dict):
            raise ValueError("El manifiesto debe ser un objeto.")
        if manifest.get("format") != "gepettos-project" or type(manifest.get("version")) is not int or manifest["version"] != 1:
            raise ValueError("Versión .gepettos no compatible.")
        index = manifest.get("files")
        if not isinstance(index, dict) or set(index) != set(names) - {"manifest.json"}:
            raise ValueError("El inventario no coincide con el manifiesto.")
        for name, expected in index.items():
            if (not isinstance(expected, dict) or type(expected.get("bytes")) is not int
                or not isinstance(expected.get("sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected["sha256"])):
                raise ValueError("Metadatos de integridad inválidos.")
            if name.endswith((".sqlite", ".sqlite-wal", ".sqlite-shm")):
                raise ValueError("No se importan bases SQLite externas.")
            if expected.get("bytes") != archive.getinfo(name).file_size:
                raise ValueError("Tamaño de archivo inconsistente.")
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with archive.open(name) as src, target.open("xb") as out:
                while block := src.read(1024 * 1024):
                    digest.update(block)
                    out.write(block)
            if digest.hexdigest() != expected.get("sha256"):
                raise ValueError("La integridad del paquete no coincide.")
    p = inspect(str(root))
    if p["id"] != manifest.get("project_id") or p["revision"] != manifest.get("revision"):
        raise ValueError("La identidad del proyecto no coincide.")
    if json.loads((root / "history.json").read_text("utf-8")) != p["history"]:
        raise ValueError("El historial del proyecto no coincide.")
    return p


def import_directory(library, source):
    if Path(source).is_file():
        with tempfile.TemporaryDirectory(prefix="gepettos-", dir=library) as temporary:
            unpack_file(source, temporary)
            return import_directory(library, temporary)
    p = inspect(source)
    p["approved"] = False
    for asset in p["assets"]:
        asset["approved"] = False
    library = Path(library).resolve()
    destination = library / p["id"]
    if destination.exists():
        raise FileExistsError("Este proyecto ya está en la biblioteca. Ábrelo en Proyectos guardados.")
    # Copy into staging and validate again before making the project visible.
    with tempfile.TemporaryDirectory(prefix="portable-", dir=library) as temporary:
        staged = Path(temporary) / p["id"]
        staged.mkdir()
        (staged / "sources").mkdir()
        source = Path(source)
        (staged / "project.json").write_text(json.dumps(p, ensure_ascii=False, indent=2), "utf-8")
        for a in p["assets"] + p["models"]:
            shutil.copyfile(source / "sources" / a["file"], staged / "sources" / a["file"])
        for a in p["assets"]:
            for filename in [f"{a['id']}_preview.png", *([f"{a['id']}.npz"] if a["regions"] else [])]:
                shutil.copyfile(source / filename, staged / filename)
        inspect(str(staged))
        # Never import a supplied history database. The app lazily starts a new
        # undo timeline on its next history read/save; document provenance stays.
        if destination.exists():
            raise FileExistsError("Este proyecto ya está en la biblioteca.")
        staged.rename(destination)
    return p


def router_for(server):
    router = APIRouter()

    @router.post("/api/projects/import-portable")
    def import_portable(body: PortablePath):
        try:
            with server.lock:
                return import_directory(server.DATA, body.path)
        except FileExistsError as error:
            raise HTTPException(409, str(error)) from error
        except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
            raise HTTPException(400, f"No se pudo abrir el proyecto portable: {error}") from error

    @router.post("/api/projects/{project_id}/export-portable")
    def export_portable(project_id: str, body: PortablePath):
        if not re.fullmatch(r"[a-f0-9]{12}", project_id):
            raise HTTPException(400, "Identificador de proyecto inválido.")
        try:
            with server.lock:
                return export_file(Path(server.DATA) / project_id, body.path)
        except FileExistsError as error:
            raise HTTPException(409, str(error)) from error
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise HTTPException(400, f"No se pudo guardar .gepettos: {error}") from error

    return router

"""CPU-only editable-project tools. Never import the application or start its queue."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP, Image as MCPImage

from . import project_history
from .hoard_link.atomic import replace_with_retry, write_json_atomic
from .commands import EditPlan, Operation, apply_operations
from .processing import segment, segment_with_anchors, describe, render, png_bytes, displacement_color, write_manual_palette
from .vision import SemanticRegion
from .project_lock import ProjectLock


class SemanticEdit(SemanticRegion):
    model_config = ConfigDict(extra="forbid", strict=True)
    height: int = Field(ge=0, le=255)
    displacementColor: list[Annotated[int, Field(strict=True, ge=0, le=255)]] | None = Field(
        default=None, min_length=3, max_length=3
    )


class ExternalOperation(Operation):
    model_config = ConfigDict(extra="forbid", strict=True)


class ExternalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=0)
    author: str = Field(min_length=1, max_length=100)
    evidence: list[str] = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=750)
    regions: list[SemanticEdit] = Field(min_length=1)
    operations: list[ExternalOperation] = Field(default_factory=list, max_length=12)


class Store:
    def __init__(self, directory: str):
        if not directory or not Path(directory).is_absolute():
            raise ValueError("An explicit absolute isolated data directory is required.")
        self.root = Path(directory).resolve()
        default = Path(__file__).resolve().parents[1] / "data"
        if self.root == default or default in self.root.parents:
            raise ValueError("The application's default data directory is forbidden.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = ProjectLock(self.root)

    def folder(self, pid):
        if not re.fullmatch(r"[a-f0-9]{12}", pid):
            raise ValueError("Invalid project id.")
        return self.root / pid

    def read(self, pid):
        return json.loads((self.folder(pid) / "project.json").read_text("utf-8"))

    def save(self, p):
        p["updated"] = time.time()
        root = self.folder(p["id"])
        project_history.record(root, p)
        write_json_atomic(root / "project.json", p)
        return p

    def change(self, p, event):
        p["revision"] += 1
        p["approved"] = False
        if p.get("proposal"):
            p["proposal"]["status"] = "stale"
        p["history"].append({"time": time.time(), **event})
        return self.save(p)

    def create(self, name):
        if not name.strip() or len(name) > 100:
            raise ValueError("Name must contain 1–100 characters.")
        pid = uuid.uuid4().hex[:12]
        (self.folder(pid) / "sources").mkdir(parents=True)
        return self.save(dict(id=pid, name=name, assets=[], models=[], revision=0,
                              proposal=None, approved=False, history=[]))

    def import_file(self, pid, source):
        path = Path(source).resolve(strict=True)
        suffix = path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".tga", ".bmp", ".dae", ".glb"}:
            raise ValueError("Only images and existing DAE/GLB are accepted; no Blender imports.")
        if path.stat().st_size > 160 * 1024 * 1024:
            raise ValueError("Maximum input size is 160 MB.")
        p = self.read(pid)
        aid = uuid.uuid4().hex[:10]
        root = self.folder(pid)
        item = dict(id=aid, name=path.name, file=aid + suffix)
        if suffix in {".dae", ".glb"}:
            if suffix == ".dae" and any(x in path.read_bytes().upper() for x in (b"<!DOCTYPE", b"<!ENTITY")):
                raise ValueError("DAE entity declarations are forbidden.")
            p["models"].append(item)
        else:
            with Image.open(path) as im:
                if im.width * im.height > 85_000_000:
                    raise ValueError("Maximum texture size is 85 megapixels.")
                im.load()
                preview = im.convert("RGBA")
                preview.thumbnail((1536, 1536), Image.Resampling.LANCZOS)
                item.update(width=im.width, height=im.height, regions=[], workWidth=0,
                            workHeight=0, version=0, processingMs=None, approved=False)
                preview.save(root / f"{aid}_preview.png")
            p["assets"].append(item)
        shutil.copyfile(path, root / "sources" / item["file"])
        return self.change(p, {"type": "external-import", "source": str(path), "asset": aid})

    def prepare(self, pid, aid, clusters=6, resolution=768):
        if not 2 <= clusters <= 12 or not 256 <= resolution <= 1536:
            raise ValueError("clusters must be 2–12 and resolution 256–1536.")
        p = self.read(pid)
        a = next(x for x in p["assets"] if x["id"] == aid)
        if a["regions"]:
            raise ValueError("Refusing to replace existing editable masks; create a new project.")
        start = time.perf_counter()
        with Image.open(self.folder(pid) / "sources" / a["file"]) as im:
            labels, regions, work, centers = segment(im, clusters, resolution)
        np.savez_compressed(self.folder(pid) / f"{aid}.npz", labels=labels, centers=centers)
        a.update(regions=regions, workWidth=work.width, workHeight=work.height,
                 version=a["version"] + 1, processingMs=round(1000 * (time.perf_counter()-start)))
        return self.change(p, {"type": "external-segment", "asset": aid})

    def apply(self, pid, payload):
        plan = ExternalPlan.model_validate(payload)
        p = self.read(pid)
        if p["revision"] != plan.revision:
            raise ValueError("Revision conflict; read the project again.")
        lookup = {f"{a['id']}:{r['id']}": r for a in p["assets"] for r in a["regions"]}
        keys = [r.key for r in plan.regions]
        if len(set(keys)) != len(keys) or set(keys) != set(lookup):
            raise ValueError("Provide each current region exactly once.")
        # Validation completes on an in-memory document before any file mutation.
        for r in plan.regions:
            lookup[r.key].update(r.model_dump(exclude={"key"}))
        operations = EditPlan.model_validate({"explanation": plan.explanation,
                                              "operations": [op.model_dump() for op in plan.operations]})
        for op in operations.operations:
            if any(key.startswith("group:") for key in op.targets):
                raise ValueError("Use explicit region keys, not group selectors.")
        result = apply_operations(p, operations)
        groups = {}
        for a in result["assets"]:
            for r in a["regions"]:
                group = r.get("heightGroup")
                if group and groups.setdefault(group, r["height"]) != r["height"]:
                    raise ValueError("Linked height groups must have equal heights.")
            a["version"] += 1
            a["approved"] = False
        return self.change(result, {"type": "external-plan", **plan.model_dump()})

    def prepare_anchors(self, pid, aid, colors, revision, resolution=1536):
        p = self.read(pid)
        if type(revision) is not int or p["revision"] != revision:
            raise ValueError("Revision conflict; read the project again.")
        if any(e.get("type") == "external-plan" for e in p["history"]):
            raise ValueError("Cannot replace masks after an external semantic plan.")
        a = next(x for x in p["assets"] if x["id"] == aid)
        with Image.open(self.folder(pid) / "sources" / a["file"]) as im:
            labels, regions, work, centers = segment_with_anchors(im, colors, resolution)
        mask_path = self.folder(pid) / f"{aid}.npz"
        temporary = mask_path.with_name(aid + ".tmp.npz")
        np.savez_compressed(temporary, labels=labels, centers=centers)
        replace_with_retry(temporary, mask_path)
        a.update(regions=regions, workWidth=work.width, workHeight=work.height,
                 version=a["version"] + 1, approved=False,
                 segmentation={"method": "explicit-rgb-anchors", "colors": colors,
                               "resolution": resolution, "smallComponents": "nearest-same-anchor",
                               "colorPixelsDiscarded": False})
        return self.change(p, {"type": "external-anchor-segment", "asset": aid,
                               "colors": colors, "resolution": resolution})

    def evidence(self, pid, aid, mode):
        p = self.read(pid)
        a = next(x for x in p["assets"] if x["id"] == aid)
        if mode == "original":
            return (self.folder(pid) / f"{aid}_preview.png").read_bytes()
        if mode not in {"ids", "color", "height", "manual-palette"}:
            raise ValueError("Unknown image mode.")
        with np.load(self.folder(pid) / f"{aid}.npz", allow_pickle=False) as masks:
            return png_bytes(render(masks["labels"], a["regions"], mode))

    def split_polygon(self, pid, aid, region_id, points, revision):
        p = self.read(pid)
        if type(revision) is not int or p["revision"] != revision:
            raise ValueError("Revision conflict; read the project again.")
        if not 3 <= len(points) <= 256 or any(
            not isinstance(point, list) or len(point) != 2 or any(
                type(v) not in (int, float) or not np.isfinite(v) or not 0 <= v <= 1
                for v in point) for point in points
        ):
            raise ValueError("Provide 3–256 normalized [x,y] points within 0..1.")
        a = next(x for x in p["assets"] if x["id"] == aid)
        region = next((r for r in a["regions"] if r["id"] == region_id), None)
        if region is None or type(region_id) is not int:
            raise ValueError("Unknown zero-based region id.")
        mask_path = self.folder(pid) / f"{aid}.npz"
        with np.load(mask_path, allow_pickle=False) as masks:
            labels, centers = masks["labels"].copy(), masks["centers"].copy()
        h, w = labels.shape
        polygon = Image.new("L", (w, h))
        ImageDraw.Draw(polygon).polygon([(round(x * (w - 1)), round(y * (h - 1))) for x, y in points], fill=255)
        selected = (labels == region_id) & (np.asarray(polygon) > 0)
        if not selected.any() or selected.sum() == np.count_nonzero(labels == region_id):
            raise ValueError("Polygon must select a nonempty proper subset of the region.")
        new_id = max(r["id"] for r in a["regions"]) + 1
        if new_id > 32766:
            raise ValueError("Region capacity exceeded.")
        labels[selected] = new_id
        inventory = [{"id": region_id, "cluster": region["cluster"]},
                     {"id": new_id, "cluster": region["cluster"]}]
        with Image.open(self.folder(pid) / "sources" / a["file"]) as im:
            work = im.convert("RGBA").resize((w, h), Image.Resampling.LANCZOS)
            affected = describe(labels, np.asarray(work), inventory)
            # A split cannot change other masks or their measured metadata.
            regions = sorted([r.copy() for r in a['regions'] if r['id'] != region_id] + affected,
                             key=lambda r: r['id'])
        previous = {r['id']: r for r in a['regions']}
        semantic_fields = ('name', 'height', 'displacementColor', 'reason', 'confidence', 'geometry', 'heightGroup', 'semanticGroup')
        for r in regions:
            original = previous.get(r['id'], region)
            for key in semantic_fields:
                if key in original and not (r['id'] == new_id and key in ('heightGroup', 'semanticGroup')):
                    r[key] = original[key]
        temporary = mask_path.with_name(aid + ".tmp.npz")
        np.savez_compressed(temporary, labels=labels, centers=centers)
        replace_with_retry(temporary, mask_path)
        a.update(regions=regions, version=a["version"] + 1, approved=False)
        return self.change(p, {"type": "external-polygon-split", "asset": aid,
                               "region": region_id, "new_region": new_id, "points": points})

    def export(self, pid, destination):
        target = Path(destination)
        if not target.is_absolute():
            raise ValueError("An absolute new destination directory is required.")
        target = target.resolve()
        source = self.folder(pid).resolve()
        if target.exists() or source == target or source in target.parents:
            raise ValueError("Destination must be new and outside the source project.")
        self.read(pid)
        shutil.copytree(source, target)
        self.inspect_directory(str(target))
        return {"directory": str(target), "project": str(target / "project.json")}

    def export_manual_palette(self, pid, destination):
        target = Path(destination)
        if not target.is_absolute():
            raise ValueError("An absolute new destination directory is required.")
        target = target.resolve()
        source = self.folder(pid).resolve()
        if target.exists() or target == source or source in target.parents:
            raise ValueError("Destination must be new and outside the source project.")
        p = self.inspect_directory(str(source))
        if not p["assets"]:
            raise ValueError("No textures to export.")
        for a in p["assets"]:
            if not a["regions"]:
                raise ValueError("Every texture needs segmented regions and explicit colors.")
            for region in a["regions"]:
                displacement_color(region)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / (".manual-palette-" + uuid.uuid4().hex)
        staging.mkdir()
        try:
            textures = []
            for a in p["assets"]:
                # Validated hex IDs prevent traversal, reserved names and collisions.
                filename = a["id"] + "_manual.png"
                with Image.open(source / "sources" / a["file"]) as im:
                    with np.load(source / f"{a['id']}.npz", allow_pickle=False) as masks:
                        write_manual_palette(im, masks["labels"], a["regions"], masks["centers"], staging / filename)
                    textures.append({"id": a["id"], "name": a["name"], "file": filename,
                                     "size": list(im.size), "regions": a["regions"]})
            manifest = {"profile": "manual-rgb-preserve-scene", "project_id": pid,
                        "revision": p["revision"], "textures": textures,
                        "notes": "Production maps use exact manually assigned RGB and original alpha. Preserve the scene image colorspace and Figure Tools nodes. Heights are internal ordinal values, not calibrated displacement. No Blender evaluation performed."}
            (staging / "manual-palette.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")
            # rename is non-overwriting on Windows; check again for concurrent callers.
            if target.exists():
                raise ValueError("Destination already exists.")
            staging.rename(target)
        except BaseException:
            if staging.is_dir():
                shutil.rmtree(staging)
            raise
        return {"directory": str(target), "manifest": str(target / "manual-palette.json"),
                "profile": manifest["profile"], "textures": len(textures)}

    @staticmethod
    def inspect_directory(directory):
        root = Path(directory).resolve(strict=True)
        p = json.loads((root / "project.json").read_text("utf-8"))
        for a in p["assets"] + p["models"]:
            path = (root / "sources" / a["file"]).resolve()
            if root not in path.parents or not path.is_file():
                raise ValueError("Missing or nonportable project source.")
        for a in p["assets"]:
            if not re.fullmatch(r"[a-f0-9]{10}", a["id"]):
                raise ValueError("Invalid asset id.")
            if not (root / f"{a['id']}_preview.png").is_file():
                raise ValueError("Missing preview.")
            if a["regions"]:
                with np.load(root / f"{a['id']}.npz", allow_pickle=False) as masks:
                    if set(np.unique(masks["labels"])) - {-1} != {r["id"] for r in a["regions"]}:
                        raise ValueError("Mask and region inventory mismatch.")
                    if "centers" not in masks:
                        raise ValueError("Missing native reconstruction centers.")
        return p


def make_server(directory):
    store = Store(directory)
    mcp = FastMCP("Sculptor's Hoard external CPU preparation")

    @mcp.tool()
    def create_project(name: str) -> dict:
        """Create an editable project in the explicitly isolated library."""
        with store.lock:
            return store.create(name)

    @mcp.tool()
    def import_source(project_id: str, path: str) -> dict:
        """Copy a native image or existing DAE/GLB; never opens Blender."""
        with store.lock:
            return store.import_file(project_id, path)

    @mcp.tool()
    def segment_texture(project_id: str, asset_id: str, clusters: int = 6, resolution: int = 768) -> dict:
        """CPU candidate masks only. Semantic decisions belong to the calling model."""
        with store.lock:
            return store.prepare(project_id, asset_id, clusters, resolution)

    @mcp.tool()
    def read_project(project_id: str) -> dict:
        """Read current revision, region inventory, semantic heights and provenance."""
        with store.lock:
            return store.read(project_id)

    @mcp.tool()
    def segment_texture_with_anchors(project_id: str, asset_id: str,
                                     colors: list[list[Annotated[int, Field(strict=True, ge=0, le=255)]]],
                                     revision: int, resolution: int = 1536) -> dict:
        """CPU masks from 1–32 caller RGB prototypes. Tiny islands join only the same color. Replaces masks only before any semantic plan and at exact revision. Region IDs are zero-based."""
        with store.lock:
            return store.prepare_anchors(project_id, asset_id, colors, revision, resolution)

    @mcp.tool()
    def split_region_polygon(project_id: str, asset_id: str, region_id: int,
                             points: list[list[float]], revision: int) -> dict:
        """Split intersection of a region and caller polygon into a new zero-based ID, preserving its color cluster and semantic settings. Points are normalized [x,y]; the exact current revision is required. Native boundary follows nearest work-mask component."""
        with store.lock:
            return store.split_polygon(project_id, asset_id, region_id, points, revision)

    @mcp.tool()
    def image_evidence(project_id: str, asset_id: str,
                       mode: Literal["original", "ids", "color", "height", "manual-palette"] = "original") -> MCPImage:
        """Return an image to the calling model for visual interpretation."""
        with store.lock:
            return MCPImage(data=store.evidence(project_id, asset_id, mode), format="png")

    @mcp.tool()
    def apply_semantic_plan(project_id: str, plan: ExternalPlan) -> dict:
        """Validate and save the caller's complete semantic plan with revision and evidence."""
        with store.lock:
            return store.apply(project_id, plan.model_dump())

    @mcp.tool()
    def export_editable_project(project_id: str, destination: str) -> dict:
        """Copy the full editable folder to a NEW explicit destination; never overwrite."""
        with store.lock:
            return store.export(project_id, destination)

    @mcp.tool()
    def export_manual_palette_maps(project_id: str, destination: str) -> dict:
        """Export exact artist RGB production PNGs and source alpha to a NEW directory. Preserve scene colorspace; never runs Blender."""
        with store.lock:
            return store.export_manual_palette(project_id, destination)

    @mcp.tool()
    def export_project_file(project_id: str, destination: str) -> dict:
        """Save an unapproved editable .gepettos file atomically to a NEW absolute path; no Blender or models."""
        from .portable_project import export_file
        with store.lock:
            return export_file(store.folder(project_id), destination)

    @mcp.tool()
    def inspect_editable_directory(directory: str) -> dict:
        """Read back an exported project and verify its sources and region masks."""
        return Store.inspect_directory(directory)

    return mcp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args()
    make_server(args.data_dir).run(transport="stdio")


if __name__ == "__main__":
    main()

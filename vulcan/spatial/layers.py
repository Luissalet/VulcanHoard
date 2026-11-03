"""Scenario layers: GeoJSON + declared CRS + provenance, validated and reprojected to the metric work CRS."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any

from . import crs as C
from .errors import SpatialError

ROLES = ("demand", "candidates", "network", "obstacles", "parcels", "exclusions", "slope", "elevation")
MAX_FEATURES = 50_000


@dataclass
class Layer:
    role: str
    crs_id: str
    source: str | None
    date: str | None
    resolution: str | None
    features: list = field(default_factory=list)  # [(id, shapely geom in the *declared* CRS, props)]
    grid: Any = None
    warnings: list = field(default_factory=list)
    age_days: int | None = None
    scale_note: str = ""

    def meta(self) -> dict:
        return {"role": self.role, "crs": self.crs_id, "source": self.source, "date": self.date, "resolution": self.resolution,
                "age_days": self.age_days, "features": len(self.features) if self.grid is None else None,
                "grid_shape": list(self.grid.shape) if self.grid is not None else None, "warnings": list(self.warnings)}


def _crs_of_geojson(spec: dict):
    value = spec.get("crs")
    if isinstance(value, dict):  # legacy GeoJSON named CRS
        value = (value.get("properties") or {}).get("name")
    return value


def _unwrap(role: str, spec: dict) -> tuple[dict, dict]:
    """Return (meta, FeatureCollection). Accepts the wrapper form or a bare FeatureCollection with crs/source/date members."""
    if not isinstance(spec, dict):
        raise SpatialError("layer_invalid", f"Layer '{role}' must be an object.")
    body = spec.get("geojson", spec)
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError as error:
            raise SpatialError("layer_invalid", f"Layer '{role}': geojson is not valid JSON.") from error
    meta = {"crs": spec.get("crs", _crs_of_geojson(body) if isinstance(body, dict) else None),
            "source": spec.get("source", body.get("source") if isinstance(body, dict) else None),
            "date": spec.get("date", body.get("date") if isinstance(body, dict) else None),
            "resolution": spec.get("resolution", body.get("resolution") if isinstance(body, dict) else None)}
    if isinstance(meta["crs"], dict):
        meta["crs"] = (meta["crs"].get("properties") or {}).get("name")
    if not isinstance(body, dict) or body.get("type") not in ("FeatureCollection", "Feature", "GeometryCollection") and "coordinates" not in body:
        raise SpatialError("layer_invalid", f"Layer '{role}' must be GeoJSON (FeatureCollection).")
    if body.get("type") == "Feature":
        body = {"type": "FeatureCollection", "features": [body]}
    elif body.get("type") != "FeatureCollection":
        body = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": body, "properties": {}}]}
    return meta, body


def parse_layer(role: str, spec: dict, *, as_of: _date, max_age_days: int | None, require_provenance: bool) -> Layer:
    from shapely.geometry import shape
    from shapely.validation import make_valid

    if role == "elevation":
        from .grid import load_grid

        meta = {k: spec.get(k) for k in ("crs", "source", "date", "resolution")}
        crs = C.parse_crs(meta["crs"])
        layer = Layer(role, C.crs_id(crs), meta["source"], meta["date"], meta["resolution"])
        layer.grid = load_grid(spec)
        if not layer.resolution:
            from .grid import cell_size_m
            mx, my, note = cell_size_m(layer.grid)
            layer.resolution = f"{mx:g} x {my:g} m"
            if note:
                layer.warnings.append(note)
    else:
        meta, body = _unwrap(role, spec)
        crs = C.parse_crs(meta["crs"])  # raises crs_missing / crs_unknown
        layer = Layer(role, C.crs_id(crs), meta["source"], meta["date"], meta["resolution"])
        raw = body.get("features") or []
        if len(raw) > MAX_FEATURES:
            raise SpatialError("too_large", f"Layer '{role}' has {len(raw)} features (limit {MAX_FEATURES}).")
        pts = []
        for i, f in enumerate(raw):
            geom = f.get("geometry") if isinstance(f, dict) else None
            if geom is None:
                layer.warnings.append(f"feature {i} has no geometry and was skipped")
                continue
            try:
                g = shape(geom)
            except Exception as error:  # shapely raises several types for malformed input
                raise SpatialError("layer_invalid", f"Layer '{role}' feature {i}: bad geometry ({error}).") from error
            if g.is_empty:
                layer.warnings.append(f"feature {i} is empty and was skipped")
                continue
            if not g.is_valid:
                g = make_valid(g)
                layer.warnings.append(f"feature {i} had an invalid geometry and was repaired with make_valid")
            props = dict(f.get("properties") or {})
            fid = props.get("id", f.get("id", f"{role}-{i}"))
            layer.features.append((str(fid), g, props))
            b = g.bounds
            pts += [(b[0], b[1]), (b[2], b[3])]
        C.check_in_area_of_use(pts, crs, f"Layer '{role}'")
        if not layer.features:
            raise SpatialError("layer_invalid", f"Layer '{role}' has no usable features.")
    for key in ("source", "date", "resolution"):
        if not getattr(layer, key):
            layer.warnings.append(f"no {key} recorded for this layer")
            if require_provenance:
                raise SpatialError("invalid", f"Layer '{role}' has no {key}; require_provenance is on.", hint="Record source, date and resolution of every layer.")
    if layer.date:
        try:
            d = _date.fromisoformat(str(layer.date)[:10])
            layer.age_days = (as_of - d).days
            if layer.age_days < 0:
                layer.warnings.append(f"data date {layer.date} is after the analysis date {as_of}")
            elif max_age_days is not None and layer.age_days > max_age_days:
                layer.warnings.append(f"data are {layer.age_days} days old (limit {max_age_days}): they may be out of date")
        except ValueError:
            layer.warnings.append(f"date {layer.date!r} is not ISO (YYYY-MM-DD) and could not be checked")
    return layer


def lonlat_bbox(layers: list[Layer]):
    xs, ys = [], []
    for layer in layers:
        if layer.grid is not None:
            b = layer.grid.bounds()
            geoms = [(b[0], b[1]), (b[2], b[3])]
        else:
            geoms = []
            for _, g, _ in layer.features:
                b = g.bounds
                geoms += [(b[0], b[1]), (b[2], b[3])]
        for x, y in geoms:
            lon, lat = C.transform_xy(x, y, layer.crs_id, "EPSG:4326")
            xs.append(lon)
            ys.append(lat)
    return min(xs), min(ys), max(xs), max(ys)


def to_work(layer: Layer, work_crs_id: str) -> list:
    """Features reprojected to the work CRS: [(id, geom, props)]."""
    return [(fid, C.transform_geom(g, layer.crs_id, work_crs_id), props) for fid, g, props in layer.features]

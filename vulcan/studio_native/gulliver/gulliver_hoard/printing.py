"""Printer/material snapshots and geometry-based budgets, independent of map downloads and slicing."""

import threading
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .errors import GulliverError
from .hoard_link.atomic import read_json, write_json_atomic


class Filament(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field("PLA", max_length=100)
    density_g_cm3: float = Field(1.24, gt=0, le=20)
    price_per_kg: Optional[float] = Field(None, ge=0)
    color: str = Field("", pattern=r"^(#[0-9a-fA-F]{6})?$")


class PrintProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field("", max_length=40)
    name: str = Field("Mi impresora", min_length=1, max_length=100)
    bed_x_mm: float = Field(256, ge=20, le=2000)
    bed_y_mm: float = Field(256, ge=20, le=2000)
    nozzle_mm: float = Field(.4, ge=.1, le=1.2)
    filament: Filament = Field(default_factory=Filament)
    filaments: dict[str, Filament] = Field(default_factory=dict, description="Optional material and colour per part.")
    infill_pct: float = Field(15, ge=0, le=100)
    shell_mm: float = Field(.9, ge=.1, le=10)
    currency: str = Field("EUR", pattern=r"^[A-Za-z]{3}$")
    archived: bool = False


class Profiles:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def list(self, archived=False):
        with self._lock:
            profiles = read_json(self.path, default={})
            return {"profiles": [p for p in profiles.values() if bool(p.get("archived")) == archived]}

    def get(self, id_):
        with self._lock:
            p = read_json(self.path, default={}).get(id_)
            if p is None:
                raise GulliverError("not_found", "Print profile not found.")
            return PrintProfile(**p)

    def save(self, profile: PrintProfile):
        with self._lock:
            data = read_json(self.path, default={})
            p = profile.model_copy(deep=True)
            p.id = p.id or uuid4().hex[:16]
            data[p.id] = p.model_dump()
            write_json_atomic(self.path, data)
            return p.model_dump()

    def archive(self, id_, archived=True):
        with self._lock:
            p = self.get(id_)
            p.archived = archived
            return self.save(p)


def bed(params, default):
    p = params.print_profile
    return (p.bed_x_mm, p.bed_y_mm) if p else default


def estimate(report, profile: Optional[PrintProfile] = None):
    """Volume-weighted material allocation, with an explicit shell/infill approximation; no slicer claim."""
    if "surface_mm2" not in report:
        raise GulliverError("unavailable", "This older build has no surface measurement. Rebuild it to estimate materials.")
    p = profile or PrintProfile()
    volume = float(report["volume_cm3"]) * 1000
    shell = min(volume, float(report["surface_mm2"]) * p.shell_mm)
    material_cm3 = (shell + (volume - shell) * p.infill_pct / 100) / 1000
    parts = report["parts"]
    part_volume = sum(max(0, item["volume_cm3"]) for item in parts.values())
    rows = []
    grams, cost = 0., 0.
    unpriced = []
    for name, item in parts.items():
        filament = p.filaments.get(name, p.filament)
        share = max(0, item["volume_cm3"]) / part_volume if part_volume else 0
        mass = material_cm3 * share * filament.density_g_cm3
        price = None if filament.price_per_kg is None else mass * filament.price_per_kg / 1000
        grams += mass
        if price is None and mass > 0:
            unpriced.append(name)
        cost += price or 0
        rows.append({"part": name, "geometric_cm3": item["volume_cm3"], "estimated_g": round(mass, 3),
                     "material": filament.name, "color": filament.color or item.get("color", ""), "price_per_kg": filament.price_per_kg,
                     "estimated_cost": None if price is None else round(price, 4)})
    return {"basis": "geometry_shell_infill_volume_share", "sliced": False, "profile": p.model_dump(), "parts": rows,
            "estimated_g": round(grams, 2), "estimated_cost": None if unpriced else round(cost, 2),
            "priced_parts_cost": round(cost, 2), "unpriced_parts": unpriced, "currency": p.currency.upper(),
            "includes_purge_supports_energy_time": False}

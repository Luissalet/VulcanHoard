"""Bare-earth laser terrain. Reuses the WCS readers, never the surface model's highest-pixel resampling.

Only actual terrain layers belong here: a roof or canopy must not become a second building on the ground.
National IGN MDT and worldwide Terrarium remain the fallback in dem.py.
"""

from dataclasses import replace
from io import BytesIO
import math

import numpy as np

from ..errors import GulliverError
from . import dsm
from .dem import DemGrid


PROVIDERS = [
    replace(dsm.PROVIDERS["es_nav"], key="dtm-nav", name="IDENA MDT LiDAR 2 m", layer="IDENA.WCS__ELEVAC_Ras_MDT_2M",
            ndsm_layer="", attribution="Terreno LiDAR © Gobierno de Navarra / IDENA (CC BY 4.0)"),
    replace(dsm.PROVIDERS["es_gip"], key="dtm-gip", name="Gipuzkoa MDT LiDAR 2017, 1 m",
            layer="LIDAR 2017: Lur eredua-Modelo de suelo-Digital Terrain Model", ndsm_layer="",
            attribution="Terreno LiDAR 2017 © Diputación Foral de Gipuzkoa (datos abiertos)"),
    replace(dsm.PROVIDERS["nl"], key="dtm-nl", name="AHN DTM 0.5 m", layer="dtm_05m", ndsm_layer="",
            attribution="Terreno AHN © Rijkswaterstaat / PDOK (CC0)"),
    replace(dsm.PROVIDERS["fr"], key="dtm-fr", name="IGN LiDAR HD MNT 0.5 m",
            layer="IGNF_LIDAR-HD_MNT_ELEVATION.ELEVATIONGRIDCOVERAGE.LAMB93", ndsm_layer="",
            attribution="Terreno LiDAR HD © IGN France (Licence Ouverte / Etalab)"),
]


def candidates(bbox, want_res_m):
    lon0, lat0, lon1, lat1 = bbox
    area = (lon1 - lon0) * dsm.M_PER_DEG * math.cos(math.radians((lat0 + lat1) / 2)) * (lat1 - lat0) * dsm.M_PER_DEG
    if area > 25e6 or want_res_m > 30:
        return []
    points = [((lon0 + lon1) / 2, (lat0 + lat1) / 2), (lon0, lat0), (lon1, lat1), (lon0, lat1), (lon1, lat0)]
    return [p for p in PROVIDERS if not (p.key in ("dtm-nav", "dtm-gip") and want_res_m >= 5)
            and any(p.covers(*pt) for pt in points)]


class _GroundGrid(dsm._Grid):
    def add(self, source, src_transform, src_crs, src_bounds, *, src_nodata=float("nan")):
        from rasterio.warp import reproject, transform_bounds
        from rasterio.enums import Resampling

        win = self.window(transform_bounds(src_crs, "EPSG:4326", *src_bounds, densify_pts=21))
        if win is None:
            return
        r0, r1, c0, c1 = win
        sub = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
        reproject(source=source, destination=sub, src_transform=src_transform, src_crs=src_crs,
                  dst_transform=self.transform @ type(self.transform).translation(c0, r0), dst_crs="EPSG:4326",
                  resampling=Resampling.bilinear, src_nodata=src_nodata, dst_nodata=float("nan"))
        cur = self.data[r0:r1, c0:c1]
        good = np.isfinite(sub)
        cur[good] = sub[good]


def _fill_enclosed_gaps(data, res_m):
    """Only small enclosed survey gaps: borrow bare ground, never radar rooftops or land across an open coast."""
    from scipy import ndimage
    bad = ~np.isfinite(data)
    if not bad.any() or bad.all():
        return data, 0
    labels, _ = ndimage.label(bad)
    border = np.unique(np.r_[labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    distance, nearest = ndimage.distance_transform_edt(bad, return_indices=True)
    fill = bad & ~np.isin(labels, border) & (distance * res_m <= 30)
    out = data.copy()
    out[fill] = data[tuple(axis[fill] for axis in nearest)]
    return out, int(fill.sum())


def fill_land_gaps(grid, fallback):
    """A gap connected to the crop edge can also be a row of removed roofs. Use nearby ground on land only.

    The coarse reference is used as a land/sea hint, not as a roof-height surface. Cells at or below the sea threshold
    remain missing: filling them from a cliff would move the coast inland or turn the sea into land.
    """
    from scipy import ndimage
    bad = ~np.isfinite(grid.data)
    if not bad.any() or bad.all():
        return
    measured = getattr(grid, "measured", np.isfinite(grid.data))
    distance, nearest = ndimage.distance_transform_edt(~measured, return_indices=True)
    rows, cols = np.nonzero(bad & (distance * grid.res_m <= 60))
    if not len(rows):
        return
    lon = grid.origin[0] + cols * grid.step[0]
    lat = grid.origin[1] - rows * grid.step[1]
    land = fallback.sample(lon, lat) > 0.3
    rows, cols = rows[land], cols[land]
    grid.data[rows, cols] = grid.data[nearest[0, rows, cols], nearest[1, rows, cols]]
    if hasattr(grid, "quality"):
        grid.quality["interpolated_fraction"] = round(float((~measured & np.isfinite(grid.data)).mean()), 4)
        grid.quality["fallback_fraction"] = round(float((~np.isfinite(grid.data)).mean()), 4)
    grid.notes.append(f"{len(rows)} open survey-gap cells on land borrowed measured bare ground (up to 60 m); "
                      "reference cells at or below sea level were kept for the fallback.")


def fetch(fetcher, bbox, want_res_m, provider, progress=None):
    res = max(provider.native_m, float(want_res_m))
    key = f"dtm-bilinear-v3|{provider.key}|{bbox}|{res:.5f}"
    cached = fetcher.cached("dtm_grid", key, ".npz")
    if cached is not None:
        with np.load(BytesIO(cached)) as z:
            data, bounds, res, filled, measured = z["h"], z["bounds"], float(z["res"]), int(z["filled"]), z["measured"]
    else:
        # Read around the crop too, so a building clipped by the model edge is not mistaken for an open survey boundary.
        lon0, lat0, lon1, lat1 = bbox
        pad_lat = 60 / dsm.M_PER_DEG
        pad_lon = pad_lat / max(.05, math.cos(math.radians((lat0 + lat1) / 2)))
        padded = (lon0 - pad_lon, lat0 - pad_lat, lon1 + pad_lon, lat1 + pad_lat)
        grid = _GroundGrid(padded, res)
        # The same bound as the model mesh; coarsen the source read instead of discarding the place.
        if grid.w * grid.h > 3_000_000:
            res *= math.sqrt(grid.w * grid.h / 3_000_000) * 1.01
            grid = _GroundGrid(padded, res)
        log = (lambda message: progress(message.replace("Modelo de superficies", "Modelo del terreno"))) if progress else None
        try:
            dsm._fill_service(fetcher, provider, False, padded, res, grid, log)
        except GulliverError:
            raise
        except Exception as exc:  # a malformed remote raster must not lose the ordinary terrain fallback
            raise GulliverError("source_failed", f"{provider.name} could not be read: {exc}") from exc
        if grid.valid_fraction() < 0.02:
            raise GulliverError("source_failed", f"{provider.name} has no terrain data here.")
        measured = np.isfinite(grid.data)
        data, filled = _fill_enclosed_gaps(grid.data, res)
        bounds = grid.bounds
        buf = BytesIO()
        np.savez_compressed(buf, h=data, bounds=np.array(bounds), res=res, filled=filled, measured=measured)
        fetcher.store("dtm_grid", key, buf.getvalue(), ".npz")
    left, bottom, right, top = bounds
    rows, cols = data.shape
    step = ((right - left) / cols, (top - bottom) / rows)
    result = DemGrid(data, "lonlat", (left + step[0] / 2, top - step[1] / 2), step,
                   source=provider.key, res_m=res, attribution=provider.attribution,
                   notes=[f"{provider.name}: bare-earth terrain sampled at {res:g} m; {filled} enclosed missing cells "
                          "interpolated from nearby bare ground (up to 30 m); remaining gaps use the fallback."])
    result.measured = measured
    result.quality = {"measured_fraction": round(float(measured.mean()), 4),
                      "interpolated_fraction": round(float((~measured & np.isfinite(data)).mean()), 4),
                      "fallback_fraction": round(float((~np.isfinite(data)).mean()), 4)}
    return result

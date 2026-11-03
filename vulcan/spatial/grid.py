"""Elevation raster: an inline grid or an ESRI ASCII grid (always available); GeoTIFF when rasterio is installed.

Rows run north to south. Slope is computed on the grid in ground metres (a geographic grid gets a stated approximation)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .crs import crs_id, parse_crs
from .errors import SpatialError

MAX_CELLS = 4_000_000


@dataclass
class Grid:
    values: object  # numpy array (rows, cols), nan = no data
    x_west: float
    y_north: float
    dx: float  # cell size in CRS units
    dy: float
    crs: object
    note: str = ""

    @property
    def shape(self):
        return self.values.shape

    def bounds(self):
        rows, cols = self.shape
        return (self.x_west, self.y_north - rows * self.dy, self.x_west + cols * self.dx, self.y_north)


def _from_ascii(text: str):
    import numpy as np

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    header, i = {}, 0
    while i < len(lines) and lines[i].split()[0].lower() in {"ncols", "nrows", "xllcorner", "yllcorner", "xllcenter", "yllcenter", "cellsize", "nodata_value", "dx", "dy"}:
        k, v = lines[i].split()[:2]
        header[k.lower()] = float(v)
        i += 1
    try:
        ncols, nrows = int(header["ncols"]), int(header["nrows"])
        cell = header.get("cellsize")
        dx, dy = header.get("dx", cell), header.get("dy", cell)
        if dx is None or dy is None:
            raise KeyError("cellsize")
        x0 = header["xllcorner"] if "xllcorner" in header else header["xllcenter"] - 0.5 * dx
        y0 = header["yllcorner"] if "yllcorner" in header else header["yllcenter"] - 0.5 * dy
    except KeyError as error:
        raise SpatialError("layer_invalid", f"ASCII grid header is missing {error}.") from error
    rows = [[float(v) for v in ln.split()] for ln in lines[i:]]
    arr = np.array(rows, dtype=float)
    if arr.shape != (nrows, ncols):
        raise SpatialError("layer_invalid", f"ASCII grid has {arr.shape} values, header says {(nrows, ncols)}.")
    nodata = header.get("nodata_value")
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr, x0, y0 + nrows * dy, dx, dy


def load_grid(spec: dict) -> Grid:
    """``spec``: crs (required) + one of ``ascii`` (text), ``path`` (.asc / .tif), or inline ``values`` with ncols, nrows..."""
    import numpy as np

    crs = parse_crs(spec.get("crs"))
    if "ascii" in spec:
        arr, xw, yn, dx, dy = _from_ascii(str(spec["ascii"]))
    elif "values" in spec:
        arr = np.array(spec["values"], dtype=float)
        if arr.ndim != 2:
            raise SpatialError("layer_invalid", "Elevation 'values' must be a 2-D list of rows (north to south).")
        cell = spec.get("cellsize")
        dx, dy = spec.get("dx", cell), spec.get("dy", cell)
        if dx is None or dy is None or "xllcorner" not in spec or "yllcorner" not in spec:
            raise SpatialError("layer_invalid", "Inline elevation needs xllcorner, yllcorner and cellsize.")
        if spec.get("nodata") is not None:
            arr[arr == spec["nodata"]] = np.nan
        xw, yn = float(spec["xllcorner"]), float(spec["yllcorner"]) + arr.shape[0] * float(dy)
        dx, dy = float(dx), float(dy)
    elif "path" in spec:
        path = Path(str(spec["path"])).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise SpatialError("layer_invalid", "Elevation path must be an absolute path to an existing .asc or .tif file.")
        if path.suffix.lower() in (".asc", ".txt"):
            arr, xw, yn, dx, dy = _from_ascii(path.read_text(encoding="utf-8"))
        else:
            try:
                import rasterio
            except ImportError as error:
                raise SpatialError("raster_unavailable", "GeoTIFF needs rasterio, which is not installed.", hint="Use an ASCII grid (.asc) or inline values.") from error
            with rasterio.open(path) as src:
                arr = src.read(1).astype(float)
                if src.nodata is not None:
                    arr[arr == src.nodata] = np.nan
                t = src.transform
                if t.b or t.d:
                    raise SpatialError("layer_invalid", "Rotated rasters are not supported.")
                xw, yn, dx, dy = t.c, t.f, t.a, -t.e
                if src.crs is not None and crs_id(parse_crs(src.crs.to_string())) != crs_id(crs):
                    raise SpatialError("crs_mismatch", f"The GeoTIFF declares {src.crs.to_string()} but the layer declares {crs_id(crs)}.")
    else:
        raise SpatialError("layer_invalid", "Elevation needs 'ascii', 'values' or 'path'.")
    if arr.size > MAX_CELLS:
        raise SpatialError("too_large", f"Elevation grid has {arr.size} cells (limit {MAX_CELLS}).")
    if dx <= 0 or dy <= 0:
        raise SpatialError("layer_invalid", "Cell size must be positive.")
    return Grid(arr, float(xw), float(yn), float(dx), float(dy), crs)


def cell_size_m(grid: Grid) -> tuple[float, float, str]:
    """Ground size of a cell in metres and a note when it is an approximation."""
    crs = grid.crs
    if crs.is_projected:
        factor = crs.axis_info[0].unit_conversion_factor
        return grid.dx * factor, grid.dy * factor, ""
    from pyproj import Geod

    west, south, east, north = grid.bounds()
    lat = (south + north) / 2
    geod = Geod(ellps="WGS84")
    mx = geod.inv(west, lat, west + grid.dx, lat)[2]
    my = geod.inv(west, lat, west, lat + grid.dy)[2]
    return mx, my, f"Geographic elevation grid: cell size converted to metres at latitude {lat:.3f} (approximation)."


def slope_pct(grid: Grid):
    """Slope in percent (100 x rise/run) from central differences; NaN where the elevation is unknown."""
    import numpy as np

    mx, my, _ = cell_size_m(grid)
    z = grid.values
    if z.shape[0] < 2 or z.shape[1] < 2:
        raise SpatialError("layer_invalid", "Elevation grid needs at least 2 x 2 cells.")
    dz_dy, dz_dx = np.gradient(z, my, mx)  # rows run north to south: sign does not matter for the magnitude
    return 100.0 * np.hypot(dz_dx, dz_dy)

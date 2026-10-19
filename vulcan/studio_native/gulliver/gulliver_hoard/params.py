"""Everything that decides how a map model comes out, as one validated object (stored in each project and sent by the UI,
the REST agent route and MCP alike). Two presets give sensible starting points: a city (buildings, streets, water at about
1:5.000) and a landscape (relief, rivers, contour lines, main roads, at 1:50.000 and beyond)."""

from __future__ import annotations

import math
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .geo import Area
from .printing import PrintProfile

Shape = Literal["rect", "circle", "hexagon", "rounded"]
RoadStyle = Literal["engraved", "raised", "flat", "none"]
RoadDetail = Literal["auto", "all", "local", "main", "none"]
ColorMode = Literal["mono", "color"]
RoofStyle = Literal["auto", "tagged", "flat"]
BuildingSource = Literal["auto", "osm", "cadastre"]
TreeSource = Literal["auto", "osm"]
DemSource = Literal["auto", "ign", "terrarium"]
FrameStyle = Literal["flat", "bevel", "step"]
Palette = Literal["classic", "sand", "grey", "white", "alpine"]
TileCuts = Literal["straight", "streets"]


PLAQUE_FRAME_MM = 14.0       # narrowest frame that carries a plaque
PANEL_FRAME_MM = 8.0         # frame of each framed panel when no frame was asked

class ModelParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # where
    lon: float = Field(..., ge=-180, le=180, description="Centre longitude (WGS84).")
    lat: float = Field(..., ge=-84, le=84, description="Centre latitude (WGS84).")
    width_m: float = Field(2000.0, ge=50, le=200_000, description="Ground width east-west in metres (the diameter for circle/hexagon).")
    height_m: float = Field(2000.0, ge=50, le=200_000, description="Ground height north-south in metres (ignored for circle/hexagon).")
    # print
    shape: Shape = "rect"
    size_mm: float = Field(180.0, ge=30, le=2000, description="Longest side of the whole model in millimetres.")
    base_mm: float = Field(4.0, ge=1.0, le=40, description="Solid base under the lowest point, in millimetres.")
    z_exag: float = Field(1.5, ge=0.2, le=20, description="Vertical exaggeration of the relief.")
    building_exag: float = Field(1.0, ge=0.2, le=10, description="Extra exaggeration of building heights (on top of the scale).")
    resolution_mm: float = Field(0.35, ge=0.15, le=3.0, description="Size of a cell of the terrain mesh, in millimetres.")
    smooth: float = Field(-1.0, ge=-1, le=10, description="Smoothing of the elevation in cells (-1 = automatic from the source resolution).")
    # layers
    buildings: bool = True
    buildings_source: BuildingSource = Field("auto", description="auto: in Spain the cadastre's buildings (exact footprints and "
                                                               "floors) with OpenStreetMap's 3D parts and roofs on top; "
                                                               "elsewhere OpenStreetMap, completed with the footprints traced "
                                                               "from imagery worldwide where the map has none.")
    min_building_mm2: float = Field(0.6, ge=0, le=100, description="Drop buildings whose footprint is smaller than this on the model.")
    min_building_height_mm: float = Field(0.6, ge=0, le=20)
    roofs: RoofStyle = Field("auto", description="auto: roofs mapped in OpenStreetMap plus pitched roofs on houses and churches; "
                                                  "tagged: only mapped roofs; flat: none.")
    water: bool = True
    water_depth_mm: float = Field(0.8, ge=0.1, le=10, description="How far water surfaces sit below the banks.")
    waterways: bool = True
    sea: bool = True
    roads: RoadStyle = "engraved"
    road_detail: RoadDetail = "auto"
    road_depth_mm: float = Field(0.2, ge=0.05, le=5, description="How far engraved roads sit below the ground (raised: above).")
    min_road_mm: float = Field(0.5, ge=0.1, le=5, description="Narrowest road on the model.")
    rails: bool = True
    rail_height_mm: float = Field(0.35, ge=0.1, le=3, description="How far railway beds stand above the ground.")
    bridges: bool = True
    arches: bool = Field(True, description="Bridges over water stand on arches and piers (off: a solid block down to the water).")
    tunnels: bool = Field(True, description="Tunnel mouths: an opening in the slope where a road or railway goes into a hill.")
    green: bool = True
    paths: bool = Field(True, description="Footpaths inside parks and woods, cut out of the green layer; with meadows on, every trail across the land, over rock and snow too.")
    min_path_mm: float = Field(0.4, ge=0.2, le=3, description="Narrowest path on the model.")
    trees: bool = Field(True, description="3D trees: mapped trees and rows, woods filled densely, parks scattered.")
    tree_density: float = Field(1.0, ge=0.0, le=3.0, description="How thickly woods and parks are filled with trees.")
    tree_size_mm: float = Field(1.1, ge=0.5, le=6, description="Smallest tree crown on the model.")
    tree_source: TreeSource = Field("auto", description="auto: every tree of the global canopy height map (with its height), the ones "
                                                         "seen on the aerial photo in Spain and the mapped ones; osm: mapped trees, "
                                                         "woods filled and parks scattered.")
    landcover: bool = Field(True, description="Fill woods, grass, fields, bare rock, snow and lakes from the satellite land cover "
                                              "(ESA WorldCover) where the map has none.")
    zones: bool = Field(True, description="Snow on high summits and bare rock on steep slopes as their own colours.")
    snow_m: float = Field(-1.0, ge=-1, le=9000, description="Snow line in metres (-1 = automatic on high mountains, 0 = none).")
    rock_slope_deg: float = Field(34.0, ge=10, le=80, description="Slopes steeper than this are bare rock (zones on).")
    ground_from_photo: bool = Field(True, description="Mountains in Spain: bare rock, meadows and woods read from the PNOA aerial "
                                    "photo (where it is unclear, rock by slope); off: rock by slope only.")
    fields: bool = Field(True, description="Farm fields one by one (cadastre parcels in Spain, traced from satellite images "
                                           "elsewhere) in their own colour, their edges as fine grooves.")
    field_rows: bool = Field(True, description="Crop rows engraved inside each field along its long side (vineyards and "
                                               "orchards closer together).")
    row_spacing_mm: float = Field(1.4, ge=0.8, le=5, description="Distance between engraved crop rows.")
    rows_from_photo: bool = Field(True, description="In Spain, crop rows follow the direction each field shows on the aerial photo "
                                                    "(else they run along the field's long side).")
    plazas: bool = Field(True, description="Squares and pedestrian areas in their own lighter colour (colour mode).")
    dsm_heights: bool = Field(True, description="Real heights of every building, with towers and spires as their own volumes, from "
                                                "the laser surface model where one is open (Spain, Netherlands, France, England, "
                                                "NRW, Switzerland, Flanders, Norway, Poland; Denmark with its token).")
    laser_roofs: bool = Field(True, description="Where the laser surface model measured a building (dsm_heights), its roof as "
                                                "the laser saw it: pitches, hips, domes, courtyards.")
    meadows: bool = Field(False, description="Colour all open land green (mountains and countryside), not only mapped parks and woods.")
    max_relief_ratio: float = Field(0.3, ge=0.05, le=2.0, description="The relief is kept at most this share of the model's longest "
                                                                         "side, lowering the vertical exaggeration if needed.")
    contours_m: float = Field(0.0, ge=-1, le=2000, description="Contour lines every N metres (0 = none, -1 = automatic).")
    contour_depth_mm: float = Field(0.3, ge=0.1, le=3)
    # finishing
    frame_mm: float = Field(0.0, ge=0, le=40, description="Width of a raised frame around the model (0 = none).")
    label: str = Field("", max_length=80, description="Text engraved on the bottom; empty = name, coordinates and scale.")
    label_bottom: bool = True
    water_lines: int = Field(0, ge=0, le=6, description="Thin lines engraved in the water following the shore of lakes and the sea "
                             "(0 = none), like depth lines on a chart (colour mode).")
    tall_buildings_m: float = Field(0.0, ge=0, le=600, description="Buildings at least this tall (m) are a part of their own, "
                                    "'Towers', in a colour of their own (0 = off).")
    palette: Palette = Field("classic", description="Colours of the parts: classic (muted map), sand (sand ground, white streets "
                             "and buildings, bright green), grey (dark grey ground, white streets and buildings), white (all "
                             "white but the water), alpine (bright meadows, dark rock, white snow).")
    frame_style: FrameStyle = Field("flat", description="flat: a plain raised band; bevel: its outer edge chamfered; step: a lower "
                                                        "outer ledge and a higher inner rim.")
    plaque: bool = Field(False, description="Raised plaque on the south side of the frame: title, coordinates and scale, a north "
                                            "arrow and a scale bar (the frame is widened to fit).")
    plaque_text: str = Field("", max_length=60, description="Title of the plaque; empty = the model's name.")
    # personal touches
    highlight_lon: Optional[float] = Field(None, ge=-180, le=180, description="A place to highlight in gold (your home): the "
                                                                              "building under it, or a marker where there is none.")
    highlight_lat: Optional[float] = Field(None, ge=-84, le=84)
    track: str = Field("", max_length=8_000_000, description="A route (GPX, KML or GeoJSON text) raised as a ribbon in its own colour.")
    track_width_mm: float = Field(0.9, ge=0.4, le=5, description="Width of the route ribbon.")
    track_height_mm: float = Field(0.6, ge=0.2, le=4, description="How far the route ribbon stands above the ground.")
    # printability
    nozzle_mm: float = Field(0.4, ge=0.1, le=1.2, description="Nozzle of the printer, for the printability report.")
    thicken: bool = Field(False, description="Widen features narrower than two nozzle passes (streets, rails, paths, thin buildings, "
                                             "tiny trees) so they print.")
    # tiles
    tiles_x: int = Field(1, ge=1, le=12)
    tiles_y: int = Field(1, ge=1, le=12)
    pins: bool = True
    pin_diameter_mm: float = Field(1.9, ge=0.8, le=8, description="Holes for alignment pins (1.9 fits 1.75 mm filament).")
    pin_depth_mm: float = Field(5.0, ge=1, le=30)
    tile_labels: bool = True
    panels_x: int = Field(1, ge=1, le=6, description="Framed panels across: the model split into pictures that hang side by side, "
                                                    "each with its own frame (the landscape runs on from one to the next).")
    panels_y: int = Field(1, ge=1, le=6, description="Framed panels down.")
    tile_cuts: TileCuts = Field("straight", description="straight: tiles cut along straight lines; streets: the seams follow "
                                                        "streets near the straight line, so no building is cut in two.")
    # output
    color: ColorMode = "color"
    dem: DemSource = "auto"
    print_profile: Optional[PrintProfile] = Field(None, description="Printer, material and price snapshot for this model; registry edits do not alter it.")

    @model_validator(mode="after")
    def _square_shapes(self) -> "ModelParams":
        if self.shape in ("circle", "hexagon"):
            object.__setattr__(self, "height_m", self.width_m)
        return self

    # -- derived ------------------------------------------------------------------------------------------------------------
    @property
    def area(self) -> Area:
        return Area(self.lon, self.lat, self.width_m, self.height_m)

    @property
    def scale_mm_per_m(self) -> float:
        return self.size_mm / max(self.width_m, self.height_m)

    @property
    def scale_denominator(self) -> float:
        return 1000.0 / self.scale_mm_per_m

    @property
    def model_size_mm(self) -> tuple[float, float]:
        k = self.scale_mm_per_m
        return self.width_m * k, self.height_m * k

    @property
    def cell_ground_m(self) -> float:
        return self.resolution_mm / self.scale_mm_per_m

    def effective_road_rank(self) -> int:
        """Highest road rank (1 motorway .. 6 paths) drawn, from road_detail and the scale."""
        if self.roads == "none" or self.road_detail == "none":
            return 0
        fixed = {"all": 6, "local": 5, "main": 2}
        if self.road_detail in fixed:
            return fixed[self.road_detail]
        d = self.scale_denominator
        if d <= 4000:
            return 6
        if d <= 12000:
            return 5
        if d <= 25000:
            return 4
        if d <= 60000:
            return 3
        if d <= 150000:
            return 2
        return 1

    def contour_interval(self, relief_m: float) -> float:
        if self.contours_m >= 0:
            return self.contours_m
        if relief_m <= 0:
            return 0.0
        raw = relief_m / 12.0
        mag = 10 ** math.floor(math.log10(raw))
        for m in (1, 2, 2.5, 5, 10):
            if raw <= m * mag:
                return m * mag
        return 10 * mag

    def effective_frame_mm(self) -> float:
        """The frame the build will really draw: framed panels get one even when none was asked, and a plaque needs a
        frame wide enough to carry it."""
        fr = self.frame_mm
        if self.panels_x * self.panels_y > 1 and fr <= 0:
            fr = PANEL_FRAME_MM
        if self.plaque and fr < PLAQUE_FRAME_MM:
            fr = PLAQUE_FRAME_MM
        return fr

    def tile_size_mm(self) -> tuple[float, float]:
        w, h = self.model_size_mm
        fr = self.effective_frame_mm()
        if self.panels_x * self.panels_y > 1:              # each framed panel: its share of the map plus its own frame
            return w / self.panels_x + 2 * fr, h / self.panels_y + 2 * fr
        return (w + 2 * fr) / self.tiles_x, (h + 2 * fr) / self.tiles_y

    def estimated_cells(self) -> int:
        w, h = self.model_size_mm
        return int((w / self.resolution_mm + 1) * (h / self.resolution_mm + 1))


PRESETS = {
    "city": {"size_mm": 180.0, "z_exag": 1.5, "building_exag": 1.0, "buildings": True, "roads": "engraved", "road_detail": "auto",
             "contours_m": 0.0, "green": True, "water": True, "rails": True, "resolution_mm": 0.3, "trees": True, "roofs": "auto",
             "palette": "sand", "water_lines": 2},
    "landscape": {"size_mm": 200.0, "building_exag": 1.0, "buildings": False, "roads": "engraved", "road_detail": "main",
                  "contours_m": -1.0, "green": True, "water": True, "rails": False, "resolution_mm": 0.4, "trees": True,
                  "zones": True, "paths": True, "meadows": True, "z_exag": 1.5, "palette": "alpine", "water_lines": 3},
    "relief": {"size_mm": 200.0, "z_exag": 2.5, "buildings": False, "roads": "none", "contours_m": 0.0, "green": False,
               "water": True, "waterways": True, "rails": False, "color": "mono", "resolution_mm": 0.35, "trees": False,
               "zones": False},
}

MAX_CELLS = 3_000_000
NULLABLE = {"highlight_lon", "highlight_lat", "print_profile"}      # an explicit null clears these on an update


def suggest_tiles(width_mm: float, height_mm: float, bed_x: float, bed_y: float, margin: float = 6.0) -> tuple[int, int]:
    """Fewest tiles so that each fits the bed (either orientation)."""
    best = None
    for tx in range(1, 13):
        for ty in range(1, 13):
            w, h = width_mm / tx, height_mm / ty
            fits = (w <= bed_x - margin and h <= bed_y - margin) or (h <= bed_x - margin and w <= bed_y - margin)
            if fits and (best is None or tx * ty < best[0] * best[1]):
                best = (tx, ty)
    return best or (12, 12)


def suggest_panels(width_mm: float, height_mm: float, frame_mm: float, bed_x: float, bed_y: float,
                   margin: float = 6.0) -> tuple[int, int]:
    """Fewest framed panels (up to 6 × 6) so that each, with its own frame, fits the bed."""
    best = None
    for px in range(1, 7):
        for py in range(1, 7):
            w, h = width_mm / px + 2 * frame_mm, height_mm / py + 2 * frame_mm
            fits = (w <= bed_x - margin and h <= bed_y - margin) or (h <= bed_x - margin and w <= bed_y - margin)
            if fits and (best is None or px * py < best[0] * best[1]):
                best = (px, py)
    return best or (6, 6)

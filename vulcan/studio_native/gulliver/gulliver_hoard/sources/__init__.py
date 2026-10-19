"""Where the data comes from. :class:`Sources` talks to the real services (with the disk cache); :class:`~.fake.FakeSources`
invents a small town for the demo mode and the tests, with the same interface."""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import aerial as _aerial
from . import canopy as _canopy
from . import landcover as _landcover
from . import msbuildings as _msb
from . import openbuildings as _ob
from . import catastro as _catastro
from . import dsm as _dsm
from . import dem as _dem
from . import geocode as _geocode
from . import osm as _osm
from .http import Fetcher


class Sources:
    fake = False

    def __init__(self, fetcher: Fetcher, *, overpass_servers: Optional[list[str]] = None,
                 keys: Optional[dict[str, str]] = None):
        self.fetcher = fetcher
        self.keys = dict(keys or {})          # keys of the sources that need one (Denmark's surface model)
        self.overpass_servers = overpass_servers or list(_osm.DEFAULT_SERVERS)

    def dem(self, bbox, want_res_m: float, *, prefer: str = "auto", progress: Optional[Callable[[str], None]] = None) -> Any:
        return _dem.fetch_dem(self.fetcher, bbox, want_res_m, prefer=prefer, progress=progress)

    def osm(self, bbox, *, progress: Optional[Callable[[str], None]] = None, cancelled: Optional[Callable[[], bool]] = None,
            rounds: int = 3) -> dict[str, Any]:
        return _osm.fetch_raw(self.fetcher, bbox, self.overpass_servers, progress=progress, cancelled=cancelled, rounds=rounds)

    def catastro(self, bbox, *, progress: Optional[Callable[[str], None]] = None,
                 cancelled: Optional[Callable[[], bool]] = None) -> Optional[list[dict[str, Any]]]:
        """Building parts from the Spanish cadastre, or None where it has no data."""
        if not _catastro.covers(bbox):
            return None
        return _catastro.fetch_parts(self.fetcher, bbox, progress=progress, cancelled=cancelled)

    def catastro_parcels(self, bbox, *, progress: Optional[Callable[[str], None]] = None,
                         cancelled: Optional[Callable[[], bool]] = None) -> Optional[list[dict[str, Any]]]:
        """Cadastral parcels (Spain), or None elsewhere."""
        if not _catastro.covers(bbox):
            return None
        return _catastro.fetch_parcels(self.fetcher, bbox, progress=progress, cancelled=cancelled)

    def aerial_trees(self, bbox, projector, *, progress: Optional[Callable[[str], None]] = None) -> Optional[list]:
        """Trees found on the aerial photo (Spain), or None where there is no photo to read."""
        if not _aerial.covers(bbox):
            return None
        rgb, box = _aerial.fetch(self.fetcher, bbox, progress=progress)
        return _aerial.trees(rgb, box, projector)

    def aerial_cover(self, bbox, m_per_px: float, *, progress: Optional[Callable[[str], None]] = None) -> Optional[tuple]:
        """Ground cover read from the aerial photo (Spain) at about ``m_per_px``: (masks, photo bbox), or None."""
        if not _aerial.covers(bbox):
            return None
        rgb, box = _aerial.fetch(self.fetcher, bbox, m_per_px=m_per_px, progress=progress)
        return _aerial.cover(rgb), box

    def aerial_photo(self, bbox, m_per_px: float, *, progress: Optional[Callable[[str], None]] = None) -> Optional[tuple]:
        """The aerial photo (Spain) at about ``m_per_px``: (RGB array, photo bbox), or None where there is none."""
        if not _aerial.covers(bbox):
            return None
        return _aerial.fetch(self.fetcher, bbox, m_per_px=m_per_px, progress=progress)
    def canopy(self, bbox, *, progress: Optional[Callable[[str], None]] = None):
        """Canopy heights (metres) over the area from the global map, and the mercator bounds of the array."""
        return _canopy.fetch(self.fetcher, bbox, progress=progress)

    def landcover(self, bbox, *, progress: Optional[Callable[[str], None]] = None):
        """ESA WorldCover classes over the area, and the lon/lat bounds of the array."""
        return _landcover.fetch(self.fetcher, bbox, progress=progress)

    def footprints(self, bbox, *, progress: Optional[Callable[[str], None]] = None) -> list[dict[str, Any]]:
        """Building footprints traced from imagery (worldwide) over the area."""
        return _msb.fetch(self.fetcher, bbox, progress=progress)

    def open_buildings(self, bbox, *, progress: Optional[Callable[[str], None]] = None) -> list[dict[str, Any]]:
        """Footprints and heights seen from space (Africa, South and South-East Asia, Latin America) over the area."""
        return _ob.fetch(self.fetcher, bbox, progress=progress)

    def dsm(self, bbox, *, res_m: float = 1.0, progress: Optional[Callable[[str], None]] = None):
        """Surface heights from an open laser survey (heights, lon/lat bounds, attribution, is_ndsm)."""
        return _dsm.fetch(self.fetcher, bbox, res_m=res_m, progress=progress, ndsm=True, keys=self.keys)

    def search(self, query: str, limit: int = 6, language: str = "es") -> list[dict[str, Any]]:
        return _geocode.search(self.fetcher, query, limit, language)

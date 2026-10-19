"""Gulliver's Hoard — printable 3D map models: pick a place on the map and get a watertight relief with buildings, water,
roads, rail and parks, cut into tiles that fit the printer bed, as STL, multi-part 3MF and a preview in the browser.

Elevation comes from the IGN MDT (Spain) or the open Terrarium tiles (the rest of the world); everything on the ground from
OpenStreetMap. The geometry is built with real booleans (manifold3d), so every file is a closed solid."""

__version__ = "0.1.0"
SERVICE = "gulliver-hoard"
APP_ID = "gulliver"
DEFAULT_PORT = 5206

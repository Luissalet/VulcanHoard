"""Availability of the spatial index used by mesh measurements."""

from importlib import import_module


def mesh_index_status() -> dict:
    try:
        import_module("rtree")
        available = True
    except (ImportError, OSError):
        available = False
    return {"dependency": "rtree", "available": available}

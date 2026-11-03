"""Spatial analysis for deciding where to act (proposal F17): coverage by network travel time versus straight line,
and parcel screening. Pure Python on top of shapely, pyproj, networkx and scipy (HiGHS); every one of them is imported
lazily by the submodule that needs it, so importing this package (and listing the tools) needs none of them.

Entry points: :mod:`.scenario` (``run_coverage``, ``run_screening``, ``check_layers``), :mod:`.example` (the invented
worked area), :mod:`.store` (saved scenarios and runs), :mod:`.tools` (agent tools)."""

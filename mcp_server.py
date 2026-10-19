"""Stdio MCP bridge for Vulcan's Hoard.

It never opens a module database. Discovery uses the owned static studio catalogue
and never starts an app or engine. Calls use the explicitly started studio and
the Bearer token from <DATA_DIR>/mcp-token. The transport is the shared CatalogBridge.
"""

from __future__ import annotations

import sys

from vulcan.hoard_link.bridge import CatalogBridge
from vulcan.studio.catalog import tool_catalog


class StudioBridge(CatalogBridge):
    async def fetch_catalog(self):
        self._catalog = tool_catalog()
        return self._catalog

    async def _prefetch(self):
        await self.fetch_catalog()

    async def tools(self, *, force=False):
        return await self.fetch_catalog()


def build() -> CatalogBridge:
    return StudioBridge(app="vulcan", service="vulcan-hoard", package="vulcan", default_port=5186, data_dir_env="VULCAN_DATA_DIR", autostart=False,
                         title="Vulcan's Hoard", root=__file__, instructions=
                         'All studio modules initially OFF. Listing tools starts no server or engine. '
                         'Use vulcan_set_module only for a module explicitly requested by the user. '
                         'models_search and the other native tools retain their schemas and native object IDs.')


if __name__ == "__main__":
    sys.exit(build().run_bridge())

"""Cold schema discovery for API and MCP; never imports an engine."""
import json
from pathlib import Path

from .projects import TOOLS as PROJECT_TOOLS
from .specs import SPECS


CONTROLS = [
    {'name': 'vulcan_modules', 'description': 'Read module availability without starting engines.', 'inputSchema': {'type': 'object', 'properties': {}}},
    {'name': 'vulcan_set_module', 'description': 'Explicitly activate or stop a native studio module. All modules initially OFF.',
     'inputSchema': {'type': 'object', 'properties': {'module': {'type': 'string', 'enum': [spec['id'] for spec in SPECS]}, 'enabled': {'type': 'boolean'}, 'automatic': {'type': 'boolean'}}, 'required': ['module', 'enabled']}},
]


def native_catalog():
    return json.loads((Path(__file__).parent / 'catalog.json').read_text(encoding='utf-8'))


def tool_catalog(catalog=None):
    return CONTROLS + PROJECT_TOOLS + [{key: value for key, value in tool.items() if key not in ('module', 'native_name', 'rest')}
                                      for tool in (native_catalog() if catalog is None else catalog)]

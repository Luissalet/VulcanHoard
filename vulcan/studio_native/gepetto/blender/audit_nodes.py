"""Read actual embedded node trees, including groups stored in DynamicFigure.blend."""

import bpy, json, sys
from pathlib import Path


def value(socket):
    v = getattr(socket, "default_value", None)
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    if hasattr(v, "name"):
        return v.name
    try:
        return list(v)
    except TypeError:
        return str(v)


groups = []
for tree in bpy.data.node_groups:
    groups.append(
        {
            "name": tree.name,
            "nodes": [
                {
                    "name": n.name,
                    "type": n.bl_idname,
                    "operation": getattr(n, "operation", None),
                    "data_type": getattr(n, "data_type", None),
                    "domain": getattr(n, "domain", None),
                    "group": getattr(getattr(n, "node_tree", None), "name", None),
                    "inputs": [
                        {"name": s.name, "value": value(s), "linked": s.is_linked} for s in n.inputs
                    ],
                }
                for n in tree.nodes
            ],
            "links": [
                [l.from_node.name, l.from_socket.name, l.to_node.name, l.to_socket.name]
                for l in tree.links
            ],
        }
    )
Path(sys.argv[-1]).write_text(json.dumps(groups, indent=2), "utf-8")

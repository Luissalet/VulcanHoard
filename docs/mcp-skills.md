# Skills in the studio MCP bridge

The studio keeps its existing static catalogue and vendored bridge. Its MCP
entrypoint attaches the shared skills handlers after building the server.
Discovery reads the copied shelf in VULCAN_DATA_DIR/skills (data/skills by
default), without looking up the library, starting modules or opening stores.
An empty shelf leaves the native catalogue unchanged.

A populated copy adds skills_list, skill_read, skills_library, skills_assign
and skill-<slug> prompts. The Hub or running app maintains that copy; attaching
the MCP server does not populate an empty shelf.

Run tests/test_studio_mcp_cold.py and tests/test_mcp_skills.py together.
The latter uses a real SDK stdio client with a populated shelf, checks native
schemas, reads a skill and its prompt, and audits engine imports, subprocess
launches, database opens and changes to existing data files.


The `blocking_freeze` tool evaluates a pinned take at the specified frame. For
print handoffs, `print_size_mm` and `sheet_wall_mm` thicken the blocking's paper
around its original plane. The pose receipt records the thickness before and
after adjustment. The exported solid still needs a printability check.

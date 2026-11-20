"""Refresh the library entries and publish the timeout extension in the cold schemas."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vulcan.agent_tools import tool_catalog  # noqa: E402


def main():
    path = ROOT / "vulcan" / "studio" / "catalog.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    live = {tool["name"]: tool for tool in tool_catalog()}
    for index, row in enumerate(rows):
        if row["module"] == "library":
            tool = live.pop(row["native_name"])
            rows[index] = {**row, **tool}
            library_end = index + 1
    rows[library_end:library_end] = [
        {"module": "library", "native_name": name, **tool} for name, tool in live.items()
    ]
    for row in rows:
        if "x-timeout-s" in row:
            row["inputSchema"]["x-timeout-s"] = row["x-timeout-s"]
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

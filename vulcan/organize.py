"""Deterministic collection organiser: a reference list -> a plan -> folders and moves, with undo.

Typical use: a folder holds hundreds of loose files and folders (one per model)
and the user has a list of groups (for example evolution lines, one per line:
`001 Bulbasaur > Ivysaur > Venusaur`). `build_plan` matches every immediate
child of the folder to a member of the list, with no model involved, and
returns a plan: which items move into which group folder, what is already in
place, what matched nothing, what matched several members and what would
collide. `Organizer.apply` carries a stored plan out and writes an undo
journal; `Organizer.undo` reverses it.

Safety rules, enforced here and not left to the caller:
- nothing is ever deleted and nothing is ever overwritten: a collision is
  skipped and reported (the only removal is `rmdir` of a folder the apply
  itself created, and only when it is empty again, during an undo);
- every source and destination stays inside the planned folder;
- plans and journals are plain JSON files under `<DATA_DIR>/organize/`.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .hoard_link import atomic, ids, paths

DEFAULT_TEMPLATE = "{number} {group}"
# Member separators: > -> => \u2192 , ; and a dash only when it has spaces around it (so Ho-Oh stays one name).
_SEPARATORS = re.compile(r"\s*(?:->|=>|>|\u2192|\u21d2|\u279c|,|;)\s*|\s+[-\u2013\u2014]\s+")
_NUMBER_PREFIX = re.compile(r"^\s*#?(\d+)(?:\s*[.):_]\s*|\s+)(\S.*)$", re.S)
_EXPLICIT_GROUP = re.compile(r"^(?P<g>[^:>,;\u2192]+?)\s*:\s+(?P<rest>\S.*)$", re.S)
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
IGNORED_NAMES = {"cults3d.json", "cults3d_template.json", "thumbs.db", "desktop.ini", ".ds_store"}
MAX_REFERENCE_BYTES = 2_000_000
REFERENCE_SUFFIXES = (".txt", ".csv", ".tsv", ".md", ".list")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


@dataclass
class MatchOptions:
    """How an item name is compared with a member name."""

    case_insensitive: bool = True
    accent_insensitive: bool = True
    ignore_separators: bool = True  # punctuation, underscores, hyphens, dots and spaces do not matter
    ignore_number_prefix: bool = True  # "0001_bulbasaur" matches "Bulbasaur"
    allow_contains: bool = True  # "Bulbasaur outline v2" matches "Bulbasaur" (whole words, longest member wins)
    include_files: bool = True
    include_folders: bool = True
    aliases: dict = field(default_factory=dict)  # {"alias as it appears in a file name": "member name in the list"}

    @classmethod
    def from_dict(cls, data: dict | None) -> "MatchOptions":
        data = dict(data or {})
        known = {f for f in cls.__dataclass_fields__}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"Unknown match option(s): {', '.join(unknown)}.")
        options = cls(**{k: v for k, v in data.items() if v is not None})
        if not isinstance(options.aliases, dict):
            raise ValueError("match.aliases must be an object {alias: member}.")
        return options

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ---------------------------------------------------------------- reference parsing

def load_reference_text(reference: str) -> tuple[str, str | None]:
    """The reference text, reading it from disk when `reference` is the path of a .txt/.csv file."""
    text = (reference or "").strip()
    if not text:
        raise ValueError("The reference list is empty.")
    if "\n" not in text and len(text) < 600:
        candidate = Path(paths.clean_user_path(text))
        looks_like_file = candidate.suffix.lower() in REFERENCE_SUFFIXES
        try:
            is_file = candidate.is_file()
        except OSError:
            is_file = False
        if is_file:
            if candidate.suffix.lower() not in REFERENCE_SUFFIXES + ("",):
                raise ValueError(f"The reference file must be one of {', '.join(REFERENCE_SUFFIXES)}.")
            if candidate.stat().st_size > MAX_REFERENCE_BYTES:
                raise ValueError("The reference file is larger than 2 MB.")
            return candidate.read_text(encoding="utf-8-sig"), str(candidate)
        if looks_like_file and (os.sep in text or "/" in text):
            raise ValueError(f"Reference file not found: {text}")
    return reference, None


def _split_number(text: str) -> tuple[str | None, str]:
    match = _NUMBER_PREFIX.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return None, text.strip()


def _parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line or line.startswith("//") or line.startswith("# "):
        return None
    explicit = _EXPLICIT_GROUP.match(line)
    if explicit and _SEPARATORS.search(explicit.group("rest")):
        group_number, group_name = _split_number(explicit.group("g"))
        remainder = explicit.group("rest")
    else:
        group_name = ""
        group_number, remainder = _split_number(line)
    members = []
    for part in _SEPARATORS.split(remainder):
        if not part.strip():
            continue
        number, name = _split_number(part)
        if name:
            members.append({"name": name, "number": number})
    if not members:
        return None
    if not group_name:
        group_name = members[0]["name"]
        if group_number and not members[0]["number"]:
            members[0]["number"] = group_number
    if not group_number:
        group_number = members[0]["number"]
    return {"name": group_name, "number": group_number, "members": members}

def _parse_csv(text: str, headerless: bool) -> tuple[list[dict], list[str]]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first = lines[0]
    delimiter = max((",", ";", "\t"), key=first.count)
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))
    warnings: list[str] = []
    columns = {"group": 0, "member": 1, "number": 2}
    if not headerless:
        header = [c.strip().lower().lstrip("\ufeff") for c in rows[0]]
        rows = rows[1:]
        aliases = {"group": ("group", "grupo", "folder", "carpeta"), "member": ("member", "miembro", "name", "nombre", "item"),
                   "number": ("number", "numero", "n\u00famero", "num", "no", "#", "id"), "group_number": ("group_number", "group number", "numero_grupo")}
        columns = {}
        for key, names in aliases.items():
            for index, cell in enumerate(header):
                if cell in names and key not in columns:
                    columns[key] = index
        if "group" not in columns or "member" not in columns:
            raise ValueError("The CSV needs at least the columns group and member (and optionally number).")
    groups: list[dict] = []
    current: dict | None = None
    for line_no, row in enumerate(rows, start=1 if headerless else 2):
        def cell(key):
            index = columns.get(key)
            return row[index].strip() if index is not None and index < len(row) else ""
        member, group, number = cell("member"), cell("group"), cell("number")
        if not member:
            if group or number:
                warnings.append(f"Row {line_no}: no member, ignored.")
            continue
        if group:
            current = next((g for g in groups if g["name"].casefold() == group.casefold()), None)
            if current is None:
                current = {"name": group, "number": None, "members": []}
                groups.append(current)
        elif current is None:
            warnings.append(f"Row {line_no}: no group yet, ignored.")
            continue
        current["members"].append({"name": member, "number": number or None})
        group_number = cell("group_number")
        if group_number and not current["number"]:
            current["number"] = group_number
    for group in groups:
        if not group["number"]:
            group["number"] = next((m["number"] for m in group["members"] if m["number"]), None)
    return groups, warnings


def parse_reference(reference: str, fmt: str = "auto") -> dict:
    """Parse a pasted list (or the path of one) into {groups, warnings, format, source}."""
    if fmt not in ("auto", "lines", "csv"):
        raise ValueError("reference_format must be auto, lines or csv.")
    text, source = load_reference_text(reference)
    text = text.lstrip("\ufeff")
    non_empty = [ln for ln in text.splitlines() if ln.strip()]
    if not non_empty:
        raise ValueError("The reference list is empty.")
    head = [c.strip().lower() for c in re.split(r"[,;\t]", non_empty[0])]
    looks_csv = len(head) >= 2 and head[0] in ("group", "grupo", "folder", "carpeta") and head[1] in ("member", "miembro", "name", "nombre", "item")
    use_csv = fmt == "csv" or (fmt == "auto" and looks_csv)
    warnings: list[str] = []
    if use_csv:
        groups, warnings = _parse_csv(text, headerless=not looks_csv)
    else:
        groups = []
        index: dict[str, dict] = {}
        for line_no, line in enumerate(text.splitlines(), start=1):
            parsed = _parse_line(line)
            if parsed is None:
                continue
            key = parsed["name"].casefold()
            if key in index:
                warnings.append(f"Line {line_no}: group '{parsed['name']}' repeats an earlier line; its members were merged.")
                known = {m["name"].casefold() for m in index[key]["members"]}
                index[key]["members"] += [m for m in parsed["members"] if m["name"].casefold() not in known]
                continue
            index[key] = parsed
            groups.append(parsed)
    if not groups:
        raise ValueError("The reference list has no usable group.")
    return {"groups": groups, "warnings": warnings, "format": "csv" if use_csv else "lines", "source": source}


def number_width(groups: list[dict], forced: int | None = None) -> int:
    if forced:
        return forced
    widths = [len(g["number"]) for g in groups if g.get("number") and g["number"].isdigit()]
    return max(widths) if widths else 0


def safe_component(name: str) -> str:
    """A name that is legal as one Windows/POSIX path component ('' when nothing usable is left)."""
    cleaned = " ".join(_ILLEGAL.sub(" ", name).split()).strip(" .")
    if not cleaned or cleaned in (".", ".."):
        return ""
    if cleaned.split(".")[0].casefold() in _RESERVED:
        cleaned += "_"
    return cleaned[:120].rstrip(" .")


class _SafeFormat(dict):
    def __missing__(self, key):
        raise ValueError(f"Unknown placeholder {{{key}}} in target_template; use {{number}}, {{group}}, {{first}}, {{last}}, {{count}} or {{index}}.")


def target_name(template: str, group: dict, width: int, index: int) -> str:
    number = group.get("number") or ""
    if number.isdigit() and width:
        number = number.zfill(width)
    members = group["members"]
    values = _SafeFormat(number=number, group=group["name"], first=members[0]["name"], last=members[-1]["name"],
                         count=str(len(members)), index=str(index).zfill(max(3, len(str(index)))))
    try:
        text = template.format_map(values)
    except (IndexError, KeyError, AttributeError, ValueError) as error:
        if isinstance(error, ValueError) and "placeholder" in str(error):
            raise
        raise ValueError(f"Invalid target_template: {error}") from error
    if not number:
        text = text.strip(" -_.")
    return safe_component(text)


# ---------------------------------------------------------------- matching

def normalise(text: str, options: MatchOptions) -> list[str]:
    """Tokens of a name under the match options (accents, case, separators, number prefix)."""
    s = unicodedata.normalize("NFKC", text).replace("\u2640", " f ").replace("\u2642", " m ")
    if options.accent_insensitive:
        s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    if options.case_insensitive:
        s = s.casefold()
    if options.ignore_separators:
        s = re.sub(r"['\u2019\u00b4`.]", "", s)
        s = re.sub(r"[\W_]+", " ", s)
    tokens = s.split()
    if options.ignore_number_prefix and not all(t.isdigit() for t in tokens):
        start = 0
        while start < len(tokens) - 1 and tokens[start].isdigit():
            start += 1
        tokens = tokens[start:]
    return tokens

class Matcher:
    """Index of every member (and alias) of the reference; `match` resolves one item name."""

    def __init__(self, groups: list[dict], options: MatchOptions):
        self.options = options
        self.groups = groups
        self.warnings: list[str] = []
        self.exact: dict[str, list[tuple[int, int, str]]] = {}
        self.spans: dict[str, list[tuple[int, int, str]]] = {}
        member_keys: dict[str, list[tuple[int, int]]] = {}
        for gi, group in enumerate(groups):
            for mi, member in enumerate(group["members"]):
                self._add(gi, mi, member["name"], "member")
                member_keys.setdefault("".join(normalise(member["name"], options)), []).append((gi, mi))
        for alias, member_name in (options.aliases or {}).items():
            targets = member_keys.get("".join(normalise(str(member_name), options)))
            if not targets:
                self.warnings.append(f"Alias '{alias}' points at '{member_name}', which is not in the list; ignored.")
                continue
            for gi, mi in targets:
                self._add(gi, mi, str(alias), "alias")

    def _add(self, gi: int, mi: int, text: str, via: str) -> None:
        tokens = normalise(text, self.options)
        if not tokens:
            return
        entry = (gi, mi, via)
        self.exact.setdefault("".join(tokens), []).append(entry)
        if len("".join(tokens)) >= 3:
            self.spans.setdefault(" ".join(tokens), []).append(entry)

    def label(self, gi: int, mi: int) -> dict:
        return {"group": self.groups[gi]["name"], "member": self.groups[gi]["members"][mi]["name"]}

    def match(self, name: str, is_dir: bool) -> dict:
        """{'status': 'matched'|'ambiguous'|'none', ...}; matched carries group index, member index and how."""
        stem = name if is_dir else (Path(name).stem or name)
        tokens = normalise(stem, self.options)
        if not tokens:
            return {"status": "none"}
        exact = {}
        for gi, mi, via in self.exact.get("".join(tokens), []):
            if (gi, mi) not in exact or via == "member":
                exact[(gi, mi)] = via
        if exact:
            return self._resolve(exact, "exact")
        if not self.options.allow_contains:
            return {"status": "none"}
        found: dict[tuple[int, int], tuple[int, str]] = {}
        for i in range(len(tokens)):
            for j in range(i + 1, len(tokens) + 1):
                key = " ".join(tokens[i:j])
                for gi, mi, via in self.spans.get(key, ()):
                    if (gi, mi) not in found or j - i > found[(gi, mi)][0]:
                        found[(gi, mi)] = (j - i, via)
        if not found:
            return {"status": "none"}
        best = max(v[0] for v in found.values())  # the member made of the most words wins; equal ones are ambiguous
        winners = {k: v[1] for k, v in found.items() if v[0] == best}
        return self._resolve(winners, "contains")

    def _resolve(self, candidates: dict, how: str) -> dict:
        if len(candidates) == 1:
            (gi, mi), via = next(iter(candidates.items()))
            return {"status": "matched", "group_index": gi, "member_index": mi, "how": "alias" if via == "alias" else how}
        return {"status": "ambiguous", "candidates": [self.label(gi, mi) for gi, mi in sorted(candidates)[:8]]}


# ---------------------------------------------------------------- planning

def _kind(entry: os.DirEntry) -> str:
    try:
        return "folder" if entry.is_dir(follow_symlinks=False) else "file"
    except OSError:
        return "file"


def _children(folder: Path) -> list[os.DirEntry]:
    with os.scandir(folder) as scan:
        entries = [e for e in scan if not e.name.startswith(".") and e.name.casefold() not in IGNORED_NAMES]
    return sorted(entries, key=lambda e: e.name.casefold())


def build_plan(root: str, reference: str, target_template: str = DEFAULT_TEMPLATE, match: dict | MatchOptions | None = None,
               reference_format: str = "auto", number_width_override: int | None = None) -> dict:
    """Match the immediate children of `root` against the reference. Read-only; returns the plan dict (without an id)."""
    folder = Path(root).expanduser()
    if not folder.is_dir():
        raise ValueError(f"The folder does not exist: {root}")
    folder = folder.resolve()
    options = match if isinstance(match, MatchOptions) else MatchOptions.from_dict(match)
    template = (target_template or DEFAULT_TEMPLATE).strip() or DEFAULT_TEMPLATE
    parsed = parse_reference(reference, reference_format)
    groups = parsed["groups"]
    width = number_width(groups, number_width_override)
    warnings = list(parsed["warnings"])
    matcher = Matcher(groups, options)
    warnings += matcher.warnings

    conflicts: list[dict] = []
    targets: list[str] = []
    owner: dict[str, int] = {}  # casefolded target name -> group index
    blocked: set[int] = set()
    for gi, group in enumerate(groups):
        name = target_name(template, group, width, gi + 1)
        targets.append(name)
        if not name:
            blocked.add(gi)
            conflicts.append({"type": "invalid_target_name", "group": group["name"], "detail": "The target template produced no usable folder name."})
            continue
        key = name.casefold()
        if key in owner:
            blocked.add(gi)
            conflicts.append({"type": "duplicate_target", "group": group["name"], "target": name,
                              "detail": f"Same folder name as group '{groups[owner[key]]['name']}'; its items were not planned."})
            continue
        owner[key] = gi

    per_group: dict[int, list[dict]] = {}
    in_place: list[dict] = []
    unmatched: list[dict] = []
    ambiguous: list[dict] = []
    scanned = ignored = matched_total = 0
    target_dirs: dict[int, Path] = {}
    for entry in _children(folder):
        kind = _kind(entry)
        if (kind == "file" and not options.include_files) or (kind == "folder" and not options.include_folders):
            ignored += 1
            continue
        scanned += 1
        key = entry.name.casefold()
        if key in owner:
            gi = owner[key]
            if kind == "folder":
                in_place.append({"name": entry.name, "group": groups[gi]["name"], "kind": "target_folder"})
                target_dirs[gi] = Path(entry.path)
            else:
                conflicts.append({"type": "target_is_file", "name": entry.name, "group": groups[gi]["name"], "target": targets[gi],
                                  "detail": "A file already has the name of the target folder."})
            continue
        result = matcher.match(entry.name, kind == "folder")
        if result["status"] == "none":
            unmatched.append({"name": entry.name, "kind": kind})
            continue
        if result["status"] == "ambiguous":
            ambiguous.append({"name": entry.name, "kind": kind, "candidates": result["candidates"]})
            continue
        matched_total += 1
        gi, mi = result["group_index"], result["member_index"]
        if gi in blocked:
            conflicts.append({"type": "duplicate_target", "name": entry.name, "group": groups[gi]["name"], "detail": "Its group has no usable, unique target folder."})
            continue
        per_group.setdefault(gi, []).append({"name": entry.name, "kind": kind, "member": groups[gi]["members"][mi]["name"], "match": result["how"]})

    moves: list[dict] = []
    out_groups: list[dict] = []
    for gi, group in enumerate(groups):
        if gi in blocked:
            continue
        target = targets[gi]
        tpath = folder / target
        exists = tpath.exists()
        is_dir = tpath.is_dir()
        items = []
        for item in per_group.get(gi, []):
            if exists and not is_dir:
                conflicts.append({"type": "target_is_file", "name": item["name"], "group": group["name"], "target": target,
                                  "detail": "The target path exists and is not a folder."})
                continue
            if os.path.lexists(tpath / item["name"]):
                conflicts.append({"type": "destination_exists", "name": item["name"], "group": group["name"], "target": target,
                                  "detail": "An item with that name is already inside the target folder; it was not planned."})
                continue
            items.append(item)
            moves.append({"name": item["name"], "kind": item["kind"], "target": target, "group": group["name"], "member": item["member"]})
        inside: list[str] = []
        inside_members: set[str] = set()
        if exists and is_dir:
            for child in _children(tpath):
                found = matcher.match(child.name, _kind(child) == "folder")
                if found["status"] == "matched" and found["group_index"] == gi:
                    inside.append(child.name)
                    inside_members.add(group["members"][found["member_index"]]["name"].casefold())
        if items or inside or (exists and is_dir):
            seen = {i["member"].casefold() for i in items} | inside_members
            out_groups.append({
                "group": group["name"], "number": group.get("number"), "target": target, "exists": bool(exists and is_dir),
                "items": items, "already_inside": inside,
                "missing_members": [m["name"] for m in group["members"] if m["name"].casefold() not in seen] if (items or inside) else [],
            })

    summary = {
        "groups_total": len(groups), "groups_with_items": sum(1 for g in out_groups if g["items"]),
        "groups_existing": sum(1 for g in out_groups if g["exists"]), "items_scanned": scanned, "items_ignored": ignored,
        "matched": matched_total, "to_move": len(moves), "in_place": len(in_place),
        "already_inside": sum(len(g["already_inside"]) for g in out_groups), "unmatched": len(unmatched),
        "ambiguous": len(ambiguous), "conflicts": len(conflicts),
    }
    return {
        "root": str(folder), "target_template": template, "number_width": width, "options": options.to_dict(),
        "reference": {"format": parsed["format"], "source": parsed["source"], "groups": len(groups), "members": sum(len(g["members"]) for g in groups)},
        "summary": summary, "groups": out_groups, "in_place": in_place, "unmatched": unmatched, "ambiguous": ambiguous,
        "conflicts": conflicts, "warnings": warnings, "moves": moves,
    }

def plan_view(plan: dict, cap: int = 300) -> dict:
    """The plan as returned to callers: long lists are cut at `cap` entries (the stored plan keeps everything)."""
    view = {k: v for k, v in plan.items() if k != "moves"}
    for key in ("unmatched", "ambiguous", "conflicts", "in_place", "groups"):
        rows = plan.get(key) or []
        if len(rows) > cap:
            view[key] = rows[:cap]
            view[f"{key}_truncated"] = len(rows) - cap
    return view


# ---------------------------------------------------------------- storage, apply, undo

def _atomic_json(path: Path, data: dict) -> None:
    atomic.write_json_atomic(path, data, indent=1)


def _new_id(prefix: str) -> str:
    return ids.new_id(prefix)


class Organizer:
    """Stores plans and apply journals under `base_dir` and runs apply/undo (one at a time)."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._lock = threading.Lock()

    # ---------- files ----------
    def _file(self, kind: str, item_id: str) -> Path:
        if not _ID_RE.match(item_id or ""):
            raise LookupError(f"Unknown {kind} id.")
        return self.base_dir / f"{kind}s" / f"{item_id}.json"

    def _load(self, kind: str, item_id: str) -> dict:
        path = self._file(kind, item_id)
        if not path.is_file():
            raise LookupError(f"{kind.capitalize()} '{item_id}' not found.")
        return json.loads(path.read_text(encoding="utf-8"))

    def _list(self, kind: str) -> list[dict]:
        folder = self.base_dir / f"{kind}s"
        rows = []
        if folder.is_dir():
            for path in folder.glob("*.json"):
                try:
                    rows.append(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    continue
        return sorted(rows, key=lambda r: r.get("created_at") or r.get("started_at") or 0, reverse=True)

    # ---------- plans ----------
    def plan(self, root: str, reference: str, target_template: str = DEFAULT_TEMPLATE, match: dict | None = None,
             reference_format: str = "auto", number_width_override: int | None = None) -> dict:
        plan = build_plan(root, reference, target_template, match, reference_format, number_width_override)
        plan["id"] = _new_id("plan")
        plan["created_at"] = time.time()
        plan["applies"] = []
        _atomic_json(self._file("plan", plan["id"]), plan)
        return plan

    def get_plan(self, plan_id: str) -> dict:
        return self._load("plan", plan_id)

    def list_plans(self, limit: int = 20) -> list[dict]:
        return [{"id": p["id"], "created_at": p["created_at"], "root": p["root"], "summary": p["summary"], "applies": p.get("applies", [])}
                for p in self._list("plan")[:limit]]

    # ---------- journals ----------
    def get_apply(self, apply_id: str) -> dict:
        return self._load("journal", apply_id)

    def list_applies(self, limit: int = 20) -> list[dict]:
        return [{"id": j["id"], "plan_id": j["plan_id"], "root": j["root"], "started_at": j["started_at"], "moved": j["counts"]["moved"],
                 "created_folders": len(j["created_dirs"]), "fully_undone": j.get("fully_undone", False)} for j in self._list("journal")[:limit]]

    def apply(self, plan_id: str) -> dict:
        plan = self.get_plan(plan_id)
        root = Path(plan["root"])
        if not root.is_dir():
            raise ValueError(f"The planned folder no longer exists: {root}")
        with self._lock:
            journal = {"id": _new_id("apply"), "plan_id": plan_id, "root": str(root), "started_at": time.time(), "finished_at": None,
                       "created_dirs": [], "moves": [], "skipped": [], "errors": [], "counts": {"moved": 0, "skipped": 0, "errors": 0}}
            path = self._file("journal", journal["id"])
            _atomic_json(path, journal)
            try:
                for index, move in enumerate(plan.get("moves", [])):
                    self._move_one(root, move, journal)
                    if index % 50 == 49:
                        _atomic_json(path, journal)
            finally:
                journal["finished_at"] = time.time()
                journal["counts"] = {"moved": len(journal["moves"]), "skipped": len(journal["skipped"]), "errors": len(journal["errors"])}
                _atomic_json(path, journal)
            plan.setdefault("applies", []).append(journal["id"])
            _atomic_json(self._file("plan", plan_id), plan)
        return journal

    def _move_one(self, root: Path, move: dict, journal: dict) -> None:
        name, target = move["name"], move["target"]
        entry = {"name": name, "target": target, "kind": move.get("kind", "file"), "group": move.get("group", "")}
        if safe_component(target) != target or name in ("", ".", "..") or "/" in name or "\\" in name:
            journal["skipped"].append({**entry, "reason": "unsafe_name"})
            return
        src, tdir = root / name, root / target
        dest = tdir / name
        if os.path.normcase(str(src)) == os.path.normcase(str(tdir)):
            journal["skipped"].append({**entry, "reason": "item_is_the_target_folder"})
            return
        if not os.path.lexists(src):
            journal["skipped"].append({**entry, "reason": "source_missing"})
            return
        if os.path.lexists(tdir) and not tdir.is_dir():
            journal["skipped"].append({**entry, "reason": "target_is_file"})
            return
        if os.path.lexists(dest):
            journal["skipped"].append({**entry, "reason": "destination_exists"})
            return
        try:
            if not tdir.exists():
                tdir.mkdir()
                journal["created_dirs"].append(str(tdir))
            os.rename(src, dest)
        except OSError as error:
            journal["errors"].append({**entry, "error": f"{type(error).__name__}: {error}"[:300]})
            return
        journal["moves"].append({**entry, "src": str(src), "dest": str(dest)})

    def last_apply_id(self) -> str | None:
        for journal in self._list("journal"):
            if not journal.get("fully_undone") and journal["moves"]:
                return journal["id"]
        return None

    def undo(self, apply_id: str | None = None, remove_created_folders: bool = True) -> dict:
        apply_id = apply_id or self.last_apply_id()
        if apply_id is None:
            raise LookupError("There is no applied plan to undo.")
        with self._lock:
            journal = self.get_apply(apply_id)
            restored, skipped, kept, removed = 0, [], [], 0
            for move in reversed(journal["moves"]):
                if move.get("undone"):
                    continue
                src, dest = Path(move["src"]), Path(move["dest"])
                if not os.path.lexists(dest):
                    skipped.append({"name": move["name"], "reason": "moved_item_missing"})
                    continue
                if os.path.lexists(src):
                    skipped.append({"name": move["name"], "reason": "original_location_occupied"})
                    continue
                try:
                    os.rename(dest, src)
                except OSError as error:
                    skipped.append({"name": move["name"], "reason": f"{type(error).__name__}: {error}"[:200]})
                    continue
                move["undone"] = True
                restored += 1
            if remove_created_folders:
                for folder in reversed(journal["created_dirs"]):
                    path = Path(folder)
                    if not path.is_dir():
                        continue
                    try:
                        if any(path.iterdir()):
                            kept.append(folder)
                            continue
                        path.rmdir()  # empty folder that the apply itself created
                        removed += 1
                    except OSError:
                        kept.append(folder)
            journal["fully_undone"] = all(m.get("undone") for m in journal["moves"])
            journal["undone_at"] = time.time()
            _atomic_json(self._file("journal", apply_id), journal)
        return {"undo_of": apply_id, "root": journal["root"], "restored": restored, "skipped": skipped,
                "removed_folders": removed, "kept_folders": kept, "fully_undone": journal["fully_undone"]}
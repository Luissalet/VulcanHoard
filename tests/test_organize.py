"""Collection organiser: reference parsing, matching, plans, apply without overwrite, undo, tools and API."""

import json
from pathlib import Path

import pytest

from vulcan.agent_tools import TOOLS, call_tool
from vulcan.organize import MatchOptions, Matcher, Organizer, build_plan, normalise, parse_reference, safe_component, target_name

LIST = """001 Bulbasaur > Ivysaur > Venusaur
004 Charmander > Charmeleon > Charizard
025 Pichu > Pikachu -> Raichu
// a comment line
250 Ho-Oh
122 Mr. Mime
"""


def touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def files_only(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): p.read_text(encoding="utf-8") for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def loose(tmp_path) -> Path:
    base = tmp_path / "loose"
    for name in ("0001_Bulbasaur.stl", "Ivysaur outline v2.stl", "VENUSAUR.png", "Charmander.stl", "charmele\u00f3n.stl", "Pikachu.stl",
                 "Ho_Oh.stl", "mr_mime.3mf", "notes.txt", "Ivysaur Venusaur combo.stl"):
        touch(base / name, name)
    (base / "Raichu").mkdir()
    touch(base / "Raichu" / "raichu_front.stl", "raichu")
    (base / ".hidden").mkdir()
    return base


# ---------------- parsing ----------------

def test_parse_lines_numbers_separators_and_comments():
    parsed = parse_reference(LIST)
    groups = {g["name"]: g for g in parsed["groups"]}
    assert list(groups) == ["Bulbasaur", "Charmander", "Pichu", "Ho-Oh", "Mr. Mime"]
    assert [m["name"] for m in groups["Bulbasaur"]["members"]] == ["Bulbasaur", "Ivysaur", "Venusaur"]
    assert groups["Bulbasaur"]["number"] == "001" and groups["Bulbasaur"]["members"][0]["number"] == "001"
    assert [m["name"] for m in groups["Pichu"]["members"]] == ["Pichu", "Pikachu", "Raichu"]  # '->' separator
    assert [m["name"] for m in groups["Ho-Oh"]["members"]] == ["Ho-Oh"]  # a dash without spaces is part of the name
    assert parsed["format"] == "lines"


def test_parse_alternative_separators_and_explicit_group_and_member_numbers():
    parsed = parse_reference("Venusaur line: 001 Bulbasaur, 002 Ivysaur \u2192 003 Venusaur\nPichu - Pikachu - Raichu\nType: Null")
    first, second, third = parsed["groups"]
    assert first["name"] == "Venusaur line" and first["number"] == "001"
    assert [(m["name"], m["number"]) for m in first["members"]] == [("Bulbasaur", "001"), ("Ivysaur", "002"), ("Venusaur", "003")]
    assert [m["name"] for m in second["members"]] == ["Pichu", "Pikachu", "Raichu"]
    assert third["name"] == "Type: Null" and len(third["members"]) == 1  # a colon without a list is just a name


def test_parse_repeated_group_merges_with_warning():
    parsed = parse_reference("Pichu > Pikachu\nPichu > Raichu")
    assert len(parsed["groups"]) == 1 and [m["name"] for m in parsed["groups"][0]["members"]] == ["Pichu", "Pikachu", "Raichu"]
    assert parsed["warnings"]


def test_parse_csv_with_and_without_header_and_from_file(tmp_path):
    csv_text = "group,member,number\nVenusaur line,Bulbasaur,1\n,Ivysaur,2\nVenusaur line,Venusaur,3\nPichu line,Pichu,172\n"
    parsed = parse_reference(csv_text)
    assert parsed["format"] == "csv" and [g["name"] for g in parsed["groups"]] == ["Venusaur line", "Pichu line"]
    assert [m["name"] for m in parsed["groups"][0]["members"]] == ["Bulbasaur", "Ivysaur", "Venusaur"] and parsed["groups"][0]["number"] == "1"
    headerless = parse_reference("A line,Alpha,7\nA line,Beta,8", fmt="csv")
    assert headerless["groups"][0]["number"] == "7" and len(headerless["groups"][0]["members"]) == 2
    path = tmp_path / "list.csv"
    path.write_text(csv_text, encoding="utf-8-sig")
    from_file = parse_reference(str(path))
    assert from_file["source"] == str(path) and len(from_file["groups"]) == 2
    with pytest.raises(ValueError, match="not found"):
        parse_reference(str(tmp_path / "missing.txt"))
    with pytest.raises(ValueError):
        parse_reference("   ")
    spanish = parse_reference("grupo;miembro;numero\nA;x;1\nA;y;2")  # other header names and delimiter
    assert spanish["groups"][0]["name"] == "A" and len(spanish["groups"][0]["members"]) == 2


def test_target_name_padding_template_and_safety():
    groups = parse_reference("1 Bulbasaur > Ivysaur\n25 Pikachu\n150 Mewtwo")["groups"]
    width = max(len(g["number"]) for g in groups)
    assert width == 3
    assert target_name("{number} {group}", groups[0], width, 1) == "001 Bulbasaur"
    assert target_name("{group} ({count})", groups[0], width, 1) == "Bulbasaur (2)"
    assert target_name("{index}-{last}", groups[0], width, 7) == "007-Ivysaur"
    nameless = {"name": "Unnumbered", "number": None, "members": [{"name": "Unnumbered", "number": None}]}
    assert target_name("{number} {group}", nameless, 3, 1) == "Unnumbered"
    with pytest.raises(ValueError, match="placeholder"):
        target_name("{nope}", groups[0], width, 1)
    assert safe_component('a<b>:c"/d\\e|f?g*') == "a b c d e f g"
    assert safe_component("..") == "" and safe_component("CON") == "CON_" and safe_component("name. ") == "name"


# ---------------- matching ----------------

def matcher(list_text, **options):
    groups = parse_reference(list_text)["groups"]
    return Matcher(groups, MatchOptions.from_dict(options)), groups


def member_of(m, groups, name, is_dir=False):
    found = m.match(name, is_dir)
    assert found["status"] == "matched", (name, found)
    return groups[found["group_index"]]["members"][found["member_index"]]["name"]


def test_matching_ignores_case_accents_separators_and_number_prefix():
    m, groups = matcher(LIST)
    for name, member in (("0001_Bulbasaur.stl", "Bulbasaur"), ("CHARMELE\u00d3N.STL", "Charmeleon"), ("ho_oh.stl", "Ho-Oh"),
                         ("Mr Mime.3mf", "Mr. Mime"), ("MrMime.stl", "Mr. Mime"), ("025 - Pikachu.png", "Pikachu")):
        assert member_of(m, groups, name) == member
    assert m.match("Squirtle.stl", False)["status"] == "none"


def test_matching_contains_prefers_longest_and_reports_ambiguity():
    m, groups = matcher("Pikachu line: Pichu > Pikachu > Raichu\nPikachu Libre\nMew > Mewtwo")
    found = m.match("Pikachu Libre front.stl", False)
    assert groups[found["group_index"]]["name"] == "Pikachu Libre"  # the longer member wins over the contained 'Pikachu'
    assert member_of(m, groups, "Mewtwo.stl") == "Mewtwo"  # whole words only: 'mew' is not inside 'mewtwo'
    ambiguous = m.match("Pichu Raichu combo.stl", False)
    assert ambiguous["status"] == "ambiguous" and {c["member"] for c in ambiguous["candidates"]} == {"Pichu", "Raichu"}


def test_matching_options_and_aliases():
    m, groups = matcher(LIST, allow_contains=False)
    assert m.match("Ivysaur outline v2.stl", False)["status"] == "none"
    assert m.match("Ivysaur.stl", False)["status"] == "matched"
    strict, _ = matcher(LIST, case_insensitive=False)
    assert strict.match("ivysaur.stl", False)["status"] == "none" and strict.match("Ivysaur.stl", False)["status"] == "matched"
    aliased, groups = matcher(LIST, aliases={"fushigidane": "Bulbasaur", "ghost": "Nobody"})
    found = aliased.match("fushigidane.stl", False)
    assert found["status"] == "matched" and found["how"] == "alias" and groups[found["group_index"]]["name"] == "Bulbasaur"
    assert any("Nobody" in w for w in aliased.warnings)
    with pytest.raises(ValueError, match="Unknown match option"):
        MatchOptions.from_dict({"bogus": True})
    assert normalise("0001_Pok\u00e9mon-Y", MatchOptions()) == ["pokemon", "y"]
    assert normalise("1-3", MatchOptions()) == ["1", "3"]  # a name made only of numbers is kept whole

# ---------------- plan ----------------

def test_plan_groups_unmatched_ambiguous_and_in_place(loose):
    (loose / "004 Charmander").mkdir()  # target folder that already exists
    touch(loose / "004 Charmander" / "charizard.stl", "charizard")
    plan = build_plan(str(loose), LIST)
    summary = plan["summary"]
    by_target = {g["target"]: g for g in plan["groups"]}
    assert set(by_target) == {"001 Bulbasaur", "004 Charmander", "025 Pichu", "250 Ho-Oh", "122 Mr. Mime"}
    assert {i["name"] for i in by_target["001 Bulbasaur"]["items"]} == {"0001_Bulbasaur.stl", "Ivysaur outline v2.stl", "VENUSAUR.png"}
    assert {i["name"] for i in by_target["004 Charmander"]["items"]} == {"Charmander.stl", "charmele\u00f3n.stl"}
    assert by_target["004 Charmander"]["exists"] and by_target["004 Charmander"]["already_inside"] == ["charizard.stl"]
    assert by_target["004 Charmander"]["missing_members"] == []
    assert by_target["001 Bulbasaur"]["missing_members"] == []
    assert {i["name"] for i in by_target["025 Pichu"]["items"]} == {"Pikachu.stl", "Raichu"}  # a folder matches too
    assert by_target["025 Pichu"]["missing_members"] == ["Pichu"]
    assert plan["in_place"] == [{"name": "004 Charmander", "group": "Charmander", "kind": "target_folder"}]
    assert [u["name"] for u in plan["unmatched"]] == ["notes.txt"]
    assert [a["name"] for a in plan["ambiguous"]] == ["Ivysaur Venusaur combo.stl"]
    assert summary["to_move"] == 9 and summary["unmatched"] == 1 and summary["ambiguous"] == 1 and summary["in_place"] == 1
    assert summary["groups_total"] == 5 and ".hidden" not in json.dumps(plan)
    assert plan["number_width"] == 3 and plan["reference"]["groups"] == 5


def test_plan_is_read_only(loose):
    before = tree(loose)
    build_plan(str(loose), LIST)
    assert tree(loose) == before


def test_plan_conflicts(loose):
    touch(loose / "025 Pichu", "I am a file")  # the target path is a file
    touch(loose / "001 Bulbasaur" / "VENUSAUR.png", "already there")  # the destination is taken
    plan = build_plan(str(loose), LIST)
    kinds = {(c["type"], c.get("name")) for c in plan["conflicts"]}
    assert ("target_is_file", "025 Pichu") in kinds  # the file with the target's name
    assert ("target_is_file", "Pikachu.stl") in kinds and ("target_is_file", "Raichu") in kinds
    assert ("destination_exists", "VENUSAUR.png") in kinds
    moved = {m["name"] for m in plan["moves"]}
    assert "VENUSAUR.png" not in moved and "Pikachu.stl" not in moved and "0001_Bulbasaur.stl" in moved


def test_plan_duplicate_targets_and_options(loose):
    plan = build_plan(str(loose), "Pichu > Pikachu\nPikachu > Raichu", target_template="{number}")
    assert any(c["type"] == "invalid_target_name" for c in plan["conflicts"])
    twin = build_plan(str(loose), "1 Bulbasaur\n2 Ivysaur", target_template="Same")
    assert any(c["type"] == "duplicate_target" for c in twin["conflicts"])
    folders_only = build_plan(str(loose), LIST, match={"include_files": False})
    assert [m["name"] for m in folders_only["moves"]] == ["Raichu"] and folders_only["summary"]["items_ignored"] > 0
    with pytest.raises(ValueError, match="does not exist"):
        build_plan(str(loose / "nope"), LIST)


# ---------------- apply / undo ----------------

def test_apply_moves_without_deleting_and_undo_restores(loose, tmp_path):
    org = Organizer(tmp_path / "organize")
    before_files = files_only(loose)
    plan = org.plan(str(loose), LIST)
    assert (tmp_path / "organize" / "plans" / f"{plan['id']}.json").is_file()
    journal = org.apply(plan["id"])
    assert journal["counts"]["moved"] == 9 and journal["counts"]["skipped"] == 0 and len(journal["created_dirs"]) == 5
    assert (loose / "001 Bulbasaur" / "0001_Bulbasaur.stl").is_file() and (loose / "025 Pichu" / "Raichu" / "raichu_front.stl").is_file()
    assert not (loose / "Pikachu.stl").exists() and (loose / "notes.txt").is_file()
    after = files_only(loose)
    assert len(after) == len(before_files) and sorted(Path(k).name for k in after) == sorted(Path(k).name for k in before_files)  # nothing deleted
    assert sorted(after.values()) == sorted(before_files.values())  # nothing changed

    undone = org.undo()
    assert undone["undo_of"] == journal["id"] and undone["restored"] == 9 and undone["removed_folders"] == 5 and undone["fully_undone"]
    assert files_only(loose) == before_files
    assert not (loose / "001 Bulbasaur").exists()
    with pytest.raises(LookupError):
        org.undo()  # nothing left to undo


def test_apply_never_overwrites_and_reports_skips(loose, tmp_path):
    org = Organizer(tmp_path / "organize")
    plan = org.plan(str(loose), LIST)
    touch(loose / "001 Bulbasaur" / "VENUSAUR.png", "someone else's file")  # appears after the plan was made
    (loose / "Charmander.stl").unlink()  # disappears after the plan was made
    journal = org.apply(plan["id"])
    reasons = {(s["name"], s["reason"]) for s in journal["skipped"]}
    assert ("VENUSAUR.png", "destination_exists") in reasons and ("Charmander.stl", "source_missing") in reasons
    assert (loose / "VENUSAUR.png").read_text(encoding="utf-8") == "VENUSAUR.png"  # the loose original stayed
    assert (loose / "001 Bulbasaur" / "VENUSAUR.png").read_text(encoding="utf-8") == "someone else's file"
    again = org.apply(plan["id"])  # a second apply of the same plan moves nothing new
    assert again["counts"]["moved"] == 0 and {s["reason"] for s in again["skipped"]} <= {"source_missing", "destination_exists"}


def test_apply_blocks_a_tampered_plan(loose, tmp_path):
    org = Organizer(tmp_path / "organize")
    plan = org.plan(str(loose), LIST)
    path = tmp_path / "organize" / "plans" / f"{plan['id']}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["moves"][0]["target"] = ".."
    data["moves"][1]["name"] = "..\\escape.stl"
    path.write_text(json.dumps(data), encoding="utf-8")
    journal = org.apply(plan["id"])
    assert {s["reason"] for s in journal["skipped"]} >= {"unsafe_name"}
    assert not (loose.parent / "escape.stl").exists()


def test_undo_skips_occupied_locations_and_keeps_non_empty_folders(loose, tmp_path):
    org = Organizer(tmp_path / "organize")
    journal = org.apply(org.plan(str(loose), LIST)["id"])
    touch(loose / "Pikachu.stl", "a new file that took the original place")
    touch(loose / "250 Ho-Oh" / "other.stl", "added later")
    result = org.undo(journal["id"])
    assert any(s["name"] == "Pikachu.stl" and s["reason"] == "original_location_occupied" for s in result["skipped"])
    assert (loose / "025 Pichu" / "Pikachu.stl").is_file() and (loose / "Pikachu.stl").read_text(encoding="utf-8") == "a new file that took the original place"
    assert {Path(k).name for k in result["kept_folders"]} >= {"250 Ho-Oh", "025 Pichu"} and (loose / "250 Ho-Oh" / "other.stl").is_file()
    assert result["fully_undone"] is False
    assert org.last_apply_id() == journal["id"]  # still has something to undo
    assert org.list_applies()[0]["id"] == journal["id"] and org.list_plans()[0]["id"] == journal["plan_id"]


def test_ids_cannot_escape_the_store(tmp_path):
    org = Organizer(tmp_path / "organize")
    for bad in ("../x", "..\\x", "a/b", ""):
        with pytest.raises(LookupError):
            org.get_plan(bad)
        with pytest.raises(LookupError):
            org.get_apply(bad)


# ---------------- tools and API ----------------

def test_tools_registered_with_bilingual_descriptions():
    names = {t.name: t for t in TOOLS}
    for name in ("collection_plan", "collection_apply", "collection_undo", "sheets_batch"):
        assert name in names and "Sin\u00f3nimos:" in names[name].description and len(names[name].description.split("\n")[0]) <= 110
    assert names["collection_plan"].annotations["readOnlyHint"] is True
    assert names["collection_apply"].annotations["readOnlyHint"] is False and names["collection_apply"].annotations["destructiveHint"] is False


def test_collection_tools_roundtrip_and_rescan(scanned, library):
    services, root = scanned
    plan = call_tool(services, "collection_plan", {"root": str(library), "reference": "1 Esfera > Pieza\n2 Dragon cubo > Dos cuerpos",
                                                    "target_template": "{number} {group}", "match": {"aliases": {"pieza_en_pulgadas": "Pieza"}}})
    assert plan["summary"]["to_move"] == 2 and plan["id"] and "moves" not in plan and "Nothing was moved" in plan["note"]
    with pytest.raises(ValueError):
        call_tool(services, "collection_plan", {"root": str(library / "nope"), "reference": "a"})
    with pytest.raises(ValueError):
        call_tool(services, "collection_plan", {"root": str(library), "reference": "a", "match": {"bogus": 1}})
    applied = call_tool(services, "collection_apply", {"plan_id": plan["id"]})
    assert applied["ok"] and applied["counts"]["moved"] == 2 and applied["rescan_queued"] == [root.id]
    assert (library / "1 Esfera" / "esfera_lisa.obj").is_file()
    assert services.worker.wait_idle(120)
    with services.db.lock:
        paths = {r["rel_path"] for r in services.db.conn.execute("SELECT rel_path FROM models WHERE root_id = ?", (root.id,))}
    assert "1 Esfera/esfera_lisa.obj" in paths and "esfera_lisa.obj" not in paths
    undone = call_tool(services, "collection_undo", {})
    assert undone["ok"] and undone["restored"] == 2 and undone["rescan_queued"] == [root.id]
    assert (library / "esfera_lisa.obj").is_file() and not (library / "1 Esfera").exists()
    assert services.worker.wait_idle(120)


def test_organize_http_api(client, library):
    plan = client.post("/api/organize/plan", json={"root": str(library), "reference": "1 Soportes demo > Caja abierta\n2 Esfera > Esfera lisa"})
    assert plan.status_code == 200, plan.text
    plan = plan.json()
    assert plan["summary"]["to_move"] == 1
    assert client.get("/api/organize/plans").json()["plans"][0]["id"] == plan["id"]
    assert client.get(f"/api/organize/plans/{plan['id']}").json()["id"] == plan["id"]
    assert client.get("/api/organize/plans/nope-nope").status_code == 404
    assert client.post("/api/organize/plan", json={"root": str(library / "nope"), "reference": "a"}).status_code == 400
    assert client.post("/api/organize/plan", json={"root": str(library)}).status_code == 400
    applied = client.post("/api/organize/apply", json={"plan_id": plan["id"]})
    assert applied.status_code == 200 and applied.json()["counts"]["moved"] == 1
    assert client.get("/api/organize/applies").json()["applies"][0]["id"] == applied.json()["apply_id"]
    undone = client.post("/api/organize/undo", json={})
    assert undone.status_code == 200 and undone.json()["fully_undone"] is True
    assert client.post("/api/organize/undo", json={}).status_code == 404
    assert client.post("/api/organize/apply", json={"plan_id": "nope-nope"}).status_code == 404
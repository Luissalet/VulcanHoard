"""Runs one sand3d program in the engine's own Python (the one that has ``bpy``); never imported by the app's venv at run time.

``python -I sand3d_runner.py job.json`` reads ``{"code", "work", "timeout"}``, checks the program, executes it with a reduced
set of builtins and modules, finds the object it built (the variable ``model``, else ``result``, else the only top object in the
scene), exports ``out.glb`` and ``out.stl`` (both in metres, as sand3d writes them) into ``work`` and writes ``result.json`` or
``error.json`` there. The parent process owns the time limit and kills the whole tree when it runs out.

``check_program`` only needs the standard library, so the app imports this module to refuse a program before it starts a process.
"""

from __future__ import annotations

import ast
import builtins
import io
import json
import sys
import traceback
from pathlib import Path

ALLOWED_MODULES = {"sand3d", "math", "itertools", "functools", "typing", "dataclasses", "collections", "enum", "random", "__future__"}
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "input", "breakpoint", "getattr", "setattr",
                   "delattr", "memoryview", "help", "exit", "quit", "bpy", "os", "sys", "subprocess", "socket", "pathlib", "shutil", "importlib"}
FILENAME = "<model>"
DEFAULT_OBJECTS = {"Camera", "Cube", "Light"}


def check_program(code: str) -> list[str]:
    """Why a program must not run, as short messages with the line; empty when it is fine."""
    try:
        tree = ast.parse(code or "", filename=FILENAME)
    except SyntaxError as error:
        return [f"line {error.lineno}: SyntaxError: {error.msg}"]
    problems: list[str] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_MODULES:
                    problems.append(f"line {line}: import {alias.name} is not allowed (only sand3d and math-like modules: {', '.join(sorted(ALLOWED_MODULES - {'__future__'}))})")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                problems.append(f"line {line}: from {'.' * node.level}{node.module or ''} import ... is not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"line {line}: the name {node.id} is not allowed (programs only build geometry with sand3d)")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"line {line}: attribute {node.attr} is not allowed")
    return problems[:8]


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002 - builtin signature
    if level or name.split(".")[0] not in ALLOWED_MODULES:
        raise ImportError(f"import {name} is not allowed in a sand3d program")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _safe_builtins() -> dict:
    allowed = {k: v for k, v in vars(builtins).items() if k not in FORBIDDEN_NAMES}
    allowed["__import__"] = _guarded_import
    return allowed


def _fail(work: Path, kind: str, message: str, **extra) -> int:
    (work / "error.json").write_text(json.dumps({"kind": kind, "message": message[:3000], **extra}, ensure_ascii=False), encoding="utf-8")
    return 2


def _program_error(work: Path, error: BaseException, code: str, stdout: str) -> int:
    line = None
    for frame in traceback.extract_tb(error.__traceback__):
        if frame.filename == FILENAME:
            line = frame.lineno
    lines = code.splitlines()
    snippet = lines[line - 1].strip() if line and line <= len(lines) else None
    return _fail(work, "program", f"{type(error).__name__}: {error}", line=line, code_line=snippet, stdout=stdout[-1500:])


def _find_root(ns: dict, s3d, bpy):
    for name in ("model", "result"):
        if ns.get(name) is not None:
            return ns[name]
    tops = [o for o in bpy.data.objects if o.parent is None and o.name not in DEFAULT_OBJECTS and o.type in ("MESH", "EMPTY", "CURVE")]
    if len(tops) == 1:
        return tops[0]
    if tops:
        return s3d.group("model", *tops)
    return None


def main(argv: list[str]) -> int:
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    work = Path(spec["work"])
    code = spec["code"]
    problems = check_program(code)
    if problems:
        return _fail(work, "rejected", "; ".join(problems))
    captured = io.StringIO()
    real_stdout = sys.stdout
    try:
        import bpy  # noqa: F401  (sand3d needs it; imported here so a missing engine is reported as such)
        import sand3d as s3d
    except Exception as error:  # noqa: BLE001
        return _fail(work, "engine", f"sand3d or bpy cannot be imported: {type(error).__name__}: {error}")
    ns = {"__name__": "__main__", "__builtins__": _safe_builtins(), "s3d": s3d, "sand3d": s3d}
    sys.stdout = captured
    try:
        try:
            exec(compile(code, FILENAME, "exec"), ns)  # noqa: S102 - checked above, reduced builtins, own process, time limit
            root = _find_root(ns, s3d, bpy)
        except BaseException as error:  # noqa: BLE001 - whatever the program does wrong goes back to the author
            sys.stdout = real_stdout
            return _program_error(work, error, code, captured.getvalue())
        sys.stdout = real_stdout  # the exporters log to stdout; only what the program printed is kept
        if root is None:
            return _fail(work, "program", "The program built no object. Assign the final solid to a variable called model.", stdout=captured.getvalue()[-1500:])
        try:
            s3d.export(root, str(work / "out.glb"))
            s3d.export(root, str(work / "out.stl"))
        except BaseException as error:  # noqa: BLE001
            sys.stdout = real_stdout
            return _program_error(work, error, code, captured.getvalue())
    finally:
        sys.stdout = real_stdout
    if not (work / "out.glb").is_file() or not (work / "out.stl").is_file():
        return _fail(work, "program", "sand3d did not write the GLB and STL files.")
    names = [o.name for o in bpy.data.objects if o.name not in DEFAULT_OBJECTS]
    (work / "result.json").write_text(json.dumps({"sand3d": getattr(s3d, "__version__", None), "bpy": bpy.app.version_string, "objects": names[:60],
                                                  "stdout": captured.getvalue()[-1500:], "units": str(s3d.units())}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

# code_blocks.py — парсинг блоков + атомарные правки
import ast
import sys
import json
import shutil
import argparse
import textwrap
from pathlib import Path
from datetime import datetime

# ── мелкие хелперы ────────────────────────────────────────────────────────
def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _calls_of(node: ast.AST) -> list:
    out = set()
    for ch in ast.walk(node):
        if isinstance(ch, ast.Call):
            if isinstance(ch.func, ast.Name):
                out.add(ch.func.id)
            elif isinstance(ch.func, ast.Attribute):
                out.add(ch.func.attr)
    return sorted(out)


def _dec_name(n: ast.AST) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return n.attr
    if isinstance(n, ast.Call):
        return _dec_name(n.func)
    return "?"


def _pre_ann(lines: list, start_ln: int, base_indent: int = 0) -> str:
    found = []
    for i in range(start_ln - 2, max(-2, start_ln - 21), -1):
        if i < 0:
            continue
        line = lines[i].rstrip()
        stripped = line.strip()
        if not stripped:
            if found:
                break
            continue
        if stripped.startswith("@"):
            continue
        line_indent = _indent_of(line)
        if line_indent > base_indent:
            if found:
                break
            continue
        if stripped.startswith("# ANN:") or stripped.startswith("# ann:"):
            return stripped[6:].strip()
        if stripped.startswith("#ANN:") or stripped.startswith("#ann:"):
            return stripped[5:].strip()
        if not stripped.startswith("#"):
            break
        if line_indent < base_indent:
            break
        txt = stripped[1:].strip()
        low = txt.lower()
        if any(low.startswith(x) for x in ("todo", "fixme", "hack", "xxx",
                                            "pylint", "flake8", "noqa",
                                            "type:", "pragma:")):
            continue
        if not txt or txt in {"-", "—", "*", "="}:
            continue
        found.append(txt)
    found.reverse()
    return "\n".join(found)


def _read_source(filepath: Path) -> str:
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1", "cp1251", "iso-8859-1"]
    for enc in encodings:
        try:
            return filepath.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return filepath.read_text(encoding="utf-8", errors="replace")


# ── парсинг блоков ────────────────────────────────────────────────────────
def _parse_blocks(source: str) -> list:
    source = source.replace("\x00", " ")
    lines = source.splitlines()
    try:
        tree = ast.parse(source, type_comments=False)
    except TypeError:
        tree = ast.parse(source)

    def _block(node, qual, btype):
        ln = node.lineno
        indent = _indent_of(lines[ln - 1]) if ln <= len(lines) else 0
        pre = _pre_ann(lines, ln, indent)
        return {
            "name": qual.split(".")[-1], "qual": qual, "type": btype,
            "start": ln, "end": node.end_lineno or ln,
            "lines": (node.end_lineno or ln) - ln + 1,
            "doc": (ast.get_docstring(node) or "").strip(), "pre_ann": pre,
            "args": [a.arg for a in node.args.args] if hasattr(node, "args") else [],
            "decorators": [_dec_name(d) for d in getattr(node, "decorator_list", [])],
            "calls": _calls_of(node),
            "lazy_imports": [],
            "ann": "",
        }

    def walk(node, stack):
        out = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            qual = ".".join(stack + [name])
            if isinstance(node, ast.AsyncFunctionDef):
                btype = "async_function"
            elif isinstance(node, ast.FunctionDef):
                btype = "function"
            else:
                btype = "class"

            block_data = _block(node, qual, btype)

            if btype == "class":
                methods = []
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        m_qual = f"{qual}.{child.name}"
                        m_type = "async_function" if isinstance(child, ast.AsyncFunctionDef) else "method"
                        methods.append(_block(child, m_qual, m_type))
                        for sub in ast.iter_child_nodes(child):
                            methods.extend(walk(sub, stack + [name, child.name]))
                    else:
                        out.extend(walk(child, stack + [name]))
                block_data["methods"] = methods
                out.append(block_data)
                out.extend(methods)
            else:
                out.append(block_data)
                for child in ast.iter_child_nodes(node):
                    out.extend(walk(child, stack + [name]))
        else:
            for child in ast.iter_child_nodes(node):
                out.extend(walk(child, stack))
        return out

    out = []
    for top in ast.iter_child_nodes(tree):
        out.extend(walk(top, []))
    return out


def build_index(root: Path) -> dict:
    idx = {"files": {}, "blocks": {}, "by_name": {}}
    for f in sorted(Path(root).rglob("*.py")):
        if any(s in f.parts for s in {"__pycache__", ".venv", "venv", "node_modules", ".git",
                                       ".embed_cache", "backups", "_scratch", ".mypy_cache", ".pytest_cache"}):
            continue
        rel = str(f.relative_to(root)).replace("\\", "/")
        src = _read_source(f)
        blocks = _parse_blocks(src)
        idx["files"][rel] = {"lines": len(src.splitlines()), "blocks": blocks}
        for b in blocks:
            idx["blocks"][b["qual"]] = {"file": rel, "block": b}
            idx["by_name"].setdefault(b["name"], []).append(rel)
    return idx


# ── чтение / запись блоков ────────────────────────────────────────────────
def _read(path: str) -> str:
    return _read_source(Path(path))


def list_blocks(path: str) -> list:
    src = _read(path)
    blocks = _parse_blocks(src)
    return [{"name": b["name"], "qual": b["qual"], "type": b["type"],
             "start": b["start"], "end": b["end"], "lines": b["lines"]} for b in blocks]


def _find_block(blocks: list, qual: str):
    """Рекурсивно ищет блок по qual в плоском списке + methods."""
    for b in blocks:
        if b["qual"] == qual:
            return b
        if b.get("methods"):
            for m in b["methods"]:
                if m["qual"] == qual:
                    return m
    return None


def get_block(path: str, qual: str) -> dict:
    src = _read(path)
    blocks = _parse_blocks(src)
    b = _find_block(blocks, qual)
    if not b:
        return None
    lines = src.splitlines()
    body = "\n".join(lines[b["start"] - 1:b["end"]])
    return {**b, "code": body}


def _renormalize(code: str, target: int) -> str:
    lines = code.splitlines()
    if not lines:
        return code
    indents = [_indent_of(l) for l in lines if l.strip()]
    if not indents:
        return code
    base = min(indents)
    out = []
    for l in lines:
        if l.strip():
            out.append(" " * target + l[base:])
        else:
            out.append("")
    return "\n".join(out)


def _collapse_blanks(lines: list) -> list:
    out = []
    prev_blank = False
    for l in lines:
        if not l.strip():
            if not prev_blank:
                out.append("")
                prev_blank = True
        else:
            out.append(l)
            prev_blank = False
    return out


def _backup(path: Path):
    bak_dir = path.parent / "backups"
    bak_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy2(path, bak_dir / f"{path.stem}_{ts}{path.suffix}")


def _write_safe(path: Path, new_lines: list, src: str):
    new_src = "\n".join(new_lines)
    try:
        ast.parse(new_src)
    except SyntaxError as e:
        raise ValueError(f"AST invalid: {e}")
    _backup(path)
    path.write_text(new_src, encoding="utf-8")


def apply_replace(path: str, qual: str, new_code: str) -> dict:
    src = _read(path)
    blocks = _parse_blocks(src)
    b = _find_block(blocks, qual)
    if not b:
        raise ValueError(f"Block {qual} not found")
    lines = src.splitlines()
    new_lines = lines[:b["start"] - 1] + _renormalize(new_code, _indent_of(lines[b["start"] - 1])).splitlines() + lines[b["end"]:]
    _write_safe(Path(path), new_lines, src)
    return {"replaced": qual, "lines": len(new_lines)}


def apply_insert(path: str, after_qual: str, new_code: str) -> dict:
    src = _read(path)
    blocks = _parse_blocks(src)
    b = _find_block(blocks, after_qual)
    if not b:
        raise ValueError(f"Block {after_qual} not found")
    lines = src.splitlines()
    insert_at = b["end"]
    new_lines = lines[:insert_at] + [""] + _renormalize(new_code, _indent_of(lines[b["start"] - 1])).splitlines() + lines[insert_at:]
    _write_safe(Path(path), new_lines, src)
    return {"inserted_after": after_qual, "lines": len(new_lines)}


def apply_delete(path: str, qual: str) -> dict:
    src = _read(path)
    blocks = _parse_blocks(src)
    b = _find_block(blocks, qual)
    if not b:
        raise ValueError(f"Block {qual} not found")
    lines = src.splitlines()
    new_lines = lines[:b["start"] - 1] + lines[b["end"]:]
    _write_safe(Path(path), new_lines, src)
    return {"deleted": qual, "lines": len(new_lines)}


def validate_code(code: str) -> dict:
    try:
        ast.parse(textwrap.dedent(code))
        return {"valid": True}
    except SyntaxError as e:
        return {"valid": False, "error": str(e)}


def list_backups(path: str) -> list:
    p = Path(path)
    bak_dir = p.parent / "backups"
    if not bak_dir.exists():
        return []
    out = []
    for f in sorted(bak_dir.glob(f"{p.stem}_*{p.suffix}")):
        st = f.stat()
        out.append({"name": f.name, "size": st.st_size, "mtime": st.st_mtime})
    return out


def restore(path: str, backup_name: str):
    p = Path(path)
    bak = p.parent / "backups" / backup_name
    if not bak.exists():
        raise FileNotFoundError(f"Backup {backup_name} not found")
    _backup(p)
    shutil.copy2(bak, p)


# ── CLI ──────────────────────────────────────────────────────────────────
def _read_code_arg(args) -> str:
    if getattr(args, "code", None):
        return args.code
    if getattr(args, "code_file", None):
        return Path(args.code_file).read_text(encoding="utf-8")
    return sys.stdin.read()


def main():
    p = argparse.ArgumentParser(prog="code_blocks")
    sub = p.add_subparsers(dest="cmd")

    sp = sub.add_parser("list")
    sp.add_argument("file")

    sp = sub.add_parser("get")
    sp.add_argument("file")
    sp.add_argument("qual")

    sp = sub.add_parser("replace")
    sp.add_argument("file")
    sp.add_argument("qual")
    sp.add_argument("--code")
    sp.add_argument("--file", dest="code_file")

    sp = sub.add_parser("insert")
    sp.add_argument("file")
    sp.add_argument("after_qual")
    sp.add_argument("--code")
    sp.add_argument("--file", dest="code_file")

    sp = sub.add_parser("delete")
    sp.add_argument("file")
    sp.add_argument("qual")

    sp = sub.add_parser("validate")
    sp.add_argument("--code")
    sp.add_argument("--file", dest="code_file")

    sp = sub.add_parser("backups")
    sp.add_argument("file")

    sp = sub.add_parser("restore")
    sp.add_argument("file")
    sp.add_argument("backup")

    a = p.parse_args()

    if a.cmd == "list":
        for b in list_blocks(a.file):
            print(f"{b['type']:12s} {b['qual']:40s} [{b['start']}–{b['end']}]")
    elif a.cmd == "get":
        print(json.dumps(get_block(a.file, a.qual), ensure_ascii=False, indent=2))
    elif a.cmd == "replace":
        print(json.dumps(apply_replace(a.file, a.qual, _read_code_arg(a)), ensure_ascii=False))
    elif a.cmd == "insert":
        print(json.dumps(apply_insert(a.file, a.after_qual, _read_code_arg(a)), ensure_ascii=False))
    elif a.cmd == "delete":
        print(json.dumps(apply_delete(a.file, a.qual), ensure_ascii=False))
    elif a.cmd == "validate":
        print(json.dumps(validate_code(_read_code_arg(a)), ensure_ascii=False))
    elif a.cmd == "backups":
        for b in list_backups(a.file):
            print(b["name"])
    elif a.cmd == "restore":
        restore(a.file, a.backup)
        print("restored")
    else:
        p.print_help()


if __name__ == "__main__":
    main()

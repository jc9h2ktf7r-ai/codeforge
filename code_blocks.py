# code_blocks.py — "лего для кода": правка Python-файлов целыми блоками (AST-safe)
# Блок = функция / метод / класс с точно известными границами.
# Инвариант: отступы выравниваются под место вставки, синтаксис проверяется
# на ВСЁМ файле ДО записи. Не сошлось — файл не тронут, бэкап сохранён.
#
# CLI:
#   python code_blocks.py list   walker_auto.py
#   python code_blocks.py get    walker_auto.py run_walker
#   python code_blocks.py replace walker_auto.py run_walker --from new.py
#   python code_blocks.py insert  walker_auto.py run_walker --from new.py
#   python code_blocks.py delete  walker_auto.py _old_helper
#   python code_blocks.py validate --from snippet.py
#   python code_blocks.py backups walker_auto.py
#   python code_blocks.py restore walker_auto.py
#   python code_blocks.py index  D:\synochek\synochek_v2\volk
import ast, sys, json, shutil, argparse, textwrap
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
BACKUP_DIR = SCRIPT_DIR / "backups"


# ── парсинг блоков ────────────────────────────────────────────────────────
def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _calls_of(node: ast.AST) -> list[str]:
    out = set()
    for ch in ast.walk(node):
        if isinstance(ch, ast.Call):
            if isinstance(ch.func, ast.Name):
                out.add(ch.func.id)
            elif isinstance(ch.func, ast.Attribute):
                out.add(ch.func.attr)
    return sorted(out)


def _dec_name(n: ast.AST) -> str:
    if isinstance(n, ast.Name): return n.id
    if isinstance(n, ast.Attribute): return n.attr
    if isinstance(n, ast.Call): return _dec_name(n.func)
    return "?"

def _pre_ann(lines: list[str], start_ln: int, base_indent: int) -> str:
    """Все комментарии подряд над блоком (ближайшая группа). # ANN: — приоритет."""
    found = []
    # ИСПРАВЛЕНО: range до -1 включительно, чтобы захватить строку 0
    for i in range(start_ln - 2, max(-2, start_ln - 21), -1):
        if i < 0:
            continue  # ← не break, иначе пропускаем строку 0
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
        # явная # ANN: — берём только её
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

def _parse_blocks(source: str) -> list[dict]:
    """Список блоков файла: функции, классы, методы — с границами и отступом."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines = source.split("\n")
    blocks = []

    def _common(node, qual, btype):
        ln = node.lineno
        indent = _indent_of(lines[ln - 1]) if ln <= len(lines) else 0
        return {
            "qual": qual,
            "name": qual.split(".")[-1],
            "type": btype,
            "start": ln,
            "end": node.end_lineno or ln,
            "lines": (node.end_lineno or ln) - ln + 1,
            "indent": indent,
            "doc": (ast.get_docstring(node) or "").strip(),
            "pre_ann": _pre_ann(lines, ln, indent),
            "args": [a.arg for a in node.args.args] if hasattr(node, "args") else [],
            "decorators": [_dec_name(d) for d in getattr(node, "decorator_list", [])],
            "calls_raw": _calls_of(node),
        }

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            b = _common(node, node.name,
                        "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function")
            blocks.append(b)
        elif isinstance(node, ast.ClassDef):
            cb = _common(node, node.name, "class")
            methods = []
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mb = _common(ch, f"{node.name}.{ch.name}", "method")
                    methods.append(mb)
                    blocks.append(mb)
            cb["methods"] = [m["qual"] for m in methods]
            blocks.append(cb)
    return blocks


def _simple(qual: str) -> str:
    return qual.split(".")[-1]


# ── индекс проекта (кросс-файловые связи) ─────────────────────────────────
_SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git",
              "backups", "_scratch", ".embed_cache"}


def build_index(root: Path) -> dict:
    root = Path(root)
    files_data, name_map, caller_map = [], {}, {}
    parsed = []
    for f in sorted(root.rglob("*.py")):
        if any(s in f.parts for s in _SKIP_DIRS):
            continue
        try:
            src = f.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        blocks = _parse_blocks(src)
        rel = str(f.relative_to(root))
        parsed.append((rel, blocks))
        for b in blocks:
            name_map.setdefault(b["name"], []).append((rel, b["qual"]))
            for r in b["calls_raw"]:
                caller_map.setdefault(r, []).append((rel, b["qual"]))

    files_out, server_map = [], {}
    for rel, blocks in parsed:
        out_blocks = []
        for b in blocks:
            seen_c, seen_b = set(), set()
            calls_internal = []
            for r in b["calls_raw"]:
                for fr, fq in name_map.get(r, []):
                    k = (fr, fq)
                    if k in seen_c or (fr == rel and fq == b["qual"]):
                        continue
                    seen_c.add(k); calls_internal.append({"file": fr, "qual": fq})
            called_by = []
            for fr, fq in caller_map.get(b["name"], []):
                k = (fr, fq)
                if k in seen_b or (fr == rel and fq == b["qual"]):
                    continue
                seen_b.add(k); called_by.append({"file": fr, "qual": fq})
            server_map[(rel, b["qual"])] = {**b, "calls_internal": calls_internal, "called_by": called_by}
            out_blocks.append({
                "qual": b["qual"], "type": b["type"], "lines": b["lines"],
                "indent": b["indent"], "doc": b["doc"],
                "n_calls": len(calls_internal), "n_called": len(called_by),
            })
        total_lines = max((b["end"] for b in blocks), default=0)
        files_out.append({"path": rel, "lines": total_lines, "blocks": out_blocks})
        files_data.append((rel, total_lines))

    return {
        "root": str(root),
        "files": files_out,
        "stats": {
            "files": len(files_out),
            "blocks": sum(len(fd["blocks"]) for fd in files_out),
        },
        "_map": server_map,  # серверное, клиенту не отдаём
    }


# ── чтение / запись блоков ────────────────────────────────────────────────
def _read(path: Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def list_blocks(path: Path) -> dict:
    src = _read(path)
    return {"path": str(path), "lines": len(src.split("\n")),
            "blocks": [{k: b[k] for k in
                        ("qual", "type", "start", "end", "lines", "indent", "doc", "args", "decorators")}
                       for b in _parse_blocks(src)]}


def get_block(path: Path, qual: str) -> dict:
    src = _read(path)
    L = src.split("\n")
    b = next((x for x in _parse_blocks(src) if x["qual"] == qual), None)
    if not b:
        return {"error": f"блок '{qual}' не найден"}
    code = "\n".join(L[b["start"] - 1:b["end"]])
    return {**b, "code": code}


def _renormalize(code: str, target: int) -> list[str]:
    """Выравнивает вставленный код под целевой отступ (боль вайбкодера → решена)."""
    raw = code.splitlines()
    while raw and not raw[0].strip():
        raw.pop(0)
    while raw and not raw[-1].strip():
        raw.pop()
    if not raw:
        return []
    nonblank = [l for l in raw if l.strip()]
    if not nonblank:
        return []
    min_ind = min(_indent_of(l) for l in nonblank)
    shift = target - min_ind
    out = []
    for l in raw:
        if not l.strip():
            out.append("")
        else:
            out.append(" " * max(0, _indent_of(l) + shift) + l.lstrip())
    return out


def _collapse_blanks(lines: list[str]) -> list[str]:
    out, run = [], 0
    for l in lines:
        if l.strip() == "":
            run += 1
            if run <= 2:
                out.append("")
        else:
            run = 0
            out.append(l)
    return out


def _backup(path: Path) -> str:
    BACKUP_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:20]
    dst = BACKUP_DIR / f"{Path(path).name}.{ts}.bak"
    shutil.copy2(path, dst)
    return str(dst)


def _write_safe(path: Path, new_lines: list[str], src: str) -> dict:
    """Собирает файл, проверяет AST целиком, бэкапит, пишет. Атомарно."""
    result = _collapse_blanks(new_lines)
    text = "\n".join(result)
    if src.endswith("\n") and not text.endswith("\n"):
        text += "\n"
    try:
        ast.parse(text)
    except SyntaxError as e:
        return {"ok": False, "error": f"SyntaxError: {e} (файл НЕ изменён)"}
    backup = _backup(path)
    Path(path).write_text(text, encoding="utf-8")
    return {"ok": True, "backup": backup, "new_lines": len(result)}


def apply_replace(path: Path, qual: str, new_code: str) -> dict:
    src = _read(path)
    L = src.split("\n")
    b = next((x for x in _parse_blocks(src) if x["qual"] == qual), None)
    if not b:
        return {"ok": False, "error": f"блок '{qual}' не найден"}
    nl = _renormalize(new_code, b["indent"])
    if not nl:
        return {"ok": False, "error": "пустой код замены"}
    res = _write_safe(path, L[:b["start"] - 1] + nl + L[b["end"]:], src)
    if res.get("ok"):
        res["old_lines"] = b["lines"]
    return res


def apply_insert(path: Path, after_qual: str, new_code: str) -> dict:
    src = _read(path)
    L = src.split("\n")
    a = next((x for x in _parse_blocks(src) if x["qual"] == after_qual), None)
    if not a:
        return {"ok": False, "error": f"якорь '{after_qual}' не найден"}
    nl = _renormalize(new_code, a["indent"])
    if not nl:
        return {"ok": False, "error": "пустой код вставки"}
    sep = 2 if a["indent"] == 0 else 1
    # ИСПРАВЛЕНО: убрана trailing пустая строка
    ins = [""] * sep + nl
    return _write_safe(path, L[:a["end"]] + ins + L[a["end"]:], src)


def apply_delete(path: Path, qual: str) -> dict:
    src = _read(path)
    L = src.split("\n")
    b = next((x for x in _parse_blocks(src) if x["qual"] == qual), None)
    if not b:
        return {"ok": False, "error": f"блок '{qual}' не найден"}
    return _write_safe(path, L[:b["start"] - 1] + L[b["end"]:], src)


def validate_code(code: str) -> dict:
    try:
        ast.parse(code)
        return {"ok": True}
    except IndentationError:
        try:
            ast.parse(textwrap.dedent(code))
            return {"ok": True, "note": "отступы будут выровнены при применении"}
        except SyntaxError as e:
            return {"ok": False, "error": str(e)}
    except SyntaxError as e:
        return {"ok": False, "error": str(e)}


def list_backups(path: Path) -> list[dict]:
    if not BACKUP_DIR.exists():
        return []
    pref = f"{Path(path).name}."
    items = []
    for f in sorted(BACKUP_DIR.glob(pref + "*.bak"), key=lambda p: p.stat().st_mtime, reverse=True):
        items.append({"name": f.name, "size": f.stat().st_size,
                      "ts": f.stat().st_mtime})
    return items


def restore(path: Path, backup_name: str = None) -> dict:
    bks = list_backups(path)
    if not bks:
        return {"ok": False, "error": "бэкапов нет"}
    if backup_name:
        src_bk = BACKUP_DIR / backup_name
        if not src_bk.exists():
            return {"ok": False, "error": "бэкап не найден"}
    else:
        src_bk = BACKUP_DIR / bks[0]["name"]
    _backup(path)  # бэкапим текущее состояние перед откатом
    shutil.copy2(src_bk, path)
    return {"ok": True, "restored": src_bk.name}


# ── CLI ──────────────────────────────────────────────────────────────────
def _read_code_arg(args) -> str:
    if getattr(args, "src", None):
        return Path(args.src).read_text(encoding="utf-8")
    if getattr(args, "code", None):
        return args.code
    return sys.stdin.read()


def main():
    p = argparse.ArgumentParser(prog="code_blocks")
    sp = p.add_subparsers(dest="cmd", required=True)

    s = sp.add_parser("list"); s.add_argument("file")
    s.add_argument("--json", action="store_true")

    s = sp.add_parser("get"); s.add_argument("file"); s.add_argument("qual")

    s = sp.add_parser("replace"); s.add_argument("file"); s.add_argument("qual")
    s.add_argument("--from", dest="src"); s.add_argument("--code")

    s = sp.add_parser("insert"); s.add_argument("file"); s.add_argument("after")
    s.add_argument("--from", dest="src"); s.add_argument("--code")

    s = sp.add_parser("delete"); s.add_argument("file"); s.add_argument("qual")

    s = sp.add_parser("validate"); s.add_argument("--from", dest="src"); s.add_argument("--code")

    s = sp.add_parser("backups"); s.add_argument("file")
    s = sp.add_parser("restore"); s.add_argument("file"); s.add_argument("backup", nargs="?")
    s = sp.add_parser("index"); s.add_argument("root"); s.add_argument("--json", action="store_true")

    a = p.parse_args()

    if a.cmd == "list":
        d = list_blocks(a.file)
        if a.json:
            print(json.dumps(d, ensure_ascii=False, indent=2)); return
        print(f"{d['path']}  ({d['lines']} строк, {len(d['blocks'])} блоков)")
        for b in d["blocks"]:
            mark = {"class": "⌘", "method": "·", "async_function": "⚡"}.get(b["type"], "ƒ")
            print(f"  {mark} {b['qual']:42s} [{b['start']}–{b['end']}, {b['lines']} строк]")

    elif a.cmd == "get":
        d = get_block(a.file, a.qual)
        if "error" in d: print("❌", d["error"]); sys.exit(1)
        print(d["code"])

    elif a.cmd == "replace":
        r = apply_replace(a.file, a.qual, _read_code_arg(a))
        print("✅" if r["ok"] else "❌", r)

    elif a.cmd == "insert":
        r = apply_insert(a.file, a.after, _read_code_arg(a))
        print("✅" if r["ok"] else "❌", r)

    elif a.cmd == "delete":
        r = apply_delete(a.file, a.qual)
        print("✅" if r["ok"] else "❌", r)

    elif a.cmd == "validate":
        print(validate_code(_read_code_arg(a)))

    elif a.cmd == "backups":
        for b in list_backups(a.file):
            print(f"  {b['name']}  ({b['size']} B)")

    elif a.cmd == "restore":
        print(restore(a.file, a.backup))

    elif a.cmd == "index":
        idx = build_index(a.root)
        if a.json:
            idx2 = {k: v for k, v in idx.items() if k != "_map"}
            print(json.dumps(idx2, ensure_ascii=False, indent=2)); return
        print(f"root={idx['root']}  файлов={idx['stats']['files']}  блоков={idx['stats']['blocks']}")


if __name__ == "__main__":
    main()
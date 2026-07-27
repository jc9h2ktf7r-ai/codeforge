# code_map.py — «я знаю свой проект»: AST → дерево блоков + граф связей
# Один парсер, два режима: build_project_map (один корень, для UI)
# и build_workspace_map (несколько корней, внешние/кросс-проектные связи).
# Ноль внешних зависимостей. CLI: python code_map.py [list|workspace] <пути...> [--json]
import ast
import sys
import json
import argparse
from pathlib import Path
from collections import defaultdict, Counter

_SKIP_DIRS = {"__pycache__", ".venv", "venv", "node_modules", ".git",
              ".embed_cache", "backups", "_scratch", ".mypy_cache", ".pytest_cache"}

# Имена, которые НЕ рисуем как рёбра вызовов (шум: встроенные/методы-обёртки)
_SKIP_CALL = {
    "get", "set", "add", "run", "close", "reset", "flush", "write", "read",
    "parse", "format", "update", "clear", "copy", "sort", "split", "join",
    "strip", "lower", "upper", "replace", "find", "append", "extend", "insert",
    "remove", "pop", "items", "keys", "values", "group", "match", "findall",
    "finditer", "connect", "execute", "fetchall", "fetchone", "commit", "open",
    "exists", "loads", "dumps", "encode", "decode", "post", "put", "delete",
    "patch", "head", "raise_for_status", "startswith", "endswith", "isdigit",
    "isalpha", "isupper", "search", "sub", "compile", "len", "str", "int",
    "float", "bool", "list", "dict", "set", "tuple", "range", "enumerate",
    "print", "min", "max", "sum", "any", "all", "sorted", "isinstance",
    "hasattr", "getattr", "setattr", "type", "super", "round", "abs",
}


# ── мелкие хелперы ────────────────────────────────────────────────────────
def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _doc(node: ast.AST) -> str:
    return (ast.get_docstring(node) or "").strip()


def _decorator_name(n: ast.AST) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return n.attr
    if isinstance(n, ast.Call):
        return _decorator_name(n.func)
    return "?"


def _arg_name(n: ast.arg) -> str:
    return n.arg


def _base_name(n: ast.AST) -> str:
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        return n.attr
    return "?"


def _calls_of(node: ast.AST) -> list:
    out = set()
    for ch in ast.walk(node):
        if isinstance(ch, ast.Call):
            if isinstance(ch.func, ast.Name):
                out.add(ch.func.id)
            elif isinstance(ch.func, ast.Attribute):
                out.add(ch.func.attr)
    return sorted(out)


def _lazy_of(node: ast.AST) -> list:
    out = []
    for ch in ast.walk(node):
        if isinstance(ch, ast.ImportFrom):
            out.append({"module": ch.module or "", "names": [a.name for a in ch.names],
                        "line": ch.lineno})
        elif isinstance(ch, ast.Import):
            for a in ch.names:
                out.append({"module": a.name, "names": [], "line": ch.lineno})
    return out


# ── парсинг одного файла ──────────────────────────────────────────────────
def parse_file(filepath: Path) -> dict:
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return {"path": filepath.name, "full_path": str(filepath), "lines": 0,
                "error": f"read: {e}", "blocks": [], "imports": [],
                "from_imports": [], "lazy_imports": [], "all_calls": []}
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return {"path": filepath.name, "full_path": str(filepath),
                "lines": len(source.splitlines()), "error": f"SyntaxError: {e}",
                "blocks": [], "imports": [], "from_imports": [],
                "lazy_imports": [], "all_calls": []}

    lines = source.splitlines()
    blocks, top_from, top_imp = [], [], []

    def _pre_ann(start_ln: int, base_indent: int = 0) -> str:
        """Все комментарии подряд над блоком (ближайшая группа). # ANN: — приоритет."""
        found = []
        # ИСПРАВЛЕНО: range до -1 включительно, continue вместо break на i<0
        for i in range(start_ln - 2, max(-2, start_ln - 21), -1):
            if i < 0:
                continue
            line = lines[i].rstrip()
            stripped = line.strip()
            if not stripped:
                if found:
                    break                      # группа отделена пустой строкой — стоп
                continue
            if stripped.startswith("@"):
                continue                       # декоратор принадлежит блоку — идём выше
            line_indent = _indent_of(line)
            if line_indent > base_indent:
                if found:
                    break                      # вложенный код после коммента — стоп
                continue
            # явная # ANN: — берём только её
            if stripped.startswith("# ANN:") or stripped.startswith("# ann:"):
                return stripped[6:].strip()
            if stripped.startswith("#ANN:") or stripped.startswith("#ann:"):
                return stripped[5:].strip()
            if not stripped.startswith("#"):
                break                          # код — граница
            if line_indent < base_indent:
                break                          # коммент внешнего уровня — не про этот блок
            txt = stripped[1:].strip()
            low = txt.lower()
            if any(low.startswith(x) for x in ("todo", "fixme", "hack", "xxx",
                                                "pylint", "flake8", "noqa",
                                                "type:", "pragma:")):
                continue                       # служебный маркер — пропускаем
            if not txt or txt in {"-", "—", "*", "="}:
                continue                       # чистый разделитель — пропускаем
            found.append(txt)
        found.reverse()
        return "\n".join(found)

    def _block(node, qual, btype):
        ln = node.lineno
        indent = _indent_of(lines[ln - 1]) if ln <= len(lines) else 0
        pre = _pre_ann(ln, indent)
        return {
            "name": qual.split(".")[-1], "qual": qual, "type": btype,
            "start": ln, "end": node.end_lineno or ln,
            "lines": (node.end_lineno or ln) - ln + 1,
            "doc": _doc(node), "pre_ann": pre,
            "args": [_arg_name(a) for a in node.args.args] if hasattr(node, "args") else [],
            "decorators": [_decorator_name(d) for d in getattr(node, "decorator_list", [])],
            "calls": _calls_of(node),
            "lazy_imports": _lazy_of(node),
        }

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            blocks.append(_block(node, node.name,
                                 "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"))
        elif isinstance(node, ast.ClassDef):
            methods = [_block(ch, f"{node.name}.{ch.name}", "method")
                       for ch in ast.iter_child_nodes(node)
                       if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef))]
            cb = _block(node, node.name, "class")
            cb["methods"] = methods
            cb["bases"] = [_base_name(b) for b in node.bases]
            blocks.append(cb)
        elif isinstance(node, ast.ImportFrom):
            top_from.append({"module": node.module or "", "names": [a.name for a in node.names]})
        elif isinstance(node, ast.Import):
            top_imp += [a.name for a in node.names]

    # lazy с пометкой, в каком блоке встретились
    lazy = []
    for b in blocks:
        for li in b.get("lazy_imports", []):
            lazy.append({**li, "in_block": b["name"]})
        if b["type"] == "class":
            for m in b.get("methods", []):
                for li in m.get("lazy_imports", []):
                    lazy.append({**li, "in_block": m["name"]})

    all_calls = set()
    for b in blocks:
        all_calls.update(b.get("calls", []))
        if b["type"] == "class":
            for m in b.get("methods", []):
                all_calls.update(m.get("calls", []))

    return {
        "path": filepath.name, "full_path": str(filepath),
        "lines": len(lines), "blocks": blocks,
        "imports": top_imp, "from_imports": top_from,
        "lazy_imports": lazy, "all_calls": sorted(all_calls),
    }


# ── разрешение имени модуля в файл ────────────────────────────────────────
def _module_candidates(module: str) -> list:
    if not module:
        return []
    parts = module.split(".")
    cands = ["/".join(parts) + ".py"]
    for i in range(1, len(parts)):
        cands.append("/".join(parts[i:]) + ".py")
    cands.append(parts[-1] + ".py")
    # ИСПРАВЛЕНО: добавляем __init__.py для пакетов
    cands.append("/".join(parts) + "/__init__.py")
    seen, out = set(), []
    for c in cands:
        c = c.replace("\\", "/")
        if c not in seen:
            seen.add(c); out.append(c)
    return out


def _resolve(module: str, idx: dict, home_root: str):
    """Возвращает (rootname, rel) или None. home_root — приоритет своему корню."""
    for cand in _module_candidates(module):
        if cand in idx["rel"]:
            return idx["rel"][cand]
    for cand in _module_candidates(module):
        base = cand.split("/")[-1]
        hits = idx["base"].get(base, [])
        if hits:
            own = [h for h in hits if h[0] == home_root]
            return (own or hits)[0]
    return None


def _key(rootname: str, rel: str) -> str:
    return f"{rootname}/{rel}" if rootname else rel


def _collect(roots: list):
    """Собирает распарсенные файлы из корней + индексы разрешения."""
    idx = {"rel": {}, "base": {}}
    parsed = []
    for root in roots:
        rname = root.name
        for f in sorted(root.rglob("*.py")):
            if any(s in f.parts for s in _SKIP_DIRS):
                continue
            rel = str(f.relative_to(root)).replace("\\", "/")
            data = parse_file(f)
            data["path"] = _key(rname, rel)         # ключ узла = путь относительно корня
            data["_root"] = rname
            data["_rel"] = rel
            idx["rel"][rel] = (rname, rel)
            idx["base"].setdefault(f.name, []).append((rname, rel))
            parsed.append(data)
    return parsed, idx


# ИСПРАВЛЕНО: функция проверки skip с учётом определённых в проекте имён
def _should_skip_call(name: str, defined_names: set) -> bool:
    """Скипаем только если имя НЕ определено в проекте и оно в списке шума."""
    if name in defined_names:
        return False
    return name in _SKIP_CALL


def _build_graph(parsed, idx, with_cross: bool):
    """Строит узлы/рёбра/индексы имён. with_cross=True различает корни."""
    nodes, edges, seen_e = [], [], set()
    name_index = defaultdict(list)        # simple_name -> [(root,rel,qual)]
    blocks_by_key = {}

    def add_edge(fr, to, etype, **extra):
        k = (fr, to, etype)
        if k in seen_e or fr == to:
            return
        seen_e.add(k)
        edges.append({"from": fr, "to": to, "type": etype, **extra})

    # индекс имён + узлы
    for f in parsed:
        rname, rel = f["_root"], f["_rel"]
        key = f["path"]
        nodes.append({"id": key, "root": rname, "rel": rel, "lines": f.get("lines", 0)})
        for b in f.get("blocks", []):
            name_index[b["name"]].append((rname, rel, b["name"]))
            blocks_by_key[(key, b["name"])] = b
            if b["type"] == "class":
                for m in b.get("methods", []):
                    name_index[m["name"]].append((rname, rel, m["name"]))
                    blocks_by_key[(key, m["name"])] = m

    # ИСПРАВЛЕНО: собираем множество всех определённых имён
    defined_names = set(name_index.keys())

    # рёбра импортов
    for f in parsed:
        key, rname = f["path"], f["_root"]
        ext = []
        def handle(module, etype, in_block=None):
            tgt = _resolve(module, idx, rname)
            if tgt is None:
                if module:
                    ext.append({"module": module, "in": in_block})
                return
            tkey = _key(*tgt)
            real = "cross_root" if (with_cross and tgt[0] != rname) else etype
            add_edge(key, tkey, real, module=module, **({"in": in_block} if in_block else {}))
        for imp in f.get("from_imports", []):
            handle(imp["module"], "import")
        for imp in f.get("imports", []):
            handle(imp, "import")
        for li in f.get("lazy_imports", []):
            handle(li.get("module", ""), "lazy_import", in_block=li.get("in_block"))
        f["external_imports"] = ext

        # рёбра вызовов (только по определённым в проекте именам)
        def add_calls(block):
            for c in block.get("calls", []):
                # ИСПРАВЛЕНО: используем _should_skip_call
                if _should_skip_call(c, defined_names):
                    continue
                for tr, rel2, qual2 in name_index.get(c, []):
                    tkey = _key(tr, rel2)
                    if tkey == key:
                        continue
                    etype = "cross_root" if (with_cross and tr != rname) else "calls"
                    add_edge(key, tkey, etype, name=c)
        for b in f.get("blocks", []):
            add_calls(b)
            if b["type"] == "class":
                for m in b.get("methods", []):
                    add_calls(m)

    return nodes, edges, name_index, blocks_by_key


def _stats(parsed, edges, name_index):
    defined = set(name_index.keys())
    freq = Counter()
    for f in parsed:
        for c in f.get("all_calls", []):
            # ИСПРАВЛЕНО: не считаем skip-вызовы в статистике
            if _should_skip_call(c, defined):
                continue
            freq[c] += 1
    by_type = Counter(e["type"] for e in edges)
    return {
        "files": len(parsed),
        "total_lines": sum(f.get("lines", 0) for f in parsed),
        "blocks": sum(len(f.get("blocks", [])) for f in parsed),
        "methods": sum(len(b.get("methods", [])) for f in parsed for b in f.get("blocks", [])),
        "top_calls": freq.most_common(20),
        "by_type": dict(by_type),
        "cross_root": by_type.get("cross_root", 0),
    }


def _clean_for_client(parsed):
    out = []
    for f in parsed:
        out.append({k: f[k] for k in
                    ("path", "lines", "blocks", "imports", "from_imports",
                     "lazy_imports", "external_imports") if k in f})
    return out


# ── публичные сборки ──────────────────────────────────────────────────────
def build_project_map(project_dir: Path) -> dict:
    """Один корень — формат для UI (дерево + граф + редактор)."""
    root = Path(project_dir)
    try:
        parsed, idx = _collect([root])
    except Exception as e:
        return {"project": str(root), "files": [], "graph": {"nodes": [], "edges": []},
                "stats": {"files": 0, "total_lines": 0, "blocks": 0, "methods": 0,
                          "top_calls": [], "by_type": {}, "cross_root": 0},
                "error": f"scan: {e}"}
    nodes, edges, name_index, _ = _build_graph(parsed, idx, with_cross=False)
    return {
        "project": str(root),
        "files": _clean_for_client(parsed),
        "graph": {"nodes": [{"id": n["id"], "lines": n["lines"]} for n in nodes], "edges": edges},
        "stats": _stats(parsed, edges, name_index),
    }


def build_workspace_map(roots, include_external_nodes: bool = False) -> dict:
    """Несколько корней — видит мосты между проектами и внешнюю границу."""
    roots = [Path(r) for r in roots]
    parsed, idx = _collect(roots)
    nodes, edges, name_index, _ = _build_graph(parsed, idx, with_cross=True)
    if include_external_nodes:
        ext_cnt = Counter()
        for f in parsed:
            for x in f.get("external_imports", []):
                ext_cnt[x["module"].split(".")[0]] += 1
        for mod, n in ext_cnt.items():
            nodes.append({"id": f"ext:{mod}", "root": "external", "rel": mod,
                          "lines": 0, "phantom": True, "used": n})
    cross = [e for e in edges if e["type"] == "cross_root"]
    st = _stats(parsed, edges, name_index)
    st["external_modules"] = sum(1 for f in parsed for _ in f.get("external_imports", []))
    return {
        "roots": [str(r) for r in roots],
        "files": _clean_for_client(parsed),
        "graph": {"nodes": [{"id": n["id"], "lines": n["lines"]} for n in nodes], "edges": edges},
        "stats": st,
        "cross_root_edges": cross,
    }


# ── печать (терминал) ─────────────────────────────────────────────────────
def _sz(lines):
    return "🔴" if lines > 1000 else "🟡" if lines > 300 else "🟢"


def print_map(data):
    st = data["stats"]
    print(f"\n{'═' * 70}\n  КАРТА: {data['project']}\n"
          f"  Файлов {st['files']} · строк {st['total_lines']:,} · "
          f"блоков {st['blocks']} · методов {st['methods']}\n{'═' * 70}")
    for f in data["files"]:
        print(f"\n  {_sz(f['lines'])} {f['path']}  ({f['lines']} строк, {len(f['blocks'])} блоков)")
        for b in f["blocks"]:
            g = {"class": "⌘", "method": "·", "async_function": "⚡"}.get(b["type"], "ƒ")
            print(f"      {g} {b['name']:40s} [{b['start']}–{b['end']}, {b['lines']} строк]")
            for m in b.get("methods", []):
                print(f"        · {m['name']:38s} [{m['start']}–{m['end']}, {m['lines']} строк]")
    edges = data["graph"]["edges"]
    if edges:
        print(f"\n── СВЯЗИ ({len(edges)}) ──")
        for e in edges[:80]:
            extra = e.get("module") or e.get("name") or ""
            print(f"   {e['from']}  ─{e['type']}─►  {e['to']}  {extra}")
    if st.get("top_calls"):
        print("\n── ТОП ВЫЗОВОВ ПРОЕКТА ──")
        for name, c in st["top_calls"]:
            print(f"   {name:32s} {c:3d}  {'█' * min(c, 40)}")
    print()


def print_workspace(data):
    st = data["stats"]
    print(f"\n{'═' * 72}\n  РАБОЧАЯ ОБЛАСТЬ: {len(data['roots'])} корней")
    for r in data["roots"]:
        print(f"    • {r}")
    print(f"  Файлов {st['files']} · узлов {len(data['graph']['nodes'])} · "
          f"рёбер {st['by_type']}\n{'═' * 72}")
    cross = data["cross_root_edges"]
    print(f"\n── КРОСС-ПРОЕКТНЫЕ МОСТЫ: {len(cross)} ──")
    for e in cross:
        extra = e.get("module") or e.get("name") or ""
        print(f"   {e['from']}  ───✦───►  {e['to']}  [{extra}]")
    # детектор цикла между проектами
    pair = Counter()
    for e in cross:
        a, b = e["from"].split("/")[0], e["to"].split("/")[0]
        pair[(a, b)] += 1
    cycles = [(a, b, pair[(a, b)], pair[(b, a)]) for (a, b), n in pair.items()
              if (b, a) in pair and a < b]
    if cycles:
        print("\n── ⚠️  ЦИКЛ МЕЖДУ ПРОЕКТАМИ ──")
        for a, b, ab, ba in cycles:
            print(f"   {a} ──({ab})──► {b}  и  {b} ──({ba})──► {a}")
            print("   → две кодовые базы зависят друг от друга: правка одной ломает другую.")
    ext = Counter()
    for f in data["files"]:
        for x in f.get("external_imports", []):
            ext[x["module"].split(".")[0]] += 1
    if ext:
        print("\n── ВНЕШНЯЯ ГРАНИЦА (не в области) ──")
        for m, c in ext.most_common(15):
            print(f"   {m:16s} ×{c}")
    print()


def main():
    p = argparse.ArgumentParser(prog="code_map")
    p.add_argument("cmd", nargs="?", default="list", choices=["list", "workspace", "index"])
    p.add_argument("paths", nargs="*")
    p.add_argument("--external", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    if a.cmd == "workspace" or len(a.paths) > 1:
        data = build_workspace_map(a.paths or ["."], include_external_nodes=a.external)
        print(json.dumps(data, ensure_ascii=False, indent=2) if a.json else print_workspace(data))
    else:
        data = build_project_map(Path(a.paths[0]) if a.paths else Path("."))
        if a.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print_map(data)


if __name__ == "__main__":
    main()
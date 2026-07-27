# app.py — CodeForge: один сервер над ядром (code_map + code_blocks)
# Запуск:  python app.py        →  http://127.0.0.1:8002
# code_map.py и code_blocks.py должны лежать рядом.
import json, threading
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import code_map as cm
import code_blocks as cb

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"; DATA.mkdir(exist_ok=True)
PROJECTS_FILE = HERE / "projects.json"

app = FastAPI(title="CodeForge")

# CORS — для открытия фронтенда с других origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── иконка (4 блока-лего, один коралловый = «где болит») ───────────────────
_FAVICON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='7' fill='#0b1622'/>"
    "<rect x='6' y='6' width='9' height='9' rx='2' fill='#4dd8e6'/>"
    "<rect x='17' y='6' width='9' height='9' rx='2' fill='#4dd8e6' opacity='.5'/>"
    "<rect x='6' y='17' width='9' height='9' rx='2' fill='#4dd8e6' opacity='.5'/>"
    "<rect x='17' y='17' width='9' height='9' rx='2' fill='#ff6b5e'/></svg>"
)
@app.get("/favicon.svg")
async def favicon_svg():
    return Response(content=_FAVICON, media_type="image/svg+xml")
@app.get("/favicon.ico")
async def favicon_ico():
    return Response(content=_FAVICON, media_type="image/svg+xml")

# ── реестр проектов ────────────────────────────────────────────────────────
_lock = threading.Lock()

def _load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        return {"active": "", "projects": []}
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"active": "", "projects": []}

def _save_projects(reg: dict):
    PROJECTS_FILE.write_text(json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8")

def _rel_file(root: Path, file: str) -> str:
    """code_map.py в build_project_map добавляет rootname/ к path — убираем."""
    prefix = root.name + "/"
    if file.startswith(prefix):
        return file[len(prefix):]
    return file

def _proj(name: str):
    return next((p for p in _load_projects()["projects"] if p["name"] == name), None)

# ── кэш карт (инвалидируется после правок) ─────────────────────────────────
_cache: dict = {}

def _simple(qual: str) -> str:
    return qual.split(".")[-1]

def _build(name: str) -> dict:
    with _lock:
        if name in _cache:
            return _cache[name]
    p = _proj(name)

    def _empty(err):
        return {
            "map": {"files": [], "graph": {"nodes": [], "edges": []},
                    "stats": {"files": 0, "total_lines": 0, "blocks": 0,
                              "methods": 0, "top_calls": [], "by_type": {}, "cross_root": 0}},
            "extra": {"defs": {}, "defs_names": set(), "caller_map": {}, "blocks_by_qual": {}},
            "root": str(p["path"]) if p else "", "_error": err,
        }

    if not p:
        res = _empty(f"проект '{name}' не найден в реестре")
        with _lock: _cache[name] = res
        return res
    root = Path(p["path"])
    if not root.is_dir():
        res = _empty(f"путь не существует: {root}")
        with _lock: _cache[name] = res
        return res
    try:
        m = cm.build_project_map(root)
    except Exception as e:
        import traceback; traceback.print_exc()
        res = _empty(f"ошибка парсинга: {e}")
        with _lock: _cache[name] = res
        return res

    defs, blocks_by_qual, caller_map = {}, {}, {}
    for f in m["files"]:
        fp = f["path"]
        for b in f.get("blocks", []):
            qual = b["name"]
            defs.setdefault(qual, []).append((fp, qual, b["type"], b.get("lines", 0)))
            blocks_by_qual[(fp, qual)] = b
            for c in b.get("calls", []):
                caller_map.setdefault(c, []).append((fp, qual))
            if b["type"] == "class":
                for meth in b.get("methods", []):
                    mqual = f"{b['name']}.{meth['name']}"
                    defs.setdefault(meth["name"], []).append((fp, mqual, "method", meth.get("lines", 0)))
                    blocks_by_qual[(fp, mqual)] = meth
                    for c in meth.get("calls", []):
                        caller_map.setdefault(c, []).append((fp, mqual))
    res = {"map": m,
           "extra": {"defs": defs, "defs_names": set(defs), "caller_map": caller_map,
                     "blocks_by_qual": blocks_by_qual},
           "root": str(root)}
    with _lock: _cache[name] = res
    return res

def _invalidate(name: str):
    with _lock: _cache.pop(name, None)

# ── аннотации (живут в data/, НЕ в чужом проекте) ──────────────────────────
def _ann_file(name: str) -> Path:
    p = _proj(name) or {}
    return DATA / (p.get("ann") or f"{name}.annotations.json")

def _load_ann(name: str) -> dict:
    f = _ann_file(name)
    if not f.exists():
        return {}
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
        return {k: (v.get("what", "") if isinstance(v, dict) else v) for k, v in raw.items()}
    except Exception:
        return {}

def _save_ann(name: str, ann: dict):
    _ann_file(name).write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")

def _ann_for(ann: dict, file: str, qual: str) -> str:
    return ann.get(f"{file}:{qual}") or ann.get(qual) or ""

# ── API: реестр ────────────────────────────────────────────────────────────
@app.get("/api/meta")
async def meta():
    reg = _load_projects()
    n_bk = len(list(cb.BACKUP_DIR.glob("*.bak"))) if cb.BACKUP_DIR.exists() else 0
    return {"active": reg.get("active", ""),
            "projects": [p["name"] for p in reg["projects"]],
            "backups": n_bk}

@app.get("/api/projects")
async def projects():
    return _load_projects()

@app.post("/api/projects")
async def add_project(req: Request):
    b = await req.json()
    name = (b.get("name") or "").strip()
    path = (b.get("path") or "").strip()
    ann = (b.get("ann") or "").strip()
    if not name or not path:
        return JSONResponse({"ok": False, "error": "нужны name и path"}, status_code=400)
    reg = _load_projects()
    if any(p["name"] == name for p in reg["projects"]):
        return JSONResponse({"ok": False, "error": f"проект '{name}' уже есть"}, status_code=400)
    entry = {"name": name, "path": path}
    if ann:
        entry["ann"] = ann
    reg["projects"].append(entry)
    if not reg.get("active"):
        reg["active"] = name
    _save_projects(reg)
    return {"ok": True}

@app.post("/api/projects/delete")
async def del_project(req: Request):
    b = await req.json()
    name = (b.get("name") or "").strip()
    reg = _load_projects()
    reg["projects"] = [p for p in reg["projects"] if p["name"] != name]
    if reg.get("active") == name:
        reg["active"] = reg["projects"][0]["name"] if reg["projects"] else ""
    _save_projects(reg)
    _invalidate(name)
    return {"ok": True}

@app.post("/api/projects/active")
async def set_active(req: Request):
    b = await req.json()
    name = (b.get("name") or "").strip()
    reg = _load_projects()
    if not _proj(name):
        return JSONResponse({"ok": False, "error": "нет такого проекта"}, status_code=400)
    reg["active"] = name
    _save_projects(reg)
    return {"ok": True}

@app.post("/api/refresh")
async def refresh(req: Request):
    b = await req.json()
    _invalidate(b.get("name", ""))
    return {"ok": True}

# ── API: карта / граф ──────────────────────────────────────────────────────
@app.get("/api/files")
async def files(project: str):
    c = _build(project)
    m = c["map"]
    for f in m["files"]:
        f.pop("full_path", None)
    out = {"root": c["root"], "files": m["files"], "stats": m["stats"]}
    if c.get("_error"):
        out["error"] = c["_error"]
    return out

@app.get("/api/graph")
async def graph(project: str):
    return _build(project)["map"]["graph"]

# ── API: блок (код + мета + аннотация + связи) ─────────────────────────────
@app.get("/api/block")
async def block(project: str, file: str, qual: str):
    c = _build(project)
    if "error" in c["map"] or c.get("_error"):
        return JSONResponse({"error": c.get("_error") or "проект недоступен"}, status_code=404)
    root = Path(c["root"])
    file = _rel_file(root, file)
    try:
        data = cb.get_block(root / file, qual)
    except FileNotFoundError:
        return JSONResponse({"error": f"файл не найден: {file}"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": f"ошибка чтения файла: {e}"}, status_code=500)
    if "error" in data:
        return JSONResponse(data, status_code=404)
    ex = c["extra"]
    # ИСПРАВЛЕНО: сравниваем _simple(x) с defs_names
    calls_internal = [x for x in data.get("calls_raw", []) if _simple(x) in ex["defs_names"]]
    seen, called_by = set(), []
    for fp, cq in ex["caller_map"].get(_simple(qual), []):
        k = (fp, cq)
        if k not in seen and not (fp == file and cq == qual):
            seen.add(k); called_by.append({"file": fp, "qual": cq})
    data["calls_internal"] = calls_internal
    data["called_by"] = called_by
    data["ann"] = _ann_for(_load_ann(project), file, qual)
    return data

# ── API: правки (атомарные, AST-safe) ──────────────────────────────────────
@app.post("/api/replace")
async def replace(req: Request):
    b = await req.json()
    p = _proj(b["project"])
    if not p:
        return JSONResponse({"ok": False, "error": "проект не найден"}, status_code=404)
    file = _rel_file(Path(p["path"]), b["file"])
    try:
        r = cb.apply_replace(Path(p["path"]) / file, b["qual"], b["code"])
    except FileNotFoundError:
        return {"ok": False, "error": f"файл не найден: {b['file']}"}
    if r.get("ok"): _invalidate(b["project"])
    return r

@app.post("/api/insert")
async def insert(req: Request):
    b = await req.json()
    p = _proj(b["project"])
    if not p:
        return JSONResponse({"ok": False, "error": "проект не найден"}, status_code=404)
    file = _rel_file(Path(p["path"]), b["file"])
    try:
        r = cb.apply_insert(Path(p["path"]) / file, b["after"], b["code"])
    except FileNotFoundError:
        return {"ok": False, "error": f"файл не найден: {b['file']}"}
    if r.get("ok"): _invalidate(b["project"])
    return r

@app.post("/api/delete")
async def delete(req: Request):
    b = await req.json()
    p = _proj(b["project"])
    if not p:
        return JSONResponse({"ok": False, "error": "проект не найден"}, status_code=404)
    file = _rel_file(Path(p["path"]), b["file"])
    try:
        r = cb.apply_delete(Path(p["path"]) / file, b["qual"])
    except FileNotFoundError:
        return {"ok": False, "error": f"файл не найден: {b['file']}"}
    if r.get("ok"): _invalidate(b["project"])
    return r

@app.post("/api/validate")
async def validate(req: Request):
    b = await req.json()
    return cb.validate_code(b["code"])

@app.get("/api/backups")
async def backups(project: str, file: str):
    return cb.list_backups(Path(_proj(project)["path"]) / file)

@app.post("/api/restore")
async def restore(req: Request):
    b = await req.json()
    r = cb.restore(Path(_proj(b["project"])["path"]) / b["file"], b.get("backup"))
    if r.get("ok"): _invalidate(b["project"])
    return r

# ── API: аннотации ─────────────────────────────────────────────────────────
@app.get("/api/annotations")
async def get_annotations(project: str):
    return _load_ann(project)

@app.post("/api/annotation")
async def set_annotation(req: Request):
    b = await req.json()
    project, file, qual = b["project"], b["file"], b["qual"]
    p = _proj(project)
    if p:
        file = _rel_file(Path(p["path"]), file)
    text = (b.get("text") or "").strip()
    ann = _load_ann(project)
    key = f"{file}:{qual}"
    if text:
        ann[key] = text
    else:
        ann.pop(key, None); ann.pop(qual, None)
    _save_ann(project, ann)
    return {"ok": True, "count": sum(1 for v in ann.values() if v)}


# ── API: глобальный поиск по коду ──────────────────────────────────────────
@app.get("/api/search")
async def search_code(project: str, q: str):
    c = _build(project)
    if "error" in c["map"] or c.get("_error"):
        return JSONResponse({"error": c.get("_error") or "проект недоступен"}, status_code=404)
    root = Path(c["root"])
    query = q.lower()
    qwords = query.split()
    ann = _load_ann(project)
    results = []
    seen_files = set()

    # ИСПРАВЛЕНО: используем rel_fpath для ключей аннотаций
    for f in c["map"]["files"]:
        fpath = f["path"]
        rel_fpath = _rel_file(root, fpath)
        blocks = f.get("blocks", [])
        file_hits = []
        for b in blocks:
            hit = False
            hl_fields = []
            # по имени блока
            if query in b.get("qual", "").lower() or query in b.get("name", "").lower():
                hit = True
                hl_fields.append("имя блока")
            # по docstring
            doc = (b.get("doc", "") or "").lower()
            if query in doc:
                hit = True
                hl_fields.append("docstring")
            # по pre_ann из кода
            pre = (b.get("pre_ann", "") or "").lower()
            if query in pre:
                hit = True
                hl_fields.append("# ANN:")
            # по аннотации из словаря — ИСПРАВЛЕНО: rel_fpath
            akey = f"{rel_fpath}:{b.get('qual', '')}"
            ann_text = (ann.get(akey) or ann.get(b.get("qual", "")) or "").lower()
            if query in ann_text:
                hit = True
                hl_fields.append("словарь")
            if hit:
                file_hits.append({
                    "line": b["start"],
                    "text": b.get("qual", b["name"]),
                    "block": b["qual"],
                    "fields": hl_fields,
                })
            # методы класса
            if b.get("type") == "class":
                for m in b.get("methods", []):
                    m_hit = False
                    m_fields = []
                    if query in m.get("qual", "").lower() or query in m.get("name", "").lower():
                        m_hit = True; m_fields.append("имя метода")
                    mdoc = (m.get("doc", "") or "").lower()
                    if query in mdoc:
                        m_hit = True; m_fields.append("docstring")
                    mpre = (m.get("pre_ann", "") or "").lower()
                    if query in mpre:
                        m_hit = True; m_fields.append("# ANN:")
                    # ИСПРАВЛЕНО: rel_fpath
                    makey = f"{rel_fpath}:{m.get('qual', '')}"
                    mann = (ann.get(makey) or ann.get(m.get("qual", "")) or "").lower()
                    if query in mann:
                        m_hit = True; m_fields.append("словарь")
                    if m_hit:
                        file_hits.append({
                            "line": m["start"],
                            "text": m.get("qual", m["name"]),
                            "block": m["qual"],
                            "fields": m_fields,
                        })
        if file_hits:
            seen_files.add(fpath)
            results.append({"file": fpath, "matches": file_hits[:20]})

    # 2. Поиск по содержимому файлов (как раньше, но только в файлах без hits)
    for f in c["map"]["files"]:
        fpath = f["path"]
        if fpath in seen_files:
            continue
        rel = _rel_file(root, fpath)
        fp = root / rel
        if not fp.exists():
            continue
        try:
            src = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lines = src.splitlines()
        matches = []
        for i, line in enumerate(lines, 1):
            if query in line.lower():
                ctx = line.strip()
                matches.append({"line": i, "text": ctx})
        if matches:
            blocks = f.get("blocks", [])
            for m in matches:
                blk = None
                for b in blocks:
                    if b["start"] <= m["line"] <= b["end"]:
                        blk = b["qual"]
                        break
                m["block"] = blk
            results.append({"file": fpath, "matches": matches[:20]})

    # сортируем: сначала hits по блокам, потом по содержимому
    def sort_key(r):
        has_block = any(m.get("block") for m in r.get("matches", []))
        has_fields = any(m.get("fields") for m in r.get("matches", []))
        return (0 if has_fields else (1 if has_block else 2), r["file"])

    results.sort(key=sort_key)
    return {"query": q, "results": results[:50], "total_files": len(results)}


# ── API: экспорт карты в JSON ──────────────────────────────────────────────
@app.get("/api/export")
async def export_project(project: str):
    c = _build(project)
    if "error" in c["map"] or c.get("_error"):
        return JSONResponse({"error": c.get("_error") or "проект недоступен"}, status_code=404)
    ann = _load_ann(project)
    out = {
        "project": project,
        "root": c["root"],
        "exported_at": __import__('datetime').datetime.now().isoformat(),
        "stats": c["map"]["stats"],
        "graph": c["map"]["graph"],
        "files": []
    }
    for f in c["map"]["files"]:
        fcopy = {k: v for k, v in f.items() if k != "full_path"}
        for b in fcopy.get("blocks", []):
            b["ann"] = _ann_for(ann, fcopy["path"], b["qual"])
            b["pre_ann"] = b.get("pre_ann", "")
            if b["type"] == "class":
                for m in b.get("methods", []):
                    m["ann"] = _ann_for(ann, fcopy["path"], m["qual"])
                    m["pre_ann"] = m.get("pre_ann", "")
        out["files"].append(fcopy)
    return out

# ── UI ────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((HERE / "index.html").read_text(encoding="utf-8"))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"CodeForge → http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, reload=False)
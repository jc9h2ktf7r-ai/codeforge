# render_map.py — визуализация карты проекта (полная версия, замена целиком)
# python render_map.py D:\synochek\synochek_v2\volk
# python render_map.py volk_map.json
import sys, json, os
from pathlib import Path
from collections import defaultdict

SCRIPT_DIR = Path(__file__).parent


def load_data(arg: str):
    p = Path(arg)
    if p.is_dir():
        sys.path.insert(0, str(SCRIPT_DIR))
        import code_map
        return code_map.build_project_map(p), p
    else:
        return json.loads(p.read_text(encoding="utf-8")), p.parent


def load_annotations(project_dir: Path) -> dict:
    for cand in (SCRIPT_DIR / "annotations.json", project_dir / "annotations.json"):
        if cand.exists():
            raw = json.loads(cand.read_text(encoding="utf-8"))
            return {k: (v.get("what", "") if isinstance(v, dict) else v) for k, v in raw.items()}
    return {}


def enrich(data: dict, ann: dict):
    files = data["files"]
    defs = defaultdict(list)
    called_by = defaultdict(list)
    allblocks = []
    for f in files:
        fp = f["path"]
        for b in f.get("blocks", []):
            b["ann"] = ann.get(f"{fp}:{b['name']}") or ann.get(b["name"]) or ""
            b.setdefault("doc", "")
            defs[b["name"]].append({"file": fp, "type": b["type"], "lines": b["lines"]})
            allblocks.append({"file": fp, "name": b["name"], "lines": b["lines"], "type": b["type"]})
            if b["type"] == "class":
                for m in b.get("methods", []):
                    mkey = f"{b['name']}.{m['name']}"
                    m["ann"] = ann.get(f"{fp}:{mkey}") or ann.get(m["name"]) or ""
                    m.setdefault("doc", "")
                    defs[mkey].append({"file": fp, "type": "method", "lines": m["lines"]})
    for f in files:
        fp = f["path"]
        for b in f.get("blocks", []):
            for c in b.get("calls", []):
                if c in defs:
                    called_by[c].append({"file": fp, "caller": b["name"]})
            if b["type"] == "class":
                for m in b.get("methods", []):
                    for c in m.get("calls", []):
                        if c in defs:
                            called_by[c].append({"file": fp, "caller": f"{b['name']}.{m['name']}"})
    for k in called_by:
        seen, uniq = set(), []
        for e in called_by[k]:
            key = (e["file"], e["caller"])
            if key not in seen:
                seen.add(key); uniq.append(e)
        called_by[k] = uniq
    data["defs"] = defs
    data["called_by"] = called_by
    data["top_blocks"] = sorted(allblocks, key=lambda x: -x["lines"])[:10]
    return data


HTML = r"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Я знаю свой проект</title>
<link href="https://fonts.googleapis.com/css2?family=Unbounded:wght@500;700;900&family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg0:#081019; --bg1:#0c1826; --bg2:#122233; --line:#1d3a52;
  --ink:#dce9f2; --dim:#7d97ab;
  --cyan:#4dd8e6; --amber:#ffb454; --coral:#ff6b5e; --green:#7ee081; --violet:#b48cff;
}
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%}
body{
  font-family:'IBM Plex Sans',system-ui,sans-serif; color:var(--ink);
  background:
    radial-gradient(900px 600px at 8% -10%, rgba(77,216,230,.08), transparent 60%),
    radial-gradient(800px 600px at 100% 110%, rgba(255,180,84,.06), transparent 60%),
    linear-gradient(rgba(77,216,230,.045) 1px, transparent 1px),
    linear-gradient(90deg, rgba(77,216,230,.045) 1px, transparent 1px),
    var(--bg0);
  background-size:auto,auto,44px 44px,44px 44px,auto;
  overflow:hidden;
}
body::after{
  content:""; position:fixed; left:0; right:0; height:220px; top:-240px;
  background:linear-gradient(180deg, transparent, rgba(77,216,230,.05), transparent);
  animation:scan 14s linear infinite; pointer-events:none;
}
@keyframes scan{to{transform:translateY(140vh)}}
.mono{font-family:'JetBrains Mono',monospace}
.disp{font-family:'Unbounded',sans-serif}
header{
  height:64px; display:flex; align-items:center; gap:28px; padding:0 22px;
  border-bottom:1px solid var(--line); background:linear-gradient(180deg,var(--bg2),var(--bg1));
  position:relative; z-index:5;
}
.brand{display:flex;align-items:center;gap:12px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--green);box-shadow:0 0 10px var(--green);animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.35}}
.brand h1{font-size:15px;font-weight:900;letter-spacing:.12em}
.brand small{display:block;font-size:9px;letter-spacing:.3em;color:var(--dim);font-weight:500}
.stats{display:flex;gap:26px;margin-left:auto}
.stat{text-align:right}
.stat b{display:block;font-family:'JetBrains Mono',monospace;font-size:19px;font-weight:700;color:var(--cyan)}
.stat span{font-size:9px;letter-spacing:.22em;color:var(--dim)}
.stat.hot b{color:var(--amber)}
#q{
  width:230px;background:var(--bg0);border:1px solid var(--line);color:var(--ink);
  padding:9px 13px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:12px;outline:none;transition:.2s;
}
#q:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(77,216,230,.12)}
main{display:grid;grid-template-columns:340px 1fr;height:calc(100% - 64px)}
aside{border-right:1px solid var(--line);overflow-y:auto;background:rgba(12,24,38,.7);padding:10px 0 40px}
.right{display:grid;grid-template-rows:minmax(240px,46%) 1fr;overflow:hidden}
#details{overflow-y:auto;padding:22px 26px;border-bottom:1px solid var(--line)}
#graphwrap{position:relative;overflow:hidden}
#graph{width:100%;height:100%;display:block;cursor:grab}
#graph:active{cursor:grabbing}
.file{margin:2px 8px;border-radius:6px;overflow:hidden}
.fhead{
  display:flex;align-items:center;gap:9px;padding:8px 10px;cursor:pointer;border-radius:6px;
  transition:background .15s, transform .15s; border-left:3px solid transparent;
}
.fhead:hover{background:var(--bg2);transform:translateX(2px)}
.file.open>.fhead{border-left-color:var(--cyan)}
.sz{width:8px;height:8px;border-radius:2px;flex:none}
.sz.r{background:var(--coral);box-shadow:0 0 7px var(--coral)}
.sz.y{background:var(--amber);box-shadow:0 0 7px var(--amber)}
.sz.g{background:var(--green)}
.fname{font-family:'JetBrains Mono',monospace;font-size:12.5px;font-weight:500;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.fmeta{font-family:'JetBrains Mono',monospace;font-size:10px;color:var(--dim)}
.caret{color:var(--dim);font-size:10px;transition:transform .2s}
.file.open .caret{transform:rotate(90deg)}
.kids{max-height:0;overflow:hidden;transition:max-height .3s ease}
.file.open .kids{max-height:4000px}
.blk{
  display:flex;align-items:center;gap:8px;padding:5px 10px 5px 26px;cursor:pointer;position:relative;
  transition:background .12s;
}
.blk:hover{background:rgba(77,216,230,.07)}
.blk.sel{background:rgba(77,216,230,.13)}
.blk.sel::before{content:"";position:absolute;left:14px;top:6px;bottom:6px;width:2px;background:var(--cyan)}
.glyph{font-family:'JetBrains Mono',monospace;font-size:11px;color:var(--cyan);width:14px;flex:none}
.glyph.c{color:var(--violet)} .glyph.m{color:var(--dim)}
.bname{font-family:'JetBrains Mono',monospace;font-size:11.5px;flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bname.hot{color:var(--coral)} .bname.warm{color:var(--amber)}
.bbar{width:44px;height:3px;background:var(--bg0);border-radius:2px;overflow:hidden;flex:none}
.bbar i{display:block;height:100%;background:linear-gradient(90deg,var(--cyan),var(--violet))}
.bln{font-family:'JetBrains Mono',monospace;font-size:9.5px;color:var(--dim);width:34px;text-align:right;flex:none}
.hide{display:none!important}
#details .inner{animation:rise .3s ease both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.crumb{font-size:10px;letter-spacing:.2em;color:var(--dim);text-transform:uppercase;margin-bottom:8px}
.btitle{font-family:'JetBrains Mono',monospace;font-size:24px;font-weight:700;color:var(--ink)}
.btitle .par{color:var(--dim);font-weight:400;font-size:16px}
.badges{display:flex;gap:8px;margin:10px 0 16px;flex-wrap:wrap}
.badge{font-family:'JetBrains Mono',monospace;font-size:10px;padding:3px 9px;border-radius:4px;border:1px solid var(--line);color:var(--dim)}
.badge.t{color:var(--violet);border-color:rgba(180,140,255,.4)}
.badge.big{color:var(--coral);border-color:rgba(255,107,94,.4)}
.annbox{
  border:1px solid var(--line);border-left:3px solid var(--amber);background:var(--bg1);
  padding:14px 16px;border-radius:6px;font-size:13.5px;line-height:1.55;margin-bottom:18px;
}
.annbox .src{display:block;font-size:9px;letter-spacing:.25em;color:var(--amber);margin-bottom:6px;text-transform:uppercase}
.annbox.doc{border-left-color:var(--cyan)} .annbox.doc .src{color:var(--cyan)}
.annbox.none{border-left-color:var(--line);color:var(--dim);font-style:italic}
.sect{font-size:10px;letter-spacing:.25em;color:var(--dim);text-transform:uppercase;margin:16px 0 8px}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{
  font-family:'JetBrains Mono',monospace;font-size:11px;padding:5px 11px;border-radius:5px;cursor:pointer;
  border:1px solid rgba(77,216,230,.35);color:var(--cyan);background:rgba(77,216,230,.06);transition:.15s;
}
.chip:hover{transform:translateY(-2px);background:rgba(77,216,230,.16);box-shadow:0 4px 12px rgba(0,0,0,.3)}
.chip.in{border-color:rgba(255,180,84,.4);color:var(--amber);background:rgba(255,180,84,.06)}
.chip.in:hover{background:rgba(255,180,84,.16)}
.chip.lz{border-color:rgba(180,140,255,.4);color:var(--violet);background:rgba(180,140,255,.06)}
.chip .f{color:var(--dim);font-size:9.5px}
.none{color:var(--dim);font-size:12px;font-style:italic}
.ov{display:grid;grid-template-columns:1fr 1fr;gap:26px}
.ov h3{font-family:'Unbounded',sans-serif;font-size:12px;font-weight:700;letter-spacing:.08em;margin-bottom:14px;color:var(--ink)}
.hrow{display:flex;align-items:center;gap:10px;margin-bottom:9px;cursor:pointer}
.hrow:hover .hname{color:var(--cyan)}
.hname{font-family:'JetBrains Mono',monospace;font-size:11.5px;width:190px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;transition:color .15s}
.hbar{flex:1;height:8px;background:var(--bg0);border-radius:4px;overflow:hidden}
.hbar i{display:block;height:100%;background:linear-gradient(90deg,var(--amber),var(--coral));border-radius:4px;animation:grow .8s ease both}
@keyframes grow{from{width:0!important}}
.hval{font-family:'JetBrains Mono',monospace;font-size:10.5px;color:var(--dim);width:44px;text-align:right}
.legend{position:absolute;left:16px;bottom:14px;display:flex;gap:16px;font-size:10px;color:var(--dim);background:rgba(8,16,25,.75);padding:8px 14px;border-radius:6px;border:1px solid var(--line)}
.legend i{display:inline-block;width:18px;height:0;border-top:2px solid var(--cyan);vertical-align:middle;margin-right:6px}
.legend i.dash{border-top-style:dashed;border-top-color:var(--violet)}
.legend i.dot{border-top-style:dotted;border-top-color:var(--amber)}
.zctl{position:absolute;right:16px;top:14px;display:flex;flex-direction:column;gap:6px;z-index:9}
.zctl button{width:34px;height:34px;border-radius:6px;border:1px solid var(--line);background:var(--bg2);
  color:var(--cyan);font-size:16px;line-height:1;cursor:pointer;font-family:'JetBrains Mono',monospace;transition:.15s}
.zctl button:hover{background:rgba(77,216,230,.16);transform:translateY(-1px);box-shadow:0 4px 12px rgba(0,0,0,.35)}
#graph.zoomed-out .gnode text{opacity:0}
.gnode text{transition:opacity .2s}
.gnode circle{cursor:pointer;transition:stroke-width .15s}
.gnode text{font-family:'JetBrains Mono',monospace;font-size:9px;fill:var(--dim);pointer-events:none}
.gnode:hover circle{stroke:#fff;stroke-width:2}
.gedge{fill:none;stroke-opacity:.5;transition:stroke-opacity .15s}
.gedge.import{stroke:var(--cyan);stroke-width:1.4}
.gedge.lazy_import{stroke:var(--violet);stroke-width:1.2;stroke-dasharray:5 4}
.gedge.calls{stroke:var(--amber);stroke-width:1.1;stroke-dasharray:1.5 3.5}
#graph.dim .gedge{stroke-opacity:.07}
#graph.dim .gnode{opacity:.2}
#graph.dim .gedge.hl{stroke-opacity:.95;animation:flow 1s linear infinite}
#graph.dim .gnode.hl{opacity:1}
@keyframes flow{to{stroke-dashoffset:-20}}
.gtip{position:absolute;pointer-events:none;background:var(--bg2);border:1px solid var(--line);padding:7px 11px;border-radius:6px;font-family:'JetBrains Mono',monospace;font-size:11px;display:none;z-index:9}
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="dot"></span>
    <h1 class="disp">Я ЗНАЮ СВОЙ ПРОЕКТ<small>CODE&nbsp;MAP&nbsp;/&nbsp;ЛЕГО-БЛОКИ</small></h1>
  </div>
  <div class="stats">
    <div class="stat"><b id="s-files">0</b><span>ФАЙЛОВ</span></div>
    <div class="stat hot"><b id="s-lines">0</b><span>СТРОК</span></div>
    <div class="stat"><b id="s-blocks">0</b><span>БЛОКОВ</span></div>
    <div class="stat"><b id="s-edges">0</b><span>СВЯЗЕЙ</span></div>
  </div>
  <input id="q" class="mono" placeholder="поиск: run_walker, tech…">
</header>
<main>
  <aside id="tree"></aside>
  <div class="right">
    <div id="details"></div>
    <div id="graphwrap">
      <svg id="graph"></svg>
      <div class="legend">
        <span><i></i>import</span><span><i class="dash"></i>lazy</span><span><i class="dot"></i>calls</span>
      </div>
      <div class="zctl">
        <button id="zin"  title="Приблизить">+</button>
        <button id="zout" title="Отдалить">−</button>
        <button id="zfit" title="Вписать граф">⤢</button>
      </div>
      <div class="gtip" id="gtip"></div>
    </div>
  </div>
</main>
<script>
const D = __DATA__;
const $ = s=>document.querySelector(s);
const blockIndex = {};
D.files.forEach(f=>(f.blocks||[]).forEach(b=>{
  blockIndex[f.path+"::"+b.name]={file:f.path,block:b};
  if(b.type==="class")(b.methods||[]).forEach(m=>{
    blockIndex[f.path+"::"+b.name+"."+m.name]={file:f.path,block:m,cls:b.name};
  });
}));
const fileLines={}; D.files.forEach(f=>fileLines[f.path]=f.lines);

function countUp(el,val){let s=0,st=Math.max(1,Math.floor(val/40));
  const t=setInterval(()=>{s+=st;if(s>=val){s=val;clearInterval(t)}el.textContent=s.toLocaleString('ru')},20);}
countUp($('#s-files'),D.stats.files); countUp($('#s-lines'),D.stats.total_lines);
countUp($('#s-blocks'),D.stats.blocks); countUp($('#s-edges'),D.graph.edges.length);

const sizeCls=n=>n>1000?'r':n>300?'y':'g';
const nameCls=n=>n>200?'hot':n>50?'warm':'';

function renderTree(){
  const box=$('#tree'); box.innerHTML='';
  D.files.forEach((f,fi)=>{
    const d=document.createElement('div'); d.className='file'+(fi<3?' open':''); d.dataset.file=f.path;
    const blocks=f.blocks||[];
    let kids='';
    blocks.forEach(b=>{
      const g=b.type==='class'?'⌘':(b.type==='async_function'?'⚡':'ƒ');
      const gc=b.type==='class'?' c':'';
      kids+=`<div class="blk" data-file="${f.path}" data-name="${b.name}">
        <span class="glyph${gc}">${g}</span><span class="bname ${nameCls(b.lines)}">${b.name}</span>
        <span class="bbar"><i style="width:${Math.min(100,b.lines/f.lines*100)}%"></i></span>
        <span class="bln">${b.lines}</span></div>`;
      if(b.type==='class')(b.methods||[]).forEach(m=>{
        kids+=`<div class="blk" data-file="${f.path}" data-name="${b.name}.${m.name}" style="padding-left:44px">
          <span class="glyph m">·</span><span class="bname ${nameCls(m.lines)}">${m.name}</span>
          <span class="bbar"><i style="width:${Math.min(100,m.lines/f.lines*100)}%"></i></span>
          <span class="bln">${m.lines}</span></div>`;
      });
    });
    d.innerHTML=`<div class="fhead"><span class="caret">▶</span><span class="sz ${sizeCls(f.lines)}"></span>
      <span class="fname">${f.path}</span><span class="fmeta">${f.lines}·${blocks.length}</span></div>
      <div class="kids">${kids}</div>`;
    d.querySelector('.fhead').onclick=()=>d.classList.toggle('open');
    box.appendChild(d);
  });
  box.querySelectorAll('.blk').forEach(el=>el.onclick=()=>selectBlock(el.dataset.file,el.dataset.name));
}

function chip(name,file,cls){
  return `<span class="chip ${cls}" data-file="${file||''}" data-name="${name}">${name}${file?` <span class="f">${file}</span>`:''}</span>`;
}
function bindChips(root){
  root.querySelectorAll('.chip').forEach(c=>c.onclick=()=>{
    let f=c.dataset.name, file=c.dataset.file;
    if(f.includes(' ')){const m=f.match(/^(.+?)\(\)\s*\[(.+)\]$/); if(m){f=m[1];file=m[2];}}
    jumpTo(file,f);
  });
}
function annBox(b){
  if(b.ann) return `<div class="annbox"><span class="src">из словаря</span>${b.ann}</div>`;
  if(b.doc) return `<div class="annbox doc"><span class="src">docstring</span>${b.doc.replace(/\n/g,'<br>')}</div>`;
  return `<div class="annbox none">нет описания — добавь в annotations.json ключ «${b.name}»</div>`;
}
function renderDetails(file,b){
  const el=$('#details');
  const callsProj=(b.calls||[]).filter(c=>D.defs[c]);
  const callers=D.called_by[b.name]||[];
  const lazy=b.lazy_imports||[];
  el.innerHTML=`<div class="inner">
    <div class="crumb">${file} · строки ${b.start}–${b.end}</div>
    <div class="btitle">${b.name}<span class="par">${b.args&&b.args.length?'('+b.args.slice(0,5).join(', ')+(b.args.length>5?', …':'')+')':''}</span></div>
    <div class="badges">
      <span class="badge t">${b.type}</span>
      <span class="badge ${b.lines>200?'big':''}">${b.lines} строк</span>
      ${(b.decorators||[]).map(d=>`<span class="badge">@${d}</span>`).join('')}
    </div>
    ${annBox(b)}
    <div class="sect">вызывает → (${callsProj.length})</div>
    <div class="chips">${callsProj.map(c=>{const d=D.defs[c][0];return chip(c,d?d.file:'','')}).join('')||'<span class="none">только встроенные</span>'}</div>
    <div class="sect">← вызывают (${callers.length})</div>
    <div class="chips">${callers.map(c=>chip(c.caller+'() ['+c.file+']','','in')).join('')||'<span class="none">никто — точка входа</span>'}</div>
    ${lazy.length?`<div class="sect">lazy-импорты</div><div class="chips">${lazy.map(l=>`<span class="chip lz">from ${l.module} import ${(l.names||[]).join(', ')||'…'}</span>`).join('')}</div>`:''}
  </div>`;
  bindChips(el);
  document.querySelectorAll('.blk.sel').forEach(x=>x.classList.remove('sel'));
  const t=document.querySelector(`.blk[data-file="${file}"][data-name="${b.name}"]`);
  if(t){t.classList.add('sel');t.scrollIntoView({block:'nearest'});}
}
function renderOverview(){
  const el=$('#details');
  const top=D.stats.top_calls||[];
  const maxC=top.length?top[0][1]:1;
  const tb=D.top_blocks||[];
  el.innerHTML=`<div class="inner"><div class="ov">
    <div><h3 class="disp">ТОП ВЫЗЫВАЕМЫХ</h3>${top.slice(0,8).map(([n,c])=>
      `<div class="hrow" data-name="${n}"><span class="hname">${n}</span>
       <span class="hbar"><i style="width:${c/maxC*100}%"></i></span><span class="hval">${c}</span></div>`).join('')}</div>
    <div><h3 class="disp">ГДЕ БОЛИТ — КРУПНЕЙШИЕ БЛОКИ</h3>${tb.map(b=>
      `<div class="hrow" data-file="${b.file}" data-name="${b.name}"><span class="hname ${nameCls(b.lines)}">${b.name}</span>
       <span class="hbar"><i style="width:${b.lines/tb[0].lines*100}%"></i></span><span class="hval">${b.lines}</span></div>`).join('')}</div>
  </div></div>`;
  el.querySelectorAll('.hrow').forEach(r=>r.onclick=()=>{
    const n=r.dataset.name,f=r.dataset.file;
    if(f) jumpTo(f,n); else { const d=D.defs[n]&&D.defs[n][0]; if(d) jumpTo(d.file,n); }
  });
}
function selectBlock(file,name){
  const e=blockIndex[file+"::"+name];
  if(e) renderDetails(file,e.block);
}
function jumpTo(file,name){
  if(!file||!name)return;
  const e=blockIndex[file+"::"+name];
  if(!e){ const d=D.defs[name]&&D.defs[name][0]; if(!d)return; file=d.file; }
  const fd=document.querySelector(`.file[data-file="${file}"]`);
  if(fd){fd.classList.add('open');fd.scrollIntoView({block:'nearest'});}
  selectBlock(file,name);
}

$('#q').addEventListener('input',e=>{
  const q=e.target.value.trim().toLowerCase();
  document.querySelectorAll('.file').forEach(fd=>{
    let any=false;
    fd.querySelectorAll('.blk').forEach(b=>{
      const hit=!q||b.dataset.name.toLowerCase().includes(q);
      b.classList.toggle('hide',!hit); if(hit)any=true;
    });
    const fh=fd.querySelector('.fname').textContent.toLowerCase();
    const fhit=!q||fh.includes(q)||any;
    fd.classList.toggle('hide',!fhit);
    if(q&&any)fd.classList.add('open');
  });
});

function initGraph(){
  const svg=$('#graph'), wrap=$('#graphwrap'), tip=$('#gtip');
  // размеры с fallback — чтобы граф не стартовал в нулевом контейнере
  const W=()=>wrap.clientWidth  || (window.innerWidth-360) || 800;
  const H=()=>wrap.clientHeight || (window.innerHeight-320) || 500;
  const N=D.graph.nodes.length||1;
  const R0=Math.max(140, Math.min(W(),H())*0.32);
  // стартовый разброс по кругу + случайный jitter (защита от сингулярности)
  const nodeData=D.graph.nodes.map(n=>typeof n==='object'?n:{id:n,lines:fileLines[n]||100});
  const nodes=nodeData.map((n,i)=>{
    const a=i/N*6.2832;
    return {id:n.id, lines:n.lines||100,
      x:W()/2 + Math.cos(a)*R0 + (Math.random()-0.5)*90,
      y:H()/2 + Math.sin(a)*R0 + (Math.random()-0.5)*90,
      vx:0, vy:0};
  });
  const idx={}; nodes.forEach(n=>idx[n.id]=n);
  const edges=D.graph.edges.filter(e=>idx[e.from]&&idx[e.to]);
  const r=n=>Math.min(26,6+Math.sqrt(n.lines)*0.35);
  const col=n=>n.lines>1000?'var(--coral)':n.lines>300?'var(--amber)':'var(--green)';

  const NS='http://www.w3.org/2000/svg';
  const gRoot=document.createElementNS(NS,'g');
  const gE=document.createElementNS(NS,'g'), gN=document.createElementNS(NS,'g');
  gRoot.appendChild(gE); gRoot.appendChild(gN); svg.appendChild(gRoot);

  const view={x:0,y:0,k:1};
  const applyView=()=>{
    gRoot.setAttribute('transform',`translate(${view.x},${view.y}) scale(${view.k})`);
    svg.classList.toggle('zoomed-out',view.k<0.55);
  };
  const toGraph=(cx,cy)=>{const rc=svg.getBoundingClientRect();
    return{x:(cx-rc.left-view.x)/view.k,y:(cy-rc.top-view.y)/view.k};};
  svg.addEventListener('wheel',ev=>{
    ev.preventDefault();
    const rc=svg.getBoundingClientRect(),mx=ev.clientX-rc.left,my=ev.clientY-rc.top;
    const k2=Math.min(4,Math.max(0.2,view.k*(ev.deltaY<0?1.15:1/1.15)));
    view.x=mx-(mx-view.x)*(k2/view.k); view.y=my-(my-view.y)*(k2/view.k); view.k=k2;
    applyView();
  },{passive:false});
  let panning=null;
  svg.addEventListener('mousedown',ev=>{
    if(ev.target.closest('.gnode'))return;
    panning={mx:ev.clientX,my:ev.clientY,vx:view.x,vy:view.y};
  });
  window.addEventListener('mousemove',ev=>{
    if(!panning)return;
    view.x=panning.vx+(ev.clientX-panning.mx);
    view.y=panning.vy+(ev.clientY-panning.my);
    applyView();
  });
  window.addEventListener('mouseup',()=>{panning=null;});
  svg.addEventListener('dblclick',ev=>{
    const rc=svg.getBoundingClientRect(),mx=ev.clientX-rc.left,my=ev.clientY-rc.top;
    const k2=Math.min(4,view.k*1.6);
    view.x=mx-(mx-view.x)*(k2/view.k); view.y=my-(my-view.y)*(k2/view.k); view.k=k2;
    applyView();
  });
  const zoomBtn=f=>{
    const cx=W()/2,cy=H()/2,k2=Math.min(4,Math.max(0.2,view.k*f));
    view.x=cx-(cx-view.x)*(k2/view.k); view.y=cy-(cy-view.y)*(k2/view.k); view.k=k2;
    applyView();
  };
  function doFit(){
    if(!nodes.length)return;
    let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;
    nodes.forEach(n=>{x0=Math.min(x0,n.x);y0=Math.min(y0,n.y);x1=Math.max(x1,n.x);y1=Math.max(y1,n.y);});
    const k=Math.min(2.5,Math.max(0.2,Math.min((W()-120)/Math.max(1,x1-x0),(H()-120)/Math.max(1,y1-y0))));
    view.k=k; view.x=W()/2-k*(x0+x1)/2; view.y=H()/2-k*(y0+y1)/2; applyView();
  }
  $('#zin').onclick=()=>zoomBtn(1.3);
  $('#zout').onclick=()=>zoomBtn(1/1.3);
  $('#zfit').onclick=doFit;

  edges.forEach(e=>{
    const p=document.createElementNS(NS,'path');
    p.setAttribute('class','gedge '+e.type); p.dataset.from=e.from; p.dataset.to=e.to;
    gE.appendChild(p); e.el=p;
  });
  nodes.forEach(n=>{
    const g=document.createElementNS(NS,'g'); g.setAttribute('class','gnode');
    const c=document.createElementNS(NS,'circle');
    c.setAttribute('r',r(n)); c.setAttribute('fill',col(n)); c.setAttribute('fill-opacity','.85');
    const t=document.createElementNS(NS,'text'); t.textContent=n.id.replace('.py','');
    t.setAttribute('text-anchor','middle'); t.setAttribute('dy',r(n)+13);
    g.appendChild(c); g.appendChild(t); gN.appendChild(g); n.el=g;
    g.addEventListener('mouseenter',()=>{
      svg.classList.add('dim'); g.classList.add('hl');
      edges.forEach(e=>{if(e.from===n.id||e.to===n.id){e.el.classList.add('hl');
        (e.from===n.id?idx[e.to]:idx[e.from]).el.classList.add('hl');}});
      tip.style.display='block'; tip.innerHTML=`<b>${n.id}</b><br>${n.lines} строк`;
    });
    g.addEventListener('mousemove',ev=>{
      const rc=wrap.getBoundingClientRect();
      tip.style.left=(ev.clientX-rc.left+16)+'px'; tip.style.top=(ev.clientY-rc.top+10)+'px';
    });
    g.addEventListener('mouseleave',()=>{svg.classList.remove('dim');
      document.querySelectorAll('.hl').forEach(x=>x.classList.remove('hl')); tip.style.display='none';});
    g.addEventListener('click',()=>{const fd=document.querySelector(`.file[data-file="${n.id}"]`);
      if(fd){fd.classList.add('open');fd.scrollIntoView({block:'center'});}});
    let drag=false;
    g.addEventListener('mousedown',ev=>{drag=true;n.fixed=true;ev.stopPropagation();});
    window.addEventListener('mousemove',ev=>{if(!drag)return;
      const p=toGraph(ev.clientX,ev.clientY); n.x=p.x; n.y=p.y; draw();});
    window.addEventListener('mouseup',()=>{drag=false;n.fixed=false;});
  });
  function draw(){
    edges.forEach(e=>{const a=idx[e.from],b=idx[e.to];
      e.el.setAttribute('d',`M${a.x},${a.y} L${b.x},${b.y}`);});
    nodes.forEach(n=>n.el.setAttribute('transform',`translate(${n.x},${n.y})`));
  }
  let temp=1, fitted=false;
  function tick(){
    for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
      const a=nodes[i],b=nodes[j];
      let dx=b.x-a.x, dy=b.y-a.y, d2=dx*dx+dy*dy;
      // защита от сингулярности: если узлы совпали — толкаем в случайную сторону
      if(d2<0.01){ dx=(Math.random()-0.5)*2; dy=(Math.random()-0.5)*2; d2=dx*dx+dy*dy+0.01; }
      else d2+=0.01;
      const f=3000/d2, inv=f/Math.sqrt(d2); dx*=inv; dy*=inv;
      if(!a.fixed){a.vx-=dx;a.vy-=dy;} if(!b.fixed){b.vx+=dx;b.vy+=dy;}
    }
    edges.forEach(e=>{const a=idx[e.from],b=idx[e.to];
      let dx=b.x-a.x,dy=b.y-a.y,d=Math.sqrt(dx*dx+dy*dy)+0.01;
      const f=(d-120)*0.012; dx*=f/d; dy*=f/d;
      if(!a.fixed){a.vx+=dx;a.vy+=dy;} if(!b.fixed){b.vx-=dx;b.vy-=dy;}});
    nodes.forEach(n=>{if(n.fixed){n.vx=0;n.vy=0;return;}
      n.vx+=(W()/2-n.x)*0.004; n.vy+=(H()/2-n.y)*0.004;
      n.vx*=0.82;n.vy*=0.82; n.x+=n.vx*temp;n.y+=n.vy*temp;
      n.x=Math.max(30,Math.min(W()-30,n.x)); n.y=Math.max(30,Math.min(H()-40,n.y));});
    temp*=0.99; draw();
    if(temp>0.05){ requestAnimationFrame(tick); }
    else if(!fitted){ fitted=true; doFit(); }   // авто-fit когда улеглось
  }
  tick();
}

renderTree(); renderOverview(); initGraph();
</script>
</body>
</html>"""


def main():
    if len(sys.argv) < 2:
        print("python render_map.py <папка_проекта | map.json>"); sys.exit(1)
    data, project_dir = load_data(sys.argv[1])
    ann = load_annotations(project_dir)
    data = enrich(data, ann)
    for f in data["files"]:
        f.pop("full_path", None)
    out = SCRIPT_DIR / "project_map.html"
    html = HTML.replace("__DATA__", json.dumps(data, ensure_ascii=False))
    out.write_text(html, encoding="utf-8")
    print(f"✅ {out}  (аннотаций из словаря: {sum(1 for v in ann.values() if v)})")
    try:
        os.startfile(out)
    except Exception:
        pass


if __name__ == "__main__":
    main()